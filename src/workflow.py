from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .gitlab_client import GitLabAPIError, GitLabClient
from .workspace import OperationContext, OperationLock, OperationWorkspaceError

__all__ = [
    "route_issue_request",
    "run_routed_workflow",
    "build_plan_graph",
    "build_execute_graph",
]

# Recognized agent labels per implementation plan section 3.2. Only these
# labels drive routing; every other label (agent-like or not) is unrelated and
# is never inspected for routing decisions or mutated.
PLAN_TRIGGER = "agent::plan"
ISSUE_APPROVED_TRIGGER = "agent::approved"
IN_PROGRESS_STATE = "agent::in-progress"
NEEDS_REVIEW_STATE = "agent::needs-review"
EXECUTE_TRIGGER = "agent::implement"

ISSUE_AGENT_STATES = frozenset(
    {
        PLAN_TRIGGER,
        IN_PROGRESS_STATE,
        NEEDS_REVIEW_STATE,
        ISSUE_APPROVED_TRIGGER,
    }
)
MR_AGENT_STATES = frozenset(
    {EXECUTE_TRIGGER, IN_PROGRESS_STATE, NEEDS_REVIEW_STATE}
)

MODE_PLAN = "plan"
MODE_EXECUTE = "execute"

STATUS_ROUTED = "routed"
STATUS_REJECTED = "rejected"
STATUS_EXECUTE_UNAVAILABLE = "execute_unavailable"
STATUS_INSPECTION_COMPLETE = "inspection_complete"
STATUS_INSPECTION_FAILED = "inspection_failed"
STATUS_INSPECTION_BLOCKED = "inspection_blocked"
STATUS_OPERATION_LOCKED = "operation_locked"
# Ticket 04: plan synthesis / publication outcomes (truthful terminals).
STATUS_SYNTHESIS_COMPLETE = "synthesis_complete"
STATUS_PLAN_SYNTHESIS_FAILED = "plan_synthesis_failed"
STATUS_PUBLICATION_FAILED = "publication_failed"
STATUS_PLAN_PUBLISHED = "plan_published"
STATUS_PLAN_NEEDS_CLARIFICATION = "plan_needs_clarification"

# Terminal Plan-side inspection outcomes (graph returns instead of raising).
INSPECTION_TERMINAL_STATUSES = frozenset(
    {STATUS_INSPECTION_COMPLETE, STATUS_INSPECTION_FAILED, STATUS_INSPECTION_BLOCKED}
)

# Ticket 05: a re-requested Plan on an Issue that already has a published
# plan arrives as agent::plan on top of the prior review/approval state.
# Exactly one such prior state alongside the trigger is a regeneration
# request; every prior state is invalidated when the run activates the Issue.
PRIOR_PLAN_STATES = frozenset({ISSUE_APPROVED_TRIGGER, NEEDS_REVIEW_STATE})

# Failure terminals that route the Plan graph to finalize (lock release).
# Success terminals (plan_published / plan_needs_clarification) route through
# request_review first; inspection_complete now continues into synthesis, and
# synthesis success carries the intermediate synthesis_complete status (a
# success path that leads into publish_plan, never a terminal).
PLAN_FAILURE_TERMINAL_STATUSES = frozenset(
    {
        STATUS_INSPECTION_FAILED,
        STATUS_INSPECTION_BLOCKED,
        STATUS_PLAN_SYNTHESIS_FAILED,
        STATUS_PUBLICATION_FAILED,
    }
)

# Read-only transport surface used by routing. Anything else (label writes,
# MR creation, merges, approvals, user/role lookups) must never be called.
ROUTING_READ_CALLS = frozenset({"get_project", "get_issue", "get_issue_merge_requests"})

# Terminal-handling failure types for GitLab transport calls inside the Plan
# graph: an API failure, or a locally-rejected argument (the client raises
# ValueError/TypeError before any HTTP round trip). Every handler maps these
# to a truthful terminal status instead of crashing the run.
_GITLAB_FAILURES = (GitLabAPIError, ValueError, TypeError)

class WorkflowState(TypedDict, total=False):
    """State handed to the selected mode graph."""

    project_id: str
    issue_iid: int
    branch_override: str | None
    mode: str
    status: str
    issue: dict[str, Any]
    project: dict[str, Any]
    issue_agent_labels: list[str]
    prior_agent_state: str | None
    related_mrs: list[dict[str, Any]]
    mr: dict[str, Any]
    baseline_branch: str
    source_branch: str
    verification_hints: dict[str, str | None]
    message: str
    # Ticket 03 Plan-inspection fields (all optional).
    operation: dict[str, Any]
    operation_context: OperationContext  # concrete object (no circular import)
    locks: list[OperationLock]  # released OperationLock objects
    workspace_repo_dir: str
    evidence_path: str
    inspection_evidence: dict[str, Any]
    error_message: str
    # Ticket 04 plan synthesis/publication fields (all optional).
    label_outcomes: list[dict[str, Any]]
    issue_notes: list[dict[str, Any]]
    plan: dict[str, Any]
    plan_synthesis: dict[str, Any]
    plan_synthesis_path: str
    plan_publication: dict[str, Any]


class RoutingRejection(RuntimeError):
    """Invalid invocation, state, or preflight; nothing was edited or published.

    Carries the structured routing outcome for the JSON report: ``mode`` is
    the selection the request was heading toward (``None`` when no valid
    trigger existed), and ``status`` is always ``rejected``.
    """

    def __init__(self, message: str, *, mode: str | None = None):
        super().__init__(message)
        self.mode = mode
        self.status = STATUS_REJECTED


class WorkflowUnavailableError(RuntimeError):
    """Routing succeeded but the selected mode graph is not implemented yet.

    Reported as an infrastructure error (exit 1) with a distinct structured
    status (``plan_unavailable`` / ``execute_unavailable``) and no product
    edits or publication of any kind.
    """

    def __init__(self, message: str, *, mode: str, status: str):
        super().__init__(message)
        self.mode = mode
        self.status = status


@dataclass(frozen=True)
class RoutingResult:
    """Validated routing outcome before any mode graph runs."""

    mode: str
    project: dict[str, Any]
    issue: dict[str, Any]
    issue_agent_labels: list[str]
    baseline_branch: str | None = None
    mr: dict[str, Any] | None = None
    open_mrs: list[dict[str, Any]] = field(default_factory=list)
    # Ticket 05: the prior review/approval state label a regeneration run
    # must invalidate (None for a fresh Plan or an Execute request).
    prior_agent_state: str | None = None


