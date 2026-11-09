from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .checks import format_check_failures, run_build_and_test
from .config import Settings
from .git_ops import (
    GitOperationError,
    clone_repository,
    commit_and_push_branch,
    create_issue_branch,
    get_working_tree_summary,
)
from .gitlab_client import GitLabAPIError, GitLabClient
from .openhands_runner import run_openhands


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
    commit_sha: str
    merge_request: dict[str, Any]
    publish_error: str
    status: str


def _mr_description(state: WorkflowState, commit_sha: str) -> str:
    issue = state["issue"]
    issue_iid = issue.get("iid", state["issue_iid"])
    issue_url = issue.get("web_url")
    summary = (state.get("agent_result") or "").strip()
    if len(summary) > 5000:
        summary = summary[:5000] + "\n\n[Agent summary truncated]"
    report = state.get("last_check_report") or {}
    checks = report.get("checks") or {}
    check_lines = []
    for name in ("setup", "build", "test", "git_diff_check", "git_cached_diff_check"):
        item = checks.get(name) or {}
        if item:
            check_lines.append(
                f"- **{name}**: `{item.get('status', 'unknown')}` — `{item.get('command') or 'not configured'}`"
            )

    lines = [
        f"Closes #{issue_iid}",
        "",
        "## Automated implementation summary",
        summary
        or "The coding agent did not provide a summary. Please inspect the diff.",
        "",
        "## Validation",
        *check_lines,
        "",
        f"Repair attempts: {state.get('fix_attempts', 0)}",
        f"Validation round: {report.get('round', 'unknown')}",
        f"Commit: `{commit_sha[:12]}`",
    ]
    if issue_url:
        lines.extend(["", f"Original issue: {issue_url}"])
    lines.extend(
        [
            "",
            "Please review the code diff and CI pipeline before merging. This POC never merges automatically.",
        ]
    )
    return "\n".join(lines)


def build_graph(settings: Settings):
    gitlab = GitLabClient(settings.gitlab_url, settings.gitlab_token)

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

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
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
        branch = create_issue_branch(repo_dir, state["issue_iid"], suffix=stamp)
        if target_branch == branch:
            raise RuntimeError(
                "Target branch cannot be the same as the AI source branch"
            )
        return {
            "run_dir": str(run_dir),
            "repo_dir": str(repo_dir),
            "branch": branch,
            "target_branch": target_branch,
            "fix_attempts": 0,
            "check_history": [],
            "status": "workspace_prepared",
        }

    def run_agent(state: WorkflowState) -> dict:
        result = run_openhands(
            repo_dir=Path(state["repo_dir"]),
            issue=state["issue"],
            project=state["project"],
            branch=state["branch"],
            settings=settings,
        )
        return {"agent_result": result, "status": "implementation_completed"}

    def run_checks(state: WorkflowState) -> dict:
        round_number = len(state.get("check_history", [])) + 1
        report = run_build_and_test(
            repo_dir=Path(state["repo_dir"]),
            run_dir=Path(state["run_dir"]),
            setup_command=settings.setup_command,
            build_command=settings.build_command,
            test_command=settings.test_command,
            timeout_seconds=settings.check_timeout_seconds,
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
        result = run_openhands(
            repo_dir=Path(state["repo_dir"]),
            issue=state["issue"],
            project=state["project"],
            branch=state["branch"],
            settings=settings,
            repair_context=format_check_failures(state.get("last_check_report") or {}),
            repair_attempt=attempt,
        )
        return {
            "agent_result": result,
            "fix_attempts": attempt,
            "status": f"repair_attempt_{attempt}_completed",
        }

    def publish_changes(state: WorkflowState) -> dict:
        commit_sha = None
        try:
            commit_sha = commit_and_push_branch(
                Path(state["repo_dir"]),
                branch=state["branch"],
                token=settings.gitlab_token,
                author_name=settings.git_author_name,
                author_email=settings.git_author_email,
                commit_message=f"AI: implement issue #{state['issue_iid']}",
                git_username=settings.gitlab_git_username,
            )
            existing = gitlab.find_open_merge_request(
                state["project_id"], state["branch"], state["target_branch"]
            )
            if existing:
                merge_request = existing
                created = False
            else:
                issue = state["issue"]
                title = (
                    state.get("mr_title")
                    or f"Resolve #{state['issue_iid']}: {issue.get('title', 'GitLab issue')}"
                )
                description = state.get("mr_description") or _mr_description(
                    state, commit_sha
                )
                merge_request = gitlab.create_merge_request(
                    state["project_id"],
                    source_branch=state["branch"],
                    target_branch=state["target_branch"],
                    title=title,
                    description=description,
                    draft=bool(state.get("draft_mr", False)),
                )
                created = True
            safe_mr = {
                key: merge_request.get(key)
                for key in (
                    "id",
                    "iid",
                    "web_url",
                    "source_branch",
                    "target_branch",
                    "title",
                    "state",
                )
            }
            safe_mr["created"] = created
            return {
                "commit_sha": commit_sha,
                "merge_request": safe_mr,
                "status": "published",
            }
        except (GitOperationError, GitLabAPIError, RuntimeError) as exc:
            return {
                "commit_sha": commit_sha,
                "publish_error": str(exc),
                "status": "publish_failed",
            }

    def collect_diff(state: WorkflowState) -> dict:
        summary = get_working_tree_summary(Path(state["repo_dir"]))
        if state.get("status") == "published":
            final_status = "published"
        elif state.get("status") == "publish_failed":
            final_status = "publish_failed"
        elif state.get("checks_passed"):
            final_status = "checks_passed"
        elif not state.get("checks_configured"):
            final_status = "checks_not_configured"
        else:
            final_status = "checks_failed"
        return {"working_tree_summary": summary, "status": final_status}

    def route_after_checks(state: WorkflowState) -> str:
        if state.get("checks_passed"):
            return "publish_changes"
        if not state.get("checks_configured"):
            # return "collect_diff"
            return "publish_changes" # publish for test
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
        ("publish_changes", publish_changes),
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
            "publish_changes": "publish_changes",
            "collect_diff": "collect_diff",
        },
    )
    builder.add_edge("repair_agent", "run_checks")
    builder.add_edge("publish_changes", "collect_diff")
    builder.add_edge("collect_diff", END)
    return builder.compile()
