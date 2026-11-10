from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO


def positive_iid(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer IID") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer IID")
    return number


def project_identifier(value: str) -> str:
    value = value.strip()
    if not value or (value.isdecimal() and int(value) < 1):
        raise argparse.ArgumentTypeError("must be a Project ID or namespace/project path")
    return value


class InvocationParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(f"{message}. Use --help for invocation examples.")


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = InvocationParser(description="Manually explore or implement a GitLab Issue")
    operations = parser.add_subparsers(dest="operation", required=True)
    for operation in ("explore", "implement"):
        command = operations.add_parser(operation)
        command.add_argument("--project", required=True, type=project_identifier,
                             help="GitLab Project ID or namespace/project path")
        command.add_argument("--issue", required=True, type=positive_iid, help="Positive Issue IID")
        if operation == "implement":
            command.add_argument("--mr", required=True, type=positive_iid, help="Existing user-created MR IID")
        command.add_argument("--gitlab-url", help="Override GITLAB_URL")
        command.add_argument("--output-dir", type=Path, default=Path("runs"))
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    services=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    from src.workflow import Application, OperationInput, OperationOutcome, OutcomeStatus

    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    try:
        args = parse_args(argv)
    except ValueError as exc:
        outcome = OperationOutcome(OutcomeStatus.INVALID_INVOCATION, str(exc))
    except SystemExit as exc:
        return int(exc.code or 0)
    else:
        outcome = Application(services=services, environ=environ).invoke(
            OperationInput(args.operation, args.project, args.issue, getattr(args, "mr", None)),
            output_dir=args.output_dir,
            gitlab_url=args.gitlab_url,
        )
    stream = stdout if outcome.succeeded else stderr
    prefix = "" if outcome.succeeded else "ERROR "
    print(f"{prefix}{outcome.status.value}: {outcome.message}", file=stream)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