def _recognized_labels(labels: list[str], known: frozenset[str]) -> list[str]:
    return [label for label in labels if label in known]


def _unrecognized_agent_labels(labels: list[str], known: frozenset[str]) -> list[str]:
    return [
        label
        for label in labels
        if label.startswith("agent::") and label not in known
    ]


def _describe_labels(labels: list[str]) -> str:
    return ", ".join(labels) if labels else "(none)"


def split_regeneration_state(recognized: list[str]) -> str | None:
    """Split a recognized-label set into the regeneration prior state, if any.

    Ticket 05: ``agent::plan`` together with exactly ONE prior review or
    approval state (``agent::approved`` / ``agent::needs-review``) is how a
    human re-requests a Plan on an Issue that already has a published plan.
    This one predicate is the regeneration shape for BOTH call sites that
    read labels — routing (before the lock) and ``activate_issue`` (the
    under-lock re-read). Any other combination (``agent::in-progress``
    alongside the trigger, or more than two recognized states) is not a
    regeneration request and returns ``None``.
    """
    if len(recognized) != 2 or PLAN_TRIGGER not in recognized:
        return None
    prior = [label for label in recognized if label in PRIOR_PLAN_STATES]
    return prior[0] if len(prior) == 1 else None


def _issue_label_advice() -> str:
    return (
        "Valid Issue triggers: agent::plan to request a Plan, or agent::approved "
        "together with one open related merge request labeled agent::implement "
        "to request Execute."
    )


def _reject_issue_state(issue_iid: int, labels: list[str]) -> RoutingRejection:
    known = ISSUE_AGENT_STATES
    recognized = _recognized_labels(labels, known)
    unrecognized = _unrecognized_agent_labels(labels, known)
    if len(recognized) > 1:
        return RoutingRejection(
            f"Issue #{issue_iid} carries conflicting agent labels: "
            f"{_describe_labels(sorted(recognized))}. The only accepted "
            f"multi-label combination is {PLAN_TRIGGER} together with "
            "exactly one prior review/approval state "
            f"({_describe_labels(sorted(PRIOR_PLAN_STATES))}) — a Plan "
            f"regeneration request. Any other combination of the Issue "
            f"agent states ({_describe_labels(sorted(known))}) is rejected. "
            "Remove the extra agent labels so the Issue carries one trigger "
            "label (or exactly that regeneration combination) and retry; "
            "other labels are left untouched."
        )
    if recognized:
        trigger = recognized[0]
        if trigger == IN_PROGRESS_STATE:
            return RoutingRejection(
                f"Issue #{issue_iid} is labeled agent::in-progress. An in-progress "
                "label is not a request to resume work. Wait for the current "
                "transition to finish, or reset the Issue to a valid trigger: "
                f"{_issue_label_advice()}"
            )
        # agent::needs-review
        return RoutingRejection(
            f"Issue #{issue_iid} is labeled agent::needs-review. Work is awaiting "
            "human review and nothing was started. To request Execute, remove "
            "agent::needs-review, apply agent::approved, and prepare one open "
            "related merge request labeled agent::implement. Alternatively, "
            "apply agent::plan to request a new Plan."
        )
    message = (
        f"Issue #{issue_iid} has no recognized agent state label "
        f"(labels: {_describe_labels(labels)}). {_issue_label_advice()}"
    )
    if unrecognized:
        message += (
            f" Unrecognized agent-like labels present (left untouched): "
            f"{_describe_labels(sorted(unrecognized))}."
        )
    return RoutingRejection(message)


def _reject_mr(mr: dict[str, Any], issue_iid: int, problem: str, advice: str) -> RoutingRejection:
    return RoutingRejection(
        f"Open merge request !{mr.get('iid', '?')} related to Issue "
        f"#{issue_iid} {problem} {advice}",
        mode=MODE_EXECUTE,
    )


def _select_implement_mr(
    project: dict[str, Any], issue_iid: int, open_mrs: list[dict[str, Any]]
) -> dict[str, Any]:
    """Validate the single open related MR required for Execute preflight."""
    mr = open_mrs[0]
    issue_project_id = project.get("id")
    if not isinstance(issue_project_id, int):
        raise RoutingRejection(
            f"GitLab project {project.get('path_with_namespace', '?')} did not "
            "report its numeric project ID, so the merge request's origin "
            "cannot be verified against the Issue project. Execute requires "
            "the merge request to live within the Issue project.",
            mode=MODE_EXECUTE,
        )
    # A fork MR keeps the Issue project as its target while its source lives
    # in another project; every project identity on the MR must equal the
    # Issue project. Missing identities reject rather than pass silently.
    for key in ("source_project_id", "target_project_id", "project_id"):
        mr_project_id = mr.get(key)
        if mr_project_id != issue_project_id:
            raise _reject_mr(
                mr,
                issue_iid,
                f"belongs to project {mr_project_id!r} "
                f"({key}), not the Issue project {issue_project_id}.",
                "Execute requires a merge request within the Issue project "
                "(no forks); close it and create one in the Issue project "
                "labeled agent::implement.",
            )

    mr_labels = [str(label) for label in (mr.get("labels") or [])]
    mr_recognized = _recognized_labels(mr_labels, MR_AGENT_STATES)
    if len(mr_recognized) > 1:
        raise _reject_mr(
            mr,
            issue_iid,
            f"carries conflicting agent labels: "
            f"{_describe_labels(sorted(mr_recognized))}. At most one recognized "
            f"MR agent state is allowed ({_describe_labels(sorted(MR_AGENT_STATES))}).",
            "Remove the extra agent labels so the MR carries only "
            "agent::implement, and retry.",
        )
    if not mr_recognized:
        raise _reject_mr(
            mr,
            issue_iid,
            f"is not labeled agent::implement (labels: {_describe_labels(mr_labels)}).",
            "Label it agent::implement to request Execute, and retry.",
        )
    if mr_recognized[0] != EXECUTE_TRIGGER:
        raise _reject_mr(
            mr,
            issue_iid,
            f"is labeled {mr_recognized[0]}; an in-progress or needs-review "
            "merge request is not a request to execute.",
            "Wait for the current transition to finish, or reset the MR "
            "label to agent::implement and retry.",
        )
    return mr


