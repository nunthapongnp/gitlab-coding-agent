from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from src.config import Settings


# Obsolete branch/MR-creation controls: the agent no longer creates branches
# or merge requests, so these flags get an actionable removal error instead of
# a generic "unrecognized arguments" one.
OBSOLETE_MR_CREATION_FLAGS = (
    "--target-branch",
    "--mr-title",
    "--mr-description",
    "--draft-mr",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="GitLab issue → label-routed coding agent (Plan/Execute preparation)"
    )
    parser.add_argument("--project", required=True, help="GitLab project ID or namespace/project path")
    parser.add_argument("--issue", required=True, type=int, help="GitLab issue IID")
    parser.add_argument("--gitlab-url", help="GitLab instance URL; overrides GITLAB_URL")
    parser.add_argument("--output-dir", type=Path, default=Path("runs"), help="Folder for run diagnostics and reports")
    parser.add_argument(
        "--branch",
        help="Plan-only: inspection baseline branch override; rejected for Execute",
    )
    parser.add_argument("--setup-command", help="Optional dependency/setup command hint")
    parser.add_argument("--build-command", help="Optional build command hint")
    parser.add_argument("--test-command", help="Optional unit test command hint")
    parser.add_argument("--max-fix-attempts", type=int, help="Maximum repair loops after initial implementation")
    args, unknown = parser.parse_known_args()
    for flag in OBSOLETE_MR_CREATION_FLAGS:
        for token in unknown:
            if token == flag or token.startswith(flag + "="):
                parser.error(
                    f"{flag} was removed: the agent no longer creates branches or "
                    "merge requests and never merges. Create your own source "
                    "branch and one merge request in the Issue project, label it "
                    "agent::implement, and retry."
                )
    if unknown:
        parser.error(f"unrecognized arguments: {' '.join(unknown)}")
    return args


def _load_settings(args: argparse.Namespace) -> Settings:
    settings = Settings.from_env(output_dir=args.output_dir)
    if args.max_fix_attempts is not None and args.max_fix_attempts < 0:
        raise ValueError("--max-fix-attempts must be an integer >= 0")
    if args.branch is not None and not args.branch.strip():
        raise ValueError("--branch must be a non-empty branch name")
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
    return settings


def _save_report(
    args: argparse.Namespace,
    settings: Settings,
    *,
    mode: str | None,
    status: str | None,
    message: str | None = None,
    extra: dict | None = None,
) -> Path:
    report = {
        "mode": mode,
        "status": status,
        "message": message,
        "project": args.project,
        "issue_iid": args.issue,
        "branch_override": args.branch,
        "max_fix_attempts": settings.max_fix_attempts,
        "execution_max_runtime_seconds": settings.execution_max_runtime_seconds,
    }
    if extra:
        report.update(extra)
    report_path = args.output_dir / "poc-result.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nSaved run summary: {report_path}")
    return report_path


def main() -> int:
    args = parse_args()

    # Application boundary: malformed environment values and invalid arguments
    # produce an actionable diagnostic and exit code 2, never a traceback.
    try:
        settings = _load_settings(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from src.workflow import (
        RoutingRejection,
        WorkflowUnavailableError,
        run_routed_workflow,
    )

    # Label routing: validate the Issue state, select Plan or Execute, and
    # dispatch. Rejected invocations and unavailable downstream behavior have
    # distinct exit codes and structured statuses; nothing is edited or
    # published on rejection, and no labels are written in this transition.
    try:
        result = run_routed_workflow(
            settings,
            project_id=args.project,
            issue_iid=args.issue,
            branch_override=args.branch,
        )
    except RoutingRejection as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        _save_report(args, settings, mode=exc.mode, status=exc.status, message=str(exc))
        return 2
    except WorkflowUnavailableError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # Structured outcome stays visible on stdout so automation can
        # distinguish the routing result from the failure detail.
        print(f"Routing outcome: mode={exc.mode} status={exc.status}")
        _save_report(args, settings, mode=exc.mode, status=exc.status, message=str(exc))
        return 1
    except (RuntimeError, ValueError, ImportError) as exc:
        # GitLabAPIError/GitOperationError and other infrastructure failures.
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # Fully successful workflow plus finalization (not reachable yet in this
    # transition: the mode graphs fail clearly until later tickets).
    issue = result.get("issue", {})
    print("\n=== Routing ===")
    print(f"Mode    : {result.get('mode')}")
    print(f"Issue   : #{issue.get('iid', args.issue)} — {issue.get('title', '(no title)')}")
    print(f"Status  : {result.get('status')}")
    _save_report(
        args,
        settings,
        mode=result.get("mode"),
        status=result.get("status"),
        message=result.get("message"),
        extra={"issue_iid": issue.get("iid", args.issue)},
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
