from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .checks import format_check_failures, run_build_and_test
from .config import Settings
from .git_ops import (
    clone_repository,
    commit_local,
    get_working_tree_summary,
)
from .gitlab_client import GitLabClient
from .openhands_runner import run_openhands

# Injectable seams for the workflow. The defaults are the real implementations;
# tests supply controlled fakes so no GitLab HTTP call or model call happens.
# ``gitlab`` is intentionally duck-typed (a GitLabClient-like transport with
# ``get_project``/``get_issue``) until ticket 02 defines the transport contract.
AgentRunner = Callable[..., str]
VerificationRunner = Callable[..., dict[str, Any]]
Clock = Callable[[], datetime]


class WorkflowState(TypedDict, total=False):
    project_id: str
    issue_iid: int
    execute: bool
    publish: bool
    target_branch: str | None
    mr_title: str | None
    mr_description: str | None
    draft_mr: bool
    issue: dict[str, Any]
    project: dict[str, Any]
    run_dir: str
    repo_dir: str
    branch: str
    agent_result: str
    working_tree_summary: str
    check_history: list[dict[str, Any]]
    last_check_report: dict[str, Any]
    checks_passed: bool
    checks_configured: bool
    fix_attempts: int
    # Sha of the local commit created from the agent's working-tree changes
    # (when there were any). Never pushed during the transition.
    commit_sha: str
    published: bool
    status: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_graph(
    settings: Settings,
    *,
    gitlab: Any | None = None,
    agent_runner: AgentRunner | None = None,
    verification_runner: VerificationRunner | None = None,
    clock: Clock | None = None,
):
    """Compile the transition workflow graph.

    ``gitlab`` is a duck-typed GitLab transport exposing ``get_project`` and
    ``get_issue`` (see ``GitLabClient``); ``agent_runner``,
    ``verification_runner``, and ``clock`` are the other injectable seams. Each
    defaults to the real implementation. During the transition the workflow
    never creates branches, pushes, or opens merge requests: it fetches the
    issue, prepares a clone-only workspace, runs the agent, runs verification
    with a bounded repair loop, preserves the agent's changes as a local
    commit, and collects the diff evidence.
    """
    gitlab = gitlab if gitlab is not None else GitLabClient(
        settings.gitlab_url, settings.gitlab_token
    )

    def default_agent_runner(
        *,
        repo_dir: Path,
        issue: dict[str, Any],
        project: dict[str, Any],
        branch: str,
        repair_context: str | None = None,
        repair_attempt: int | None = None,
    ) -> str:
        return run_openhands(
            repo_dir=Path(repo_dir),
            issue=issue,
            project=project,
            branch=branch,
            settings=settings,
            repair_context=repair_context,
            repair_attempt=repair_attempt,
        )

    def default_verification_runner(
        *,
        repo_dir: Path,
        run_dir: Path,
        round_number: int,
    ) -> dict[str, Any]:
        return run_build_and_test(
            repo_dir=Path(repo_dir),
            run_dir=Path(run_dir),
            setup_command=settings.setup_command,
            build_command=settings.build_command,
            test_command=settings.test_command,
            timeout_seconds=settings.check_timeout_seconds,
            round_number=round_number,
        )

    agent_runner = agent_runner if agent_runner is not None else default_agent_runner
    verification_runner = (
        verification_runner if verification_runner is not None else default_verification_runner
    )
    clock = clock if clock is not None else _utc_now

    def fetch_issue(state: WorkflowState) -> dict:
        project = gitlab.get_project(state["project_id"])
        issue = gitlab.get_issue(state["project_id"], state["issue_iid"])
        return {"project": project, "issue": issue, "status": "issue_fetched"}

    def prepare_workspace(state: WorkflowState) -> dict:
        project = state["project"]
        repo_url = project.get("http_url_to_repo")
        if not repo_url:
            raise RuntimeError(
                "GitLab project response did not include http_url_to_repo"
            )

        stamp = clock().strftime("%Y%m%dT%H%M%S%fZ")
        project_slug = str(project.get("path") or project.get("name") or "project")
        safe_slug = "".join(
            c if c.isalnum() or c in "-_ ." else "-" for c in project_slug
        ).replace(" ", "-")
        run_dir = (
            settings.output_dir / f"{safe_slug}-issue-{state['issue_iid']}-{stamp}"
        ).resolve()
        repo_dir = run_dir / "repo"
        target_branch = state.get("target_branch") or project.get("default_branch")
        if not target_branch:
            raise RuntimeError(
                "Could not determine GitLab target branch; pass --target-branch"
            )
        clone_repository(
            repo_url,
            repo_dir,
            settings.gitlab_token,
            branch=target_branch,
            git_username=settings.gitlab_git_username,
        )
        # Transition: clone only. No agent branch is created and nothing is
        # pushed; the agent works in the checked-out target branch workspace.
        return {
            "run_dir": str(run_dir),
            "repo_dir": str(repo_dir),
            "branch": target_branch,
            "target_branch": target_branch,
            "fix_attempts": 0,
            "check_history": [],
            "published": False,
            "status": "workspace_prepared",
        }

    def run_agent(state: WorkflowState) -> dict:
        result = agent_runner(
            repo_dir=Path(state["repo_dir"]),
            issue=state["issue"],
            project=state["project"],
            branch=state["branch"],
        )
        return {"agent_result": result, "status": "implementation_completed"}

    def run_checks(state: WorkflowState) -> dict:
        round_number = len(state.get("check_history", [])) + 1
        report = verification_runner(
            repo_dir=Path(state["repo_dir"]),
            run_dir=Path(state["run_dir"]),
            round_number=round_number,
        )
        history = list(state.get("check_history", [])) + [report]
        status = (
            "checks_passed"
            if report["passed"]
            else (
                "checks_failed"
                if report["configuration_complete"]
                else "checks_not_configured"
            )
        )
        return {
            "last_check_report": report,
            "check_history": history,
            "checks_passed": bool(report["passed"]),
            "checks_configured": bool(report["configuration_complete"]),
            "status": status,
        }

    def repair_agent(state: WorkflowState) -> dict:
        attempt = int(state.get("fix_attempts", 0)) + 1
        result = agent_runner(
            repo_dir=Path(state["repo_dir"]),
            issue=state["issue"],
            project=state["project"],
            branch=state["branch"],
            repair_context=format_check_failures(state.get("last_check_report") or {}),
            repair_attempt=attempt,
        )
        return {
            "agent_result": result,
            "fix_attempts": attempt,
            "status": f"repair_attempt_{attempt}_completed",
        }

    def collect_diff(state: WorkflowState) -> dict:
        repo_dir = Path(state["repo_dir"])
        # Capture the working-tree changes as diff evidence before they are
        # committed, so the report keeps describing the agent's edits.
        summary = get_working_tree_summary(repo_dir)
        # Preserve the work as a local commit on the workspace branch (evidence
        # for later tickets), but never push and never open a merge request.
        commit_sha = commit_local(
            repo_dir,
            author_name=settings.git_author_name,
            author_email=settings.git_author_email,
            commit_message=(
                f"agent: implement issue #{state['issue_iid']} "
                f"(local commit; not published)"
            ),
        )
        if state.get("checks_passed"):
            final_status = "checks_passed"
        elif not state.get("checks_configured"):
            final_status = "checks_not_configured"
        else:
            final_status = "checks_failed"
        # Transition: nothing is ever published. Publication returns with the
        # label-routing and delivery contracts in a later ticket.
        update: dict[str, Any] = {
            "working_tree_summary": summary,
            "status": final_status,
            "published": False,
        }
        if commit_sha:
            update["commit_sha"] = commit_sha
        return update

    def route_after_checks(state: WorkflowState) -> str:
        if state.get("checks_passed"):
            return "collect_diff"
        if not state.get("checks_configured"):
            # Incomplete verification must never route to publication.
            return "collect_diff"
        if int(state.get("fix_attempts", 0)) < settings.max_fix_attempts:
            return "repair_agent"
        return "collect_diff"

    builder = StateGraph(WorkflowState)
    for name, node in (
        ("fetch_issue", fetch_issue),
        ("prepare_workspace", prepare_workspace),
        ("run_agent", run_agent),
        ("run_checks", run_checks),
        ("repair_agent", repair_agent),
        ("collect_diff", collect_diff),
    ):
        builder.add_node(name, node)

    builder.add_edge(START, "fetch_issue")
    builder.add_edge("fetch_issue", "prepare_workspace")
    builder.add_edge("prepare_workspace", "run_agent")
    builder.add_edge("run_agent", "run_checks")
    builder.add_conditional_edges(
        "run_checks",
        route_after_checks,
        {
            "repair_agent": "repair_agent",
            "collect_diff": "collect_diff",
        },
    )
    builder.add_edge("repair_agent", "run_checks")
    builder.add_edge("collect_diff", END)
    return builder.compile()
