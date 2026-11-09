from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .config import Settings
from .gitlab_client import GitLabClient
from .workspace import ConcurrentOperationError  # re-exported for main.py

__all__ = [
    "ConcurrentOperationError",
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
EXECUTE_TRIGGER = "agent::implement"

ISSUE_AGENT_STATES = frozenset(
    {PLAN_TRIGGER, "agent::in-progress", "agent::needs-review", ISSUE_APPROVED_TRIGGER}
)
MR_AGENT_STATES = frozenset({EXECUTE_TRIGGER, "agent::in-progress", "agent::needs-review"})

MODE_PLAN = "plan"
MODE_EXECUTE = "execute"

STATUS_ROUTED = "routed"
STATUS_REJECTED = "rejected"
STATUS_PLAN_UNAVAILABLE = "plan_unavailable"
STATUS_EXECUTE_UNAVAILABLE = "execute_unavailable"
STATUS_INSPECTION_COMPLETE = "inspection_complete"
STATUS_INSPECTION_FAILED = "inspection_failed"
STATUS_INSPECTION_BLOCKED = "inspection_blocked"
STATUS_OPERATION_LOCKED = "operation_locked"

# Terminal Plan-side inspection outcomes (graph returns instead of raising).
INSPECTION_TERMINAL_STATUSES = frozenset(
    {STATUS_INSPECTION_COMPLETE, STATUS_INSPECTION_FAILED, STATUS_INSPECTION_BLOCKED}
)

# Read-only transport surface used by routing. Anything else (label writes,
# MR creation, merges, approvals, user/role lookups) must never be called.
ROUTING_READ_CALLS = frozenset({"get_project", "get_issue", "get_issue_merge_requests"})


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
    related_mrs: list[dict[str, Any]]
    mr: dict[str, Any]
    baseline_branch: str
    source_branch: str
    verification_hints: dict[str, str | None]
    message: str
    # Ticket 03 Plan-inspection fields (all optional).
    operation: dict[str, Any]
    operation_context: Any  # OperationContext object (in-memory)
    locks: list[Any]  # released OperationLock objects
    workspace_repo_dir: str
    evidence_path: str
    inspection_evidence: dict[str, Any]
    error_message: str


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
            f"{_describe_labels(sorted(recognized))}. At most one recognized Issue "
            f"agent state is allowed ({_describe_labels(sorted(known))}). "
            "Remove the extra agent labels on the Issue and retry; other labels "
            "are left untouched."
        )
    if recognized:
        trigger = recognized[0]
        if trigger == "agent::in-progress":
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
    """
    project = gitlab.get_project(project_id)
    issue = gitlab.get_issue(project_id, issue_iid)
    labels = [str(label) for label in (issue.get("labels") or [])]
    recognized = _recognized_labels(labels, ISSUE_AGENT_STATES)
    if len(recognized) != 1:
        raise _reject_issue_state(issue_iid, labels)
    trigger = recognized[0]

    if trigger not in (PLAN_TRIGGER, ISSUE_APPROVED_TRIGGER):
        # agent::in-progress / agent::needs-review are valid single states but
        # not requests to start work; reject at the Issue-state boundary
        # before any related-MR read or product activity.
        raise _reject_issue_state(issue_iid, labels)

    if trigger == PLAN_TRIGGER:
        if branch_override is not None:
            baseline_branch: str | None = branch_override
        else:
            baseline_branch = project.get("default_branch") or None
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
            issue_agent_labels=[trigger],
            baseline_branch=baseline_branch,
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

def _worker_working_dir(operation, repo_dir: Path, settings: Settings) -> str:
    """Translate the coordinator clone path into the planner worker's path.

    The planner mounts the per-operation workspace root read-only at
    ``PLANNER_WORKSPACE_MOUNT`` (default ``/workspace`` in compose), so the
    clone lives at ``<mount>/<operation-id>/repo`` inside the worker.
    """
    mount = (settings.planner_workspace_mount or "/workspace").rstrip("/")
    root = Path(settings.workspace_root).expanduser().resolve()
    try:
        relative = Path(repo_dir).resolve().relative_to(root)
        return f"{mount}/{relative}"
    except ValueError:
        return mount


def build_plan_graph(
    settings: Settings,
    *,
    operation_factory=None,
    planner_transport=None,
    clone_fn=None,
):
    """Compile the read-only Plan inspection graph.

    Flow: validate setup → create operation context (unique id, fresh
    workspace/artifact references) → acquire the Issue lock on the shared
    control volume (concurrent ownership rejects immediately) → prepare a
    fresh inspection clone at the baseline branch → run the read-only
    Planner through the injectable transport → record module/source/
    configuration evidence in artifact storage (outside the checkout).

    Plan synthesis/publication is deliberately NOT implemented here (ticket
    04). The graph stops at ``inspection_complete`` or a clearly failed/
    blocked state; failures retain all available evidence and release the
    lock. Nothing is ever deleted or reset.

    Injection seams keep offline tests free of Docker/LLM/GitLab:
    - ``operation_factory(settings) -> OperationContext``
    - ``planner_transport``: a ``PlannerTransport`` (remote adapter by default)
    - ``clone_fn``: defaults to ``git_ops.clone_repository``
    """
    from .git_ops import GitOperationError, clone_repository
    from .planner_runner import (
        PlannerRunError,
        PlannerSetupError,
        build_inspection_evidence,
        make_planner_prompt,
        record_evidence,
    )
    from .workspace import (
        ConcurrentOperationError,
        OperationWorkspaceError,
        acquire_issue_and_branch_locks,
        create_operation_context as _default_operation_factory,
        release_locks,
    )

    operation_factory = operation_factory or _default_operation_factory
    clone_fn = clone_fn or clone_repository
    transport = planner_transport

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

    def finalize_inspection(state: WorkflowState) -> dict[str, Any]:
        # Release locks (reverse order). Retained evidence is never deleted.
        release_locks(state.get("locks") or [])
        return {"message": state.get("error_message") or "Inspection complete."}

    builder = StateGraph(WorkflowState)
    builder.add_node("validate_setup", validate_setup)
    builder.add_node("create_operation", create_operation)
    builder.add_node("acquire_issue_lock", acquire_issue_lock)
    builder.add_node("prepare_inspection_clone", prepare_inspection_clone)
    builder.add_node("inspect_repository", inspect_repository)
    builder.add_node("finalize_inspection", finalize_inspection)

    def route_after(node_name: str, next_node: str):
        def router(state: WorkflowState) -> str:
            if state.get("status") in INSPECTION_TERMINAL_STATUSES:
                return "finalize_inspection"
            return next_node

        builder.add_conditional_edges(node_name, router, ["finalize_inspection", next_node])

    builder.add_edge(START, "validate_setup")
    route_after("validate_setup", "create_operation")
    route_after("create_operation", "acquire_issue_lock")
    # Lock acquisition raises ConcurrentOperationError on conflict (it must
    # propagate); after it, terminal routing covers clone/inspection failures.
    route_after("acquire_issue_lock", "prepare_inspection_clone")
    route_after("prepare_inspection_clone", "inspect_repository")
    route_after("inspect_repository", "finalize_inspection")
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
        graph = plan_graph if plan_graph is not None else build_plan_graph(settings)
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