def _select_plan_baseline(
    project: dict[str, Any],
    issue_iid: int,
    related_mrs: list[dict[str, Any]],
) -> str | None:
    """Apply the agreed planning-baseline matrix (implementation plan §3.3).

    Order of precedent:
    1. explicit ``--branch`` override (handled by the caller before this runs);
    2. no related MR → the Project default branch;
    3. exactly one related MR (open or historical) → its target branch;
    4. several related MRs with exactly one open → that open MR's target;
    5. several open MRs → ambiguous, reject and require an explicit branch;
    6. other ambiguous historical cases (several closed MRs disagreeing on
       their target branch, or a missing target branch) → reject and require
       an explicit branch.

    Returns the selected branch or ``None`` when the Project default branch
    is missing; raises :class:`RoutingRejection` for ambiguous MR situations.
    """
    open_mrs = [mr for mr in related_mrs if mr.get("state") == "opened"]

    def require_branch(problem: str) -> RoutingRejection:
        return RoutingRejection(
            f"Issue #{issue_iid} requests a Plan, but {problem} "
            "Pass --branch to choose the planning baseline explicitly and "
            "retry; nothing was edited or published.",
            mode=MODE_PLAN,
        )

    def mr_target(mr: dict[str, Any]) -> str | None:
        target = mr.get("target_branch")
        return str(target) if target else None

    if not related_mrs:
        # Case 2: no related MR context at all — the Project default branch.
        default_branch = project.get("default_branch")
        return str(default_branch) if default_branch else None

    if len(related_mrs) == 1:
        # Case 3: a single related MR (open or historical) decides the target.
        target = mr_target(related_mrs[0])
        if not target:
            raise require_branch(
                f"the single related merge request "
                f"!{related_mrs[0].get('iid', '?')} did not report a target branch."
            )
        return target

    if len(open_mrs) == 1:
        # Case 4: exactly one open MR among several — its target branch wins.
        target = mr_target(open_mrs[0])
        if not target:
            raise require_branch(
                f"the single open merge request !{open_mrs[0].get('iid', '?')} "
                "did not report a target branch."
            )
        return target

    if len(open_mrs) > 1:
        # Case 5: several open MRs — publication baseline is ambiguous.
        iids = ", !".join(str(mr.get("iid", "?")) for mr in open_mrs)
        raise require_branch(
            f"{len(open_mrs)} open merge requests are related to it "
            f"(!{iids}), so the planning baseline is ambiguous."
        )

    # Case 6: only historical (closed/merged) MRs remain. Their targets agree
    # only when every MR points at the same branch; otherwise it is ambiguous.
    targets = sorted({target for mr in related_mrs if (target := mr_target(mr))})
    if len(targets) == 1:
        return targets[0]
    listed = ", ".join(targets) if targets else "(none reported)"
    raise require_branch(
        f"several historical merge requests are related to it with disagreeing "
        f"target branches ({listed})."
    )


def route_issue_request(
    *,
    gitlab: Any,
    project_id: str | int,
    issue_iid: int,
    branch_override: str | None = None,
) -> RoutingResult:
    """Validate the Issue's agent labels and select Plan or Execute.

    Approval depends only on current label values: no user, role, permission,
    timestamp, or signed-approval lookup happens anywhere. Unrecognized labels
    are never removed or mutated, and no label writes happen here.

    Ticket 05: ``agent::plan`` together with exactly one prior review or
    approval state (``agent::needs-review`` / ``agent::approved``) is a
    regeneration request and takes the Plan flow; the prior state is recorded
    on the result so the run can invalidate it. Any other multi-label
    combination (and a lone ``agent::needs-review`` / ``agent::in-progress``)
    is rejected before anything runs.
    """
    project = gitlab.get_project(project_id)
    issue = gitlab.get_issue(project_id, issue_iid)
    labels = [str(label) for label in (issue.get("labels") or [])]
    recognized = _recognized_labels(labels, ISSUE_AGENT_STATES)
    prior_state = split_regeneration_state(recognized)
    if len(recognized) != 1 and prior_state is None:
        raise _reject_issue_state(issue_iid, labels)
    trigger = recognized[0]

    if prior_state is None and trigger not in (
        PLAN_TRIGGER,
        ISSUE_APPROVED_TRIGGER,
    ):
        # agent::in-progress / agent::needs-review are valid single states but
        # not requests to start work; reject at the Issue-state boundary
        # before any related-MR read or product activity.
        raise _reject_issue_state(issue_iid, labels)

    if trigger == PLAN_TRIGGER or prior_state is not None:
        # Fresh Plan request or a Ticket-05 regeneration request: both take
        # the Plan flow. A regeneration carries agent::plan plus exactly one
        # prior review/approval state (in any label order); activate_issue
        # invalidates that prior state under the Issue lock.
        if branch_override is not None:
            # Case 1: the explicit override wins outright; related MRs are
            # irrelevant to the baseline and are not even read.
            baseline_branch: str | None = branch_override
        else:
            related_mrs = gitlab.get_issue_merge_requests(project_id, issue_iid)
            baseline_branch = _select_plan_baseline(project, issue_iid, related_mrs)
        if not baseline_branch:
            raise RoutingRejection(
                f"GitLab project {project.get('path_with_namespace', project_id)} "
                "did not report a default branch, so no Plan inspection baseline "
                "could be selected. Pass --branch to choose the baseline explicitly."
            )
        return RoutingResult(
            mode=MODE_PLAN,
            project=project,
            issue=issue,
            issue_agent_labels=sorted(recognized),
            baseline_branch=baseline_branch,
            prior_agent_state=prior_state,
        )

    # agent::approved: Execute selection requires one open related MR that
    # carries agent::implement and belongs to the Issue project.
    if branch_override is not None:
        raise RoutingRejection(
            "--branch is only supported for Plan (inspection baseline). Execute "
            "takes its source branch from the approved merge request; remove "
            "--branch and retry.",
            mode=MODE_EXECUTE,
        )

    related = gitlab.get_issue_merge_requests(project_id, issue_iid)
    open_mrs = [mr for mr in related if mr.get("state") == "opened"]
    if not open_mrs:
        raise RoutingRejection(
            f"Issue #{issue_iid} is labeled agent::approved, but no open merge "
            "request is related to it. Create one merge request from your source "
            "branch in the same project (link it to this Issue, e.g. 'Closes "
            f"#{issue_iid}'), label it agent::implement, and retry.",
            mode=MODE_EXECUTE,
        )
    if len(open_mrs) > 1:
        iids = ", !".join(str(mr.get("iid", "?")) for mr in open_mrs)
        raise RoutingRejection(
            f"Issue #{issue_iid} is labeled agent::approved, but {len(open_mrs)} "
            f"open merge requests are related to it (!{iids}). Publication would "
            "be ambiguous; close or merge all but one, keep exactly one open MR "
            "labeled agent::implement, and retry.",
            mode=MODE_EXECUTE,
        )

    mr = _select_implement_mr(project, issue_iid, open_mrs)

    return RoutingResult(
        mode=MODE_EXECUTE,
        project=project,
        issue=issue,
        issue_agent_labels=[trigger],
        mr=mr,
        open_mrs=open_mrs,
    )


