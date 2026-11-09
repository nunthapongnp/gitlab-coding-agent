from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from src.config import Settings
from src.gitlab_client import GitLabAPIError
from src.git_ops import GitOperationError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitLab issue → LangGraph → OpenHands/Ollama coding agent"
    )
    parser.add_argument("--project", required=True, help="GitLab project ID or namespace/project path")
    parser.add_argument("--issue", required=True, type=int, help="GitLab issue IID")
    parser.add_argument("--gitlab-url", help="GitLab instance URL; overrides GITLAB_URL")
    parser.add_argument("--output-dir", type=Path, default=Path("runs"), help="Folder for disposable clones and run reports")
    parser.add_argument("--target-branch", help="MR target branch; defaults to the GitLab project's default branch")
    parser.add_argument("--mr-title", help="Override generated merge request title")
    parser.add_argument("--mr-description", help="Override generated merge request description")
    parser.add_argument("--draft-mr", action="store_true", help="Create the merge request as Draft")
    parser.add_argument("--setup-command", help="Optional dependency/setup command before each validation round")
    parser.add_argument("--build-command", help="Override auto-detected build command")
    parser.add_argument("--test-command", help="Override auto-detected test command")
    parser.add_argument("--max-fix-attempts", type=int, help="Maximum repair loops after initial implementation")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    # Application boundary: malformed environment values and invalid arguments
    # produce an actionable diagnostic and exit code 2, never a traceback.
    try:
        settings = Settings.from_env(output_dir=args.output_dir)
        if args.max_fix_attempts is not None and args.max_fix_attempts < 0:
            raise ValueError("--max-fix-attempts must be an integer >= 0")
        overrides = {}
        if args.gitlab_url:
            overrides["gitlab_url"] = args.gitlab_url.rstrip("/")
        if args.setup_command is not None:
            overrides["setup_command"] = args.setup_command.strip() or None
        if args.build_command is not None:
            overrides["build_command"] = args.build_command.strip() or None
        if args.test_command is not None:
            overrides["test_command"] = args.test_command.strip() or None
        if args.max_fix_attempts is not None:
            overrides["max_fix_attempts"] = args.max_fix_attempts
        if overrides:
            settings = replace(settings, **overrides)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        from src.workflow import build_graph

        graph = build_graph(settings)
        result = graph.invoke(
            {
                "project_id": args.project,
                "issue_iid": args.issue,
                "target_branch": args.target_branch,
                "mr_title": args.mr_title,
                "mr_description": args.mr_description,
                "draft_mr": args.draft_mr,
            },
            config={"recursion_limit": max(30, 15 + settings.max_fix_attempts * 5)},
        )
    except (GitLabAPIError, GitOperationError, RuntimeError, ValueError, ImportError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    issue = result.get("issue", {})
    project = result.get("project", {})
    print("\n=== GitLab issue ===")
    print(f"Project : {project.get('path_with_namespace', args.project)}")
    print(f"Issue   : #{issue.get('iid', args.issue)} — {issue.get('title', '(no title)')}")
    print(f"URL     : {issue.get('web_url', '(not provided)')}")
    print(f"Workspace branch : {result.get('branch')}")
    print(f"Target  : {result.get('target_branch')}")

    print("\n=== Workflow result ===")
    print(f"Status          : {result.get('status')}")
    print(f"Repair attempts : {result.get('fix_attempts', 0)} / {settings.max_fix_attempts}")
    print(f"Workspace       : {result.get('repo_dir')}")
    print(f"Publication     : {'yes' if result.get('published') else 'nothing was published (publication is disabled during this transition)'}")

    print("\n=== Build / test validation ===")
    for round_report in result.get("check_history", []):
        print(f"Round {round_report.get('round')}: {'PASSED' if round_report.get('passed') else 'FAILED'}")
        for check_name, check in (round_report.get("checks") or {}).items():
            print(f"  {check_name}: {check.get('status')} | {check.get('command') or '(not configured)'}")
            if check.get("log_file"):
                print(f"    log: {check.get('log_file')}")

    print("\n=== Git changes ===")
    print(result.get("working_tree_summary", "(not available)"))

    run_dir = Path(result.get("run_dir", args.output_dir))
    report_path = run_dir / "poc-result.json"
    safe_result = {
        "status": result.get("status"),
        "published": bool(result.get("published")),
        "project": project.get("path_with_namespace"),
        "issue_iid": issue.get("iid", args.issue),
        "issue_title": issue.get("title"),
        "issue_url": issue.get("web_url"),
        "branch": result.get("branch"),
        "target_branch": result.get("target_branch"),
        "repo_dir": result.get("repo_dir"),
        "agent_result": result.get("agent_result"),
        "working_tree_summary": result.get("working_tree_summary"),
        "fix_attempts": result.get("fix_attempts", 0),
        "max_fix_attempts": settings.max_fix_attempts,
        "checks_passed": result.get("checks_passed", False),
        "checks_configured": result.get("checks_configured", False),
        "check_history": result.get("check_history", []),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(safe_result, indent=2), encoding="utf-8")
    print(f"\nSaved run summary: {report_path}")

    if result.get("status") == "checks_passed":
        print(
            "\nVALIDATION PASSED: build, tests, and Git diff checks passed locally, "
            "and the local report was saved."
        )
        print("Nothing was published: branch push and merge request creation are disabled during this transition.")
        return 0
    if result.get("status") == "checks_not_configured":
        print("\nVALIDATION INCOMPLETE: build and/or test command was not detected/configured.")
        print("Set BUILD_COMMAND and TEST_COMMAND or pass --build-command / --test-command.")
    else:
        print("\nVALIDATION FAILED: one or more checks still fail after the configured repair attempts.")
    print("Nothing was published; do not publish or merge this branch until the validation issue is resolved.")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
