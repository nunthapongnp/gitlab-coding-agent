from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CheckCommand:
    name: str
    command: str


def _python_executable(repo_dir: Path) -> str:
    candidates = [
        repo_dir / ".venv" / "bin" / "python",
        repo_dir / "venv" / "bin" / "python",
        repo_dir / ".venv" / "Scripts" / "python.exe",
        repo_dir / "venv" / "Scripts" / "python.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return sys.executable


def _node_package_manager(repo_dir: Path) -> str:
    if (repo_dir / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_dir / "yarn.lock").exists():
        return "yarn"
    return "npm"


def detect_check_commands(
    repo_dir: Path,
    *,
    build_command: str | None = None,
    test_command: str | None = None,
) -> dict[str, str | None]:
    repo_dir = repo_dir.resolve()
    detected_build: str | None = None
    detected_test: str | None = None

    package_json = repo_dir / "package.json"
    if package_json.is_file():
        try:
            scripts = (json.loads(package_json.read_text(encoding="utf-8")).get("scripts") or {})
        except (OSError, json.JSONDecodeError, AttributeError):
            scripts = {}
        manager = _node_package_manager(repo_dir)
        if "build" in scripts:
            detected_build = f"{manager} run build"
        if "test" in scripts:
            detected_test = f"{manager} test"
    elif (repo_dir / "go.mod").is_file():
        detected_build, detected_test = "go build ./...", "go test ./..."
    elif (repo_dir / "Cargo.toml").is_file():
        detected_build, detected_test = "cargo check --all-targets", "cargo test"
    elif (repo_dir / "pom.xml").is_file():
        mvn = "./mvnw" if (repo_dir / "mvnw").exists() else "mvn"
        detected_build, detected_test = f"{mvn} -B -DskipTests package", f"{mvn} -B test"
    elif any(repo_dir.glob("*.gradle")) or any(repo_dir.glob("*.gradle.kts")) or (repo_dir / "gradlew").is_file():
        gradle = "./gradlew" if (repo_dir / "gradlew").is_file() else "gradle"
        detected_build, detected_test = f"{gradle} assemble", f"{gradle} test"
    elif any(repo_dir.glob("*.sln")) or any(repo_dir.glob("*.csproj")):
        detected_build, detected_test = "dotnet build", "dotnet test --no-build"
    elif (repo_dir / "pyproject.toml").is_file() or (repo_dir / "setup.py").is_file() or (repo_dir / "requirements.txt").is_file():
        python = shlex.quote(_python_executable(repo_dir))
        exclude = r"(^|[\\/])(\.venv|venv|\.git|build|dist)([\\/]|$)"
        detected_build = f"{python} -m compileall -q . -x {shlex.quote(exclude)}"
        has_tests = (
            (repo_dir / "tests").is_dir()
            or (repo_dir / "test").is_dir()
            or any(repo_dir.glob("test_*.py"))
            or any(repo_dir.glob("*_test.py"))
        )
        if has_tests:
            detected_test = f"{python} -m pytest -q"

    makefile = repo_dir / "Makefile"
    if makefile.is_file():
        try:
            targets = set(re.findall(r"(?m)^([A-Za-z0-9_.-]+)\s*:", makefile.read_text(encoding="utf-8", errors="replace")))
            if detected_build is None and "build" in targets:
                detected_build = "make build"
            if detected_test is None and "test" in targets:
                detected_test = "make test"
        except OSError:
            pass

    return {
        "build": build_command.strip() if build_command and build_command.strip() else detected_build,
        "test": test_command.strip() if test_command and test_command.strip() else detected_test,
    }


def _run_one_check(
    repo_dir: Path,
    check: CheckCommand,
    *,
    timeout_seconds: int,
    log_path: Path,
) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "name": check.name,
        "command": check.command,
        "status": "failed",
        "exit_code": None,
        "duration_seconds": 0.0,
        "log_file": str(log_path),
        "output_excerpt": "",
    }
    try:
        args = shlex.split(check.command, posix=(os.name != "nt"))
        if not args:
            raise ValueError("Command is empty")
        env = os.environ.copy()
        # Do not expose publication/model credentials to project-controlled scripts.
        env.pop("GITLAB_TOKEN", None)
        env.pop("LLM_API_KEY", None)
        env.update({"CI": "true", "NONINTERACTIVE": "1", "PYTHONUNBUFFERED": "1"})
        completed = subprocess.run(
            args,
            cwd=str(repo_dir),
            env=env,
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        result["exit_code"] = completed.returncode
        result["status"] = "passed" if completed.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or ""
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", errors="replace")
        output = f"{partial}\n\nCHECK TIMED OUT after {timeout_seconds} seconds."
        result["status"] = "timed_out"
    except FileNotFoundError as exc:
        output = f"Executable not found: {exc}. Check project toolchain/PATH or configure the command."
        result["status"] = "command_not_found"
    except (OSError, ValueError) as exc:
        output = f"Could not execute check: {type(exc).__name__}: {exc}"
        result["status"] = "failed_to_start"

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(output, encoding="utf-8", errors="replace")
    result["duration_seconds"] = round(time.monotonic() - started, 2)
    result["output_excerpt"] = output[-16000:]
    return result


def run_build_and_test(
    repo_dir: Path,
    run_dir: Path,
    *,
    setup_command: str | None = None,
    build_command: str | None,
    test_command: str | None,
    timeout_seconds: int,
    round_number: int,
) -> dict[str, Any]:
    commands = detect_check_commands(repo_dir, build_command=build_command, test_command=test_command)
    checks_dir = run_dir / "checks" / f"round-{round_number:02d}"
    checks_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {}

    setup_failed = False
    if setup_command and setup_command.strip():
        results["setup"] = _run_one_check(
            repo_dir,
            CheckCommand("setup", setup_command.strip()),
            timeout_seconds=timeout_seconds,
            log_path=checks_dir / "setup.log",
        )
        setup_failed = results["setup"]["status"] != "passed"
    else:
        results["setup"] = {
            "name": "setup", "command": None, "status": "not_requested",
            "exit_code": None, "duration_seconds": 0.0, "log_file": None,
            "output_excerpt": "No SETUP_COMMAND configured; dependency setup was not run.",
        }

    for name in ("build", "test"):
        command = commands[name]
        if setup_failed:
            results[name] = {
                "name": name, "command": command, "status": "skipped_setup_failed",
                "exit_code": None, "duration_seconds": 0.0, "log_file": None,
                "output_excerpt": "Skipped because SETUP_COMMAND failed; see checks/setup.log.",
            }
        elif not command:
            results[name] = {
                "name": name, "command": None, "status": "not_configured",
                "exit_code": None, "duration_seconds": 0.0, "log_file": None,
                "output_excerpt": f"No {name} command detected. Set {name.upper()}_COMMAND or pass --{name}-command. This check is required to pass.",
            }
        else:
            results[name] = _run_one_check(
                repo_dir, CheckCommand(name, command), timeout_seconds=timeout_seconds,
                log_path=checks_dir / f"{name}.log",
            )

    for name, command in (
        ("git_diff_check", "git diff --check"),
        ("git_cached_diff_check", "git diff --cached --check"),
    ):
        # Only run Git checks in actual Git working copies.
        results[name] = _run_one_check(
            repo_dir, CheckCommand(name, command), timeout_seconds=min(timeout_seconds, 120),
            log_path=checks_dir / f"{name}.log",
        )

    configured = bool(commands["build"] and commands["test"])
    passed = configured and not setup_failed and all(
        results[name]["status"] == "passed"
        for name in ("build", "test", "git_diff_check", "git_cached_diff_check")
    )
    report = {
        "round": round_number,
        "configuration_complete": configured,
        "commands": {"setup": setup_command, **commands},
        "checks": results,
        "passed": bool(passed),
    }
    (checks_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def format_check_failures(report: dict[str, Any], *, max_chars: int = 22000) -> str:
    lines = [
        f"Build/test validation round {report.get('round')} failed.",
        "Fix the underlying source/configuration defect. Do not hide, skip, weaken, or delete tests to obtain a pass.",
    ]
    checks = report.get("checks") or {}
    for name in ("setup", "build", "test", "git_diff_check", "git_cached_diff_check"):
        check = checks.get(name) or {}
        status = check.get("status", "unknown")
        if status in {"passed", "not_requested"}:
            continue
        lines.extend([
            f"\n[{name.upper()}] {status.upper()}",
            f"Command: {check.get('command') or '(not configured)'}",
            str(check.get("output_excerpt") or "No output captured."),
        ])
    text = "\n".join(lines)
    return text[:max_chars] + "\n...[truncated; full logs saved in run folder]" if len(text) > max_chars else text