def _unavailable_mode_graph(
    settings: Settings, *, mode: str, status: str, node_name: str, message: str
):
    """Compile a mode graph that fails clearly instead of doing product work.

    The Execute graph (and Plan publication) arrive in later tickets. Until
    then a routed request is validated, then reported as an infrastructure
    error with a distinct structured status and no product edits or
    publication.
    """

    def not_available(state: WorkflowState) -> dict[str, Any]:
        raise WorkflowUnavailableError(message, mode=mode, status=status)

    builder = StateGraph(WorkflowState)
    builder.add_node(node_name, not_available)
    builder.add_edge(START, node_name)
    builder.add_edge(node_name, END)
    return builder.compile()


def build_execute_graph(settings: Settings):
    """Compile the Execute-mode graph (not implemented yet in this transition)."""
    return _unavailable_mode_graph(
        settings,
        mode=MODE_EXECUTE,
        status=STATUS_EXECUTE_UNAVAILABLE,
        node_name="execute_unavailable",
        message=(
            "Execute is not implemented yet in this transition: the request was "
            "validated and routed to Execute, but there is no execution graph to "
            "run. Nothing was edited or published."
        ),
    )


# ---------------------------------------------------------------------------
# Plan-side inspection graph (ticket 03)
# ---------------------------------------------------------------------------

def _worker_working_dir(operation: OperationContext, repo_dir: Path, settings: Settings) -> str:
    """Translate the coordinator clone path into the planner worker's path.

    The planner mounts the per-operation workspace root read-only at
    ``PLANNER_WORKSPACE_MOUNT`` (default ``/workspace`` in compose), so the
    clone lives at ``<mount>/<operation-id>/repo`` inside the worker.

    Raises ``OperationWorkspaceError`` when the clone is not under the
    configured ``WORKSPACE_ROOT``: the worker path could not be derived, and
    silently pointing the planner at the mount root would inspect the wrong
    directory.
    """
    mount = (settings.planner_workspace_mount or "/workspace").rstrip("/")
    root = Path(settings.workspace_root).expanduser().resolve()
    try:
        relative = Path(repo_dir).resolve().relative_to(root)
    except ValueError:
        raise OperationWorkspaceError(
            f"Inspection clone {Path(repo_dir).resolve()} is not under the "
            f"configured WORKSPACE_ROOT ({root}), so the planner worker "
            f"working directory cannot be derived for operation "
            f"{operation.operation_id}. Fix WORKSPACE_ROOT / "
            "PLANNER_WORKSPACE_MOUNT so the coordinator workspace root and "
            "the planner's read-only mount describe the same location; "
            "nothing was edited or published."
        ) from None
    return f"{mount}/{relative}"


def _find_publication_note(notes: list[dict[str, Any]], publication_uid: str) -> dict | None:
    """Scan read notes for an already-published plan with this publication UID.

    This is the §5.1 publication-UID dedup: the same UID is never posted twice,
    no matter whether the earlier POST's response was received or lost.
    """
    from . import planning

    for note in notes:
        if not isinstance(note, dict):
            continue
        parsed = planning.parse_plan_note(note)
        if parsed is not None and parsed.attrs.get("publication_uid") == publication_uid:
            return note
    return None


def _record_plan_synthesis_evidence(context: OperationContext, payload: dict) -> Path:
    """Write plan-synthesis evidence to artifact storage (outside the checkout).

    Mirrors ``record_evidence``: only this operation's own artifact files are
    written; retained evidence from earlier operations is never touched.
    """
    plan_dir = Path(context.artifact_path) / "plan"
    plan_dir.mkdir(parents=True, exist_ok=True)
    synthesis_path = plan_dir / "plan-synthesis.json"
    synthesis_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return synthesis_path


def build_plan_graph(
    settings: Settings,
    *,
    operation_factory=None,
    planner_transport=None,
    clone_fn=None,
    gitlab=None,
):
    """Compile the read-only Plan inspection + synthesis + publication graph.

    Flow (§7): validate setup → create operation context → acquire the Issue
    lock → **activate_issue** (agent::plan → agent::in-progress label
    transition; a Ticket-05 regeneration request — agent::plan plus exactly
    one prior review/approval state — also invalidates that prior state
    here) → prepare a fresh inspection clone at the baseline branch →
    run the read-only inspection Planner through the injectable transport →
    record inspection evidence → **synthesize_plan** (re-read ALL Issue
    comments under the lock, allocate the next plan version, run the planner
    again for the strict-JSON plan content, assemble + validate via
    planning.build_plan) → **publish_plan** (one append-only comment with
    publication-UID dedup before AND after the POST, still under the lock) →
    **request_review** (agent::in-progress → agent::needs-review transition) →
    finalize (lock release, retained evidence).

    Truthful terminal states: ``plan_published`` (executable plan published),
    ``plan_needs_clarification`` (non-executable plan published with
    unresolved questions), ``plan_synthesis_failed`` / ``publication_failed``
    (nothing published), and the inspection failed/blocked states.
    ``synthesis_complete`` is the intermediate SUCCESS status of the
    synthesize_plan node (the plan content validated); it always leads into
    publish_plan, which overwrites ``status`` with the final truthful
    outcome. The Issue lock is held from acquisition through publication;
    ``finalize_inspection`` releases it. Nothing is ever deleted or reset,
    and no branch/commit/push/MR-creation path exists anywhere in this graph.

    Injection seams keep offline tests free of Docker/LLM/GitLab:
    - ``operation_factory(settings) -> OperationContext``
    - ``planner_transport``: a ``PlannerTransport`` (remote adapter by default)
    - ``clone_fn``: defaults to ``git_ops.clone_repository``
    - ``gitlab``: a GitLab transport (defaults to ``GitLabClient`` from
      settings); used for label transitions, comment reads, and publication.
    """
    from .git_ops import GitOperationError, clone_repository
    from .planner_runner import (
        PlannerRunError,
        PlannerSetupError,
        build_inspection_evidence,
        make_plan_synthesis_prompt,
        make_planner_prompt,
        record_evidence,
    )
    from .workspace import (
        OperationWorkspaceError,
        acquire_issue_and_branch_locks,
        create_operation_context as _default_operation_factory,
        release_locks,
    )

    operation_factory = operation_factory or _default_operation_factory
    clone_fn = clone_fn or clone_repository
    transport = planner_transport
    gitlab_transport = gitlab

    def _gitlab():
        # The GitLab transport is created lazily so graphs that never reach
        # publication (blocked at setup) never construct one needlessly.
        if gitlab_transport is not None:
            return gitlab_transport
        return GitLabClient(settings.gitlab_url, settings.gitlab_token)

    def validate_setup(state: WorkflowState) -> dict[str, Any]:
        if not settings.planner_agent_server_url:
            return {
                "status": STATUS_INSPECTION_BLOCKED,
                "error_message": (
                    "Plan inspection is not configured: PLANNER_AGENT_SERVER_URL "
                    "is not set. Start the separately launched Planner Agent "
                    "Server worker (compose service 'planner' via "
                    "scripts/launch.sh or scripts/launch.ps1) and set "
                    "PLANNER_AGENT_SERVER_URL (plus AGENT_SERVER_API_KEY when "
                    "the worker requires a session key). Nothing was cloned, "
                    "inspected, edited, or published."
                ),
            }
        return {}

    def create_operation(state: WorkflowState) -> dict[str, Any]:
        try:
            operation = operation_factory(settings)
        except OperationWorkspaceError as exc:
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": str(exc),
            }
        return {"operation": operation.describe(), "operation_context": operation}

    def acquire_issue_lock(state: WorkflowState) -> dict[str, Any]:
        context = state["operation_context"]
        # Concurrent ownership is rejected immediately (no waiting). The
        # exception propagates to the application boundary (exit 2): nothing
        # was started, edited, or published.
        locks = acquire_issue_and_branch_locks(
            Path(settings.control_root).expanduser(),
            operation_id=context.operation_id,
            gitlab_url=settings.gitlab_url,
            project_id=state["project"]["id"],
            issue_iid=state["issue_iid"],
        )
        return {"locks": locks}

    def activate_issue(state: WorkflowState) -> dict[str, Any]:
        # Explicit label transition agent::plan → agent::in-progress (§3.2),
        # using add/remove operations only so unrelated labels survive. The
        # failure is truthful and terminal: no inspection, no publication.
        # Ticket 05: a regeneration request (agent::plan plus exactly one
        # prior review/approval state) also invalidates that prior state
        # here — the approval is reset for EVERY regeneration.
        issue_iid = state["issue_iid"]
        project_id = state["project"]["id"]
        outcome = {
            "transition": "plan_to_in_progress",
            "removed_labels": [PLAN_TRIGGER],
            "added_labels": [IN_PROGRESS_STATE],
        }
        # Re-read the Issue UNDER the lock (§3.2 race guard): the labels may
        # have changed between routing (read without the lock) and this
        # transition — e.g. a human added agent::approved. Transitioning on
        # stale labels would act on a request that no longer exists.
        try:
            fresh_issue = _gitlab().get_issue(project_id, issue_iid)
        except _GITLAB_FAILURES as exc:
            failure = {
                **outcome,
                "outcome": "failed",
                "error": str(exc),
            }
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": (
                    f"Could not re-read Issue #{issue_iid} under the Issue "
                    f"lock before its label transition: {exc}. Nothing was "
                    "transitioned, no inspection clone was prepared, and "
                    "nothing was published; the lock is released by "
                    "finalization."
                ),
                "label_outcomes": [failure],
            }
        fresh_labels = [str(label) for label in (fresh_issue.get("labels") or [])]
        recognized = _recognized_labels(fresh_labels, ISSUE_AGENT_STATES)
        prior_state = split_regeneration_state(recognized)
        invalidated = [prior_state] if prior_state is not None else []
        if recognized != [PLAN_TRIGGER] and prior_state is None:
            blocked = {
                **outcome,
                "outcome": "blocked_labels_changed",
                "found_labels": fresh_labels,
            }
            return {
                "status": STATUS_INSPECTION_BLOCKED,
                "error_message": (
                    f"Issue #{issue_iid} no longer carries a valid Plan "
                    f"request state ({PLAN_TRIGGER}, optionally together "
                    f"with exactly one of "
                    f"{_describe_labels(sorted(PRIOR_PLAN_STATES))}) under "
                    f"the Issue lock; its current recognized agent labels "
                    f"are: {_describe_labels(sorted(recognized))} (all "
                    f"labels: {_describe_labels(fresh_labels)}). The Issue "
                    "state changed after routing — possibly a human "
                    "transitioned or approved it. Nothing was transitioned, "
                    "no inspection clone was prepared, nothing was "
                    "published, and no other labels were touched. Re-run "
                    f"with the Issue carrying exactly {PLAN_TRIGGER} (plus "
                    "optionally one prior review/approval state to "
                    "regenerate) to start a fresh Plan."
                ),
                "label_outcomes": [blocked],
            }
        removed_labels = [PLAN_TRIGGER, *invalidated]
        try:
            _gitlab().update_issue_labels(
                project_id,
                issue_iid,
                remove_labels=removed_labels,
                add_labels=[IN_PROGRESS_STATE],
            )
        except _GITLAB_FAILURES as exc:
            failure = {
                **outcome,
                "outcome": "failed",
                "error": str(exc),
            }
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": (
                    f"Could not transition Issue #{issue_iid} from agent::plan "
                    f"to agent::in-progress: {exc}. No inspection clone was "
                    "prepared, nothing was published, and no other labels "
                    "were touched."
                ),
                "label_outcomes": [failure],
            }
        transitioned = {**outcome, "outcome": "transitioned"}
        if invalidated:
            # Ticket 05: the prior review/approval state was invalidated —
            # recorded truthfully so the regeneration is auditable.
            transitioned["removed_labels"] = removed_labels
            transitioned["invalidated_labels"] = invalidated
        return {"label_outcomes": [transitioned]}

    def prepare_inspection_clone(state: WorkflowState) -> dict[str, Any]:
        context = state["operation_context"]
        repo_url = state["project"].get("http_url_to_repo")
        if not repo_url:
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": (
                    "GitLab project did not report an 'http_url_to_repo', so a "
                    "fresh inspection clone cannot be prepared. Nothing was "
                    "edited or published; retained artifacts stay in place."
                ),
            }
        destination = context.workspace_path / "repo"
        try:
            clone_fn(
                repo_url,
                destination,
                settings.gitlab_token,
                branch=state["baseline_branch"],
            )
        except GitOperationError as exc:
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": (
                    f"Fresh inspection clone of {repo_url} at branch "
                    f"{state['baseline_branch']} failed: {exc}. Workspace and "
                    "artifact evidence are retained; nothing was edited or "
                    "published."
                ),
            }
        return {"workspace_repo_dir": str(destination)}

    def inspect_repository(state: WorkflowState) -> dict[str, Any]:
        context = state["operation_context"]
        repo_dir = Path(state["workspace_repo_dir"])
        prompt = make_planner_prompt(
            state["issue"], state["project"], state["baseline_branch"], context
        )
        try:
            planner_result = transport.run(
                prompt=prompt,
                repo_dir=repo_dir,
                worker_working_dir=_worker_working_dir(context, repo_dir, settings),
                operation=context,
                settings=settings,
            )
        except PlannerSetupError as exc:
            return {
                "status": STATUS_INSPECTION_BLOCKED,
                "error_message": str(exc),
            }
        except PlannerRunError as exc:
            # Keep the deterministic coordinator-side evidence collected so
            # far and record the planner failure honestly.
            evidence = build_inspection_evidence(
                operation=context,
                repo_dir=repo_dir,
                baseline_branch=state["baseline_branch"],
                verification_hints=state.get("verification_hints"),
                planner_result={"status": "failed", "error": str(exc)},
            )
            evidence_path = record_evidence(context.artifact_path, evidence)
            return {
                "status": STATUS_INSPECTION_FAILED,
                "error_message": str(exc),
                "inspection_evidence": evidence.to_dict(),
                "evidence_path": str(evidence_path),
            }
        evidence = build_inspection_evidence(
            operation=context,
            repo_dir=repo_dir,
            baseline_branch=state["baseline_branch"],
            verification_hints=state.get("verification_hints"),
            planner_result=planner_result,
        )
        evidence_path = record_evidence(context.artifact_path, evidence)
        return {
            "status": STATUS_INSPECTION_COMPLETE,
            "inspection_evidence": evidence.to_dict(),
            "evidence_path": str(evidence_path),
        }

    def _record_synthesis_failure(
        *,
        status: str,
        message: str,
        version: int,
        publication_uid: str,
        attempts: list[dict[str, Any]],
        error: str | None = None,
    ) -> dict[str, Any]:
        """Assemble a truthful synthesis-failure state update.

        Nothing is published on any of these paths; the synthesis evidence
        record carries the publication identity and every attempt so far.
        ``error`` is included only when the failure carries a distinct setup
        problem (PlannerSetupError) worth surfacing as its own key.
        """
        record: dict[str, Any] = {
            "publication_uid": publication_uid,
            "allocated_version": version,
            "attempts": attempts,
        }
        if error is not None:
            record["error"] = error
        return {"status": status, "error_message": message, "plan_synthesis": record}

    def _run_synthesis_attempt(
        attempt: int,
        prompt: str,
        attempts: list[dict[str, Any]],
        *,
        context: OperationContext,
        repo_dir: Path,
        version: int,
        publication_uid: str,
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """Run one synthesis transport call.

        Returns ``(planner_result, None)`` on success, or ``(None, failure)``
        where ``failure`` is the truthful terminal state update for a setup
        block (PlannerSetupError) or a failed conversation (PlannerRunError).
        """
        try:
            planner_result = transport.run(
                prompt=prompt,
                repo_dir=repo_dir,
                worker_working_dir=_worker_working_dir(context, repo_dir, settings),
                operation=context,
                settings=settings,
            )
        except PlannerSetupError as exc:
            return None, _record_synthesis_failure(
                status=STATUS_INSPECTION_BLOCKED,
                message=(
                    f"Plan synthesis could not start: {exc} Nothing was "
                    "published; the Issue stays agent::in-progress."
                ),
                version=version,
                publication_uid=publication_uid,
                attempts=attempts,
                error=str(exc),
            )
        except PlannerRunError as exc:
            attempts.append(
                {"attempt": attempt, "planner": {"status": "failed", "error": str(exc)}}
            )
            return None, _record_synthesis_failure(
                status=STATUS_PLAN_SYNTHESIS_FAILED,
                message=(
                    f"Plan synthesis conversation failed: {exc} Nothing "
                    "was published; the Issue stays agent::in-progress."
                ),
                version=version,
                publication_uid=publication_uid,
                attempts=attempts,
            )
        return planner_result, None

    def _assemble_synthesized_plan(
        content: dict[str, Any],
        *,
        issue: dict[str, Any],
        project: dict[str, Any],
        inspection: dict[str, Any],
        version: int,
        publication_uid: str,
    ):
        """Deterministically assemble + validate the planner's content (§5.1)."""
        from . import planning

        return planning.build_plan(
            issue=issue,
            project=project,
            inspection=inspection,
            requirement_understanding=str(
                content.get("requirement_understanding") or ""
            ),
            impact=content.get("impact"),
            tasks=content.get("tasks"),
            validation_hints=content.get("validation_hints"),
            # Unresolved/conflicting requirements travel with the
            # plan and force executable=false — never dropped (§5.1).
            unresolved_questions=content.get("unresolved_questions"),
            version=version,
            publication_uid=publication_uid,
        )

    def synthesize_plan(state: WorkflowState) -> dict[str, Any]:
        # All of this happens under the held Issue lock: notes are re-read
        # fresh so version allocation (§5.3) sees every plan published up to
        # now, including comments that arrived after routing.
        context = state["operation_context"]
        evidence = state.get("inspection_evidence") or {}
        try:
            notes = _gitlab().get_issue_notes(
                state["project"]["id"], state["issue_iid"]
            )
        except _GITLAB_FAILURES as exc:
            return {
                "status": STATUS_PLAN_SYNTHESIS_FAILED,
                "error_message": (
                    f"Could not read the Issue comments for synthesis "
                    f"(#{state['issue_iid']}): {exc}. Nothing was published; "
                    "the Issue stays agent::in-progress."
                ),
            }
        from . import planning

        try:
            version = planning.next_plan_version(notes)
        except planning.PlanRecordError as exc:
            return {
                "status": STATUS_PLAN_SYNTHESIS_FAILED,
                "error_message": (
                    f"Could not allocate the next plan version from the "
                    f"Issue comments: {exc}. Nothing was published; "
                    "the Issue stays agent::in-progress."
                ),
            }
        # The operation identity doubles as the publication UID (§5.1): one
        # run owns exactly one publication attempt, so retries and lost
        # responses dedup against it deterministically.
        publication_uid = context.operation_id
        prompt = make_plan_synthesis_prompt(
            state["issue"],
            state["project"],
            state["baseline_branch"],
            context,
            notes=notes,
            inspection=evidence,
        )

        attempts: list[dict[str, Any]] = []
        correction: str | None = None
        plan = None
        last_result: dict[str, Any] = {}
        for attempt in range(1, 3):  # one original try + one correction retry
            planner_result, failure = _run_synthesis_attempt(
                attempt,
                prompt,
                attempts,
                context=context,
                repo_dir=Path(state["workspace_repo_dir"]),
                version=version,
                publication_uid=publication_uid,
            )
            if failure is not None:
                return failure
            last_result = planner_result
            try:
                # Shared fence extractor (planning.py): uses the LAST ```json
                # fence in the planner message — chatter may precede it — and
                # rejects anything that is not a parseable JSON object.
                content = planning.extract_plan_content_json(
                    planner_result.get("summary")
                )
                plan = _assemble_synthesized_plan(
                    content,
                    issue=state["issue"],
                    project=state["project"],
                    inspection=evidence,
                    version=version,
                    publication_uid=publication_uid,
                )
                break
            except (ValueError, TypeError) as exc:
                # Malformed planner output: tolerate exactly one correction
                # retry, then fail truthfully without publishing anything.
                correction = str(exc)
                attempts.append(
                    {"attempt": attempt, "error": correction, "status": "rejected"}
                )
                prompt = make_plan_synthesis_prompt(
                    state["issue"],
                    state["project"],
                    state["baseline_branch"],
                    context,
                    notes=notes,
                    inspection=evidence,
                    correction=correction,
                )
        if plan is None:
            return _record_synthesis_failure(
                status=STATUS_PLAN_SYNTHESIS_FAILED,
                message=(
                    f"Planner did not return a valid plan content object after "
                    f"one correction retry (last problem: {correction}). No "
                    "plan comment was published; the Issue stays "
                    "agent::in-progress."
                ),
                version=version,
                publication_uid=publication_uid,
                attempts=attempts,
            )

        synthesis_record = {
            "publication_uid": publication_uid,
            "allocated_version": version,
            "plan_id": plan.plan_id,
            "executable": plan.executable,
            "unresolved_questions_count": len(plan.unresolved_questions),
            "attempts": attempts,
            "planner": {
                "status": last_result.get("status"),
                "summary": str(last_result.get("summary") or ""),
                "conversation_id": last_result.get("conversation_id"),
                "transport": last_result.get("transport"),
            },
        }
        synthesis_path = _record_plan_synthesis_evidence(context, synthesis_record)
        # Truthful status: synthesis itself succeeded (the plan validated);
        # publication has not happened yet, so this is NOT inspection_complete
        # — publish_plan sets the final truthful outcome (plan_published /
        # plan_needs_clarification / publication_failed).
        return {
            "status": STATUS_SYNTHESIS_COMPLETE,
            "issue_notes": notes,
            "plan": plan.to_dict(),
            "plan_synthesis": synthesis_record,
            "plan_synthesis_path": str(synthesis_path),
        }

    def publish_plan(state: WorkflowState) -> dict[str, Any]:
        from . import planning

        # Still under the Issue lock. Dedup BEFORE posting: if a note with
        # this publication UID already exists, the publication already
        # happened and no duplicate note/version is created (§5.1).
        plan_dict = state["plan"]
        publication = planning.build_publication_note(plan_dict)
        publication_uid = publication["publication_uid"]
        try:
            notes = _gitlab().get_issue_notes(
                state["project"]["id"], state["issue_iid"]
            )
        except _GITLAB_FAILURES as exc:
            return {
                "status": STATUS_PUBLICATION_FAILED,
                "error_message": (
                    f"Could not re-read the Issue comments before publication "
                    f"(#{state['issue_iid']}): {exc}. Nothing was posted; the "
                    "Issue stays agent::in-progress."
                ),
            }
        existing = _find_publication_note(notes, publication_uid)
        deduplicated = existing is not None
        created = existing
        if created is None:
            try:
                created = _gitlab().add_issue_note(
                    state["project"]["id"], state["issue_iid"], publication["body"]
                )
            except _GITLAB_FAILURES as exc:
                # Lost-response dedup: the POST may have been applied before
                # the failure was observed. Re-scan once; reposting blind
                # would risk a duplicate version, so an unresolvable outcome
                # is a truthful publication failure (no repeated reposts).
                try:
                    reread = _gitlab().get_issue_notes(
                        state["project"]["id"], state["issue_iid"]
                    )
                except _GITLAB_FAILURES as rescan_exc:
                    return {
                        "status": STATUS_PUBLICATION_FAILED,
                        "error_message": (
                            f"Plan note POST failed ({exc}) and the dedup "
                            f"re-scan also failed ({rescan_exc}). The note may "
                            "or may not have been posted; nothing was "
                            "reposted. Re-run once the API recovers — the "
                            "publication UID dedups."
                        ),
                    }
                created = _find_publication_note(reread, publication_uid)
                if created is None:
                    return {
                        "status": STATUS_PUBLICATION_FAILED,
                        "error_message": (
                            f"Plan note POST failed ({exc}) and no note with "
                            f"publication UID {publication_uid} exists after "
                            "re-scan, so the plan was NOT published. Nothing "
                            "was reposted; the Issue stays agent::in-progress."
                        ),
                    }
        executable = bool(plan_dict.get("executable"))
        result_publication = {
            "version": plan_dict["version"],
            "plan_id": plan_dict["plan_id"],
            "publication_uid": publication_uid,
            "note_id": created.get("id") if isinstance(created, dict) else None,
            "note_body": publication["body"],
            "executable": executable,
            "unresolved_questions_count": len(
                plan_dict.get("unresolved_questions") or []
            ),
            "deduplicated": deduplicated,
        }
        status = STATUS_PLAN_PUBLISHED if executable else STATUS_PLAN_NEEDS_CLARIFICATION
        return {"status": status, "plan_publication": result_publication}

    def request_review(state: WorkflowState) -> dict[str, Any]:
        # Explicit transition agent::in-progress → agent::needs-review after a
        # successful publication (executable OR clarification-required). A
        # label failure must NOT erase the published plan nor claim total
        # failure: it is recorded as a distinct label_outcome and the
        # publication outcome stands.
        publication = dict(state["plan_publication"])
        outcome = {
            "transition": "in_progress_to_needs_review",
            "removed_labels": [IN_PROGRESS_STATE],
            "added_labels": [NEEDS_REVIEW_STATE],
        }
        try:
            _gitlab().update_issue_labels(
                state["project"]["id"],
                state["issue_iid"],
                remove_labels=[IN_PROGRESS_STATE],
                add_labels=[NEEDS_REVIEW_STATE],
            )
        except _GITLAB_FAILURES as exc:
            failure = {**outcome, "outcome": "needs_review_transition_failed", "error": str(exc)}
            publication["label_outcome"] = failure["outcome"]
            return {
                "plan_publication": publication,
                "label_outcomes": list(state.get("label_outcomes") or []) + [failure],
            }
        success = {**outcome, "outcome": "transitioned_to_needs_review"}
        publication["label_outcome"] = success["outcome"]
        return {
            "plan_publication": publication,
            "label_outcomes": list(state.get("label_outcomes") or []) + [success],
        }

    def finalize_inspection(state: WorkflowState) -> dict[str, Any]:
        # Release locks (reverse order). Retained evidence is never deleted.
        release_locks(state.get("locks") or [])
        if state.get("error_message"):
            message = state["error_message"]
        elif state.get("plan_publication"):
            publication = state["plan_publication"]
            kind = "executable" if publication.get("executable") else (
                "NOT executable — clarification required"
            )
            message = (
                f"Plan {publication.get('plan_id')} published "
                f"(note {publication.get('note_id')}, publication UID "
                f"{publication.get('publication_uid')}, {kind}); "
                f"label outcome: {publication.get('label_outcome')}."
            )
        else:
            message = "Inspection complete."
        return {"message": message}

    builder = StateGraph(WorkflowState)
    builder.add_node("validate_setup", validate_setup)
    builder.add_node("create_operation", create_operation)
    builder.add_node("acquire_issue_lock", acquire_issue_lock)
    builder.add_node("activate_issue", activate_issue)
    builder.add_node("prepare_inspection_clone", prepare_inspection_clone)
    builder.add_node("inspect_repository", inspect_repository)
    builder.add_node("synthesize_plan", synthesize_plan)
    builder.add_node("publish_plan", publish_plan)
    builder.add_node("request_review", request_review)
    builder.add_node("finalize_inspection", finalize_inspection)

    def route_after(node_name: str, next_node: str):
        def router(state: WorkflowState) -> str:
            if state.get("status") in PLAN_FAILURE_TERMINAL_STATUSES:
                return "finalize_inspection"
            return next_node

        builder.add_conditional_edges(node_name, router, ["finalize_inspection", next_node])

    builder.add_edge(START, "validate_setup")
    route_after("validate_setup", "create_operation")
    route_after("create_operation", "acquire_issue_lock")
    # Lock acquisition raises ConcurrentOperationError on conflict (it must
    # propagate). From here through publication the Issue lock is held; every
    # failure terminal routes to finalize_inspection, which releases it.
    route_after("acquire_issue_lock", "activate_issue")
    route_after("activate_issue", "prepare_inspection_clone")
    route_after("prepare_inspection_clone", "inspect_repository")
    route_after("inspect_repository", "synthesize_plan")
    route_after("synthesize_plan", "publish_plan")

    def route_after_publication(state: WorkflowState) -> str:
        # Only a successful publication (executable or clarification-required)
        # reaches the needs-review transition; failures go straight to finalize.
        if state.get("plan_publication"):
            return "request_review"
        return "finalize_inspection"

    builder.add_conditional_edges(
        "publish_plan",
        route_after_publication,
        ["request_review", "finalize_inspection"],
    )
    builder.add_edge("request_review", "finalize_inspection")
    builder.add_edge("finalize_inspection", END)
    return builder.compile()


def run_routed_workflow(
    settings: Settings,
    *,
    project_id: str | int,
    issue_iid: int,
    branch_override: str | None = None,
    gitlab: Any | None = None,
    plan_graph=None,
    execute_graph=None,
) -> dict[str, Any]:
    """Validate labels, route, and dispatch to the selected mode graph.

    ``gitlab`` is a duck-typed GitLab transport (see ``GitLabClient``); the
    mode graphs are injectable so tests can supply controlled stand-ins. There
    is no fallback to the old unconditional implementation graph.
    """
    gitlab = gitlab if gitlab is not None else GitLabClient(
        settings.gitlab_url, settings.gitlab_token
    )
    routing = route_issue_request(
        gitlab=gitlab,
        project_id=project_id,
        issue_iid=issue_iid,
        branch_override=branch_override,
    )

    if routing.mode == MODE_PLAN:
        graph = (
            plan_graph
            if plan_graph is not None
            else build_plan_graph(settings, gitlab=gitlab)
        )
    else:
        graph = execute_graph if execute_graph is not None else build_execute_graph(settings)

    state: WorkflowState = {
        "project_id": str(project_id),
        "issue_iid": issue_iid,
        "branch_override": branch_override,
        "mode": routing.mode,
        "status": STATUS_ROUTED,
        "issue": routing.issue,
        "project": routing.project,
        "issue_agent_labels": routing.issue_agent_labels,
        "related_mrs": routing.open_mrs,
        # Verification hints remain optional plumbing for the mode graphs.
        # Ticket 05: None for a fresh Plan / Execute; the invalidated prior
        # review/approval state label for a regeneration request.
        "prior_agent_state": routing.prior_agent_state,
        "verification_hints": {
            "setup_command": settings.setup_command,
            "build_command": settings.build_command,
            "test_command": settings.test_command,
        },
    }
    if routing.baseline_branch is not None:
        state["baseline_branch"] = routing.baseline_branch
    if routing.mr is not None:
        state["mr"] = routing.mr
        source_branch = routing.mr.get("source_branch")
        if source_branch:
            state["source_branch"] = str(source_branch)
    return graph.invoke(state)
