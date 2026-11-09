"""Workflow transition tests (ticket 01).

Seams under test (per docs/p0/specs/README.md "Shared testing seam"):
- Workflow seam: ``build_graph(settings, gitlab=..., agent_runner=...,
  verification_runner=..., clock=...)`` compiled graph invocation with
  controlled transports.
- Application boundary: ``main()`` return code and stderr/stdout messages.

Ordinary tests never call real GitLab or a model: the GitLab transport is an
in-memory fake, the agent runner is a recording fake, and the verification
runner returns a configurable report.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

import src.workflow as workflow_module
from src.config import Settings
from src.workflow import build_graph


FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def _run_git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=True,
    )
    return completed.stdout.strip()


class FakeGitLabTransport:
    """In-memory GitLab transport that records every call and never hits HTTP."""

    def __init__(self, project: dict, issue: dict):
        self.project = project
        self.issue = issue
        self.calls: list[tuple[str, tuple]] = []

    def get_project(self, project_id):
        self.calls.append(("get_project", (project_id,)))
        return dict(self.project)

    def get_issue(self, project_id, issue_iid):
        self.calls.append(("get_issue", (project_id, issue_iid)))
        return dict(self.issue)

    def find_open_merge_request(self, project_id, source_branch, target_branch):
        self.calls.append(
            ("find_open_merge_request", (project_id, source_branch, target_branch))
        )
        return None

    def create_merge_request(self, project_id, **kwargs):
        self.calls.append(("create_merge_request", (project_id, kwargs)))
        raise AssertionError("create_merge_request must not be called in transition")

    def publication_calls(self) -> list[str]:
        return [
            name
            for name, _ in self.calls
            if name in ("find_open_merge_request", "create_merge_request")
        ]


class FakeAgentRunner:
    """Recording agent runner; never imports OpenHands or talks to a model."""

    def __init__(self, summary: str = "Agent implemented the issue."):
        self.summary = summary
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        return self.summary

    @property
    def repair_invocations(self) -> int:
        return sum(1 for call in self.calls if call.get("repair_attempt"))


class FakeVerificationRunner:
    """Verification runner returning a configurable report; runs no commands."""

    def __init__(self, report_factory):
        self.report_factory = report_factory
        self.calls: list[dict] = []

    def __call__(self, *, repo_dir, run_dir, round_number) -> dict:
        self.calls.append(
            {"repo_dir": repo_dir, "run_dir": run_dir, "round_number": round_number}
        )
        return self.report_factory(round_number)


def incomplete_report(round_number: int) -> dict:
    """A report like run_build_and_test produces when build/test are unset."""
    def check(name: str) -> dict:
        return {
            "name": name,
            "command": None,
            "status": "not_configured",
            "exit_code": None,
            "duration_seconds": 0.0,
            "log_file": None,
            "output_excerpt": f"No {name} command detected.",
        }

    return {
        "round": round_number,
        "configuration_complete": False,
        "commands": {"setup": None, "build": None, "test": None},
        "checks": {
            "setup": {
                "name": "setup",
                "command": None,
                "status": "not_requested",
                "exit_code": None,
                "duration_seconds": 0.0,
                "log_file": None,
                "output_excerpt": "No SETUP_COMMAND configured.",
            },
            "build": check("build"),
            "test": check("test"),
            "git_diff_check": check("git_diff_check"),
            "git_cached_diff_check": check("git_cached_diff_check"),
        },
        "passed": False,
    }


def passing_report(round_number: int) -> dict:
    """A fully-configured, fully-passing report (no commands actually run)."""
    def check(name: str, command: str) -> dict:
        return {
            "name": name,
            "command": command,
            "status": "passed",
            "exit_code": 0,
            "duration_seconds": 0.01,
            "log_file": None,
            "output_excerpt": "",
        }

    return {
        "round": round_number,
        "configuration_complete": True,
        "commands": {"setup": None, "build": "echo build", "test": "echo test"},
        "checks": {
            "setup": {
                "name": "setup",
                "command": None,
                "status": "not_requested",
                "exit_code": None,
                "duration_seconds": 0.0,
                "log_file": None,
                "output_excerpt": "No SETUP_COMMAND configured.",
            },
            "build": check("build", "echo build"),
            "test": check("test", "echo test"),
            "git_diff_check": check("git_diff_check", "git diff --check"),
            "git_cached_diff_check": check(
                "git_cached_diff_check", "git diff --cached --check"
            ),
        },
        "passed": True,
    }


def make_settings(output_dir: Path) -> Settings:
    return Settings(
        gitlab_url="http://gitlab.invalid",
        gitlab_token=None,
        gitlab_git_username="agent-bot",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="test-model",
        ollama_api_key="ollama",
        max_iterations=5,
        timeout_seconds=60,
        max_fix_attempts=3,
        check_timeout_seconds=60,
        setup_command=None,
        build_command=None,
        test_command=None,
        git_author_name="agent-bot",
        git_author_email="ai@agent.bot",
        output_dir=output_dir,
    )


def make_project(origin_dir: Path) -> dict:
    return {
        "id": 101,
        "path": "demo",
        "name": "demo",
        "path_with_namespace": "group/demo",
        "default_branch": "main",
        "http_url_to_repo": str(origin_dir),
        "web_url": "http://gitlab.invalid/group/demo",
    }


def make_issue() -> dict:
    return {
        "iid": 7,
        "title": "Add a greeting",
        "description": "Print a greeting on startup.",
        "labels": ["agent::in-progress"],
        "web_url": "http://gitlab.invalid/group/demo/-/issues/7",
    }


@pytest.fixture
def origin_repo(tmp_path: Path) -> Path:
    """A temporary real Git repository standing in for the GitLab remote."""
    origin = tmp_path / "origin" / "demo.git"
    origin.mkdir(parents=True)
    _run_git("init", "--bare", "--initial-branch=main", str(origin))
    seed = tmp_path / "seed"
    seed.mkdir()
    _run_git("init", "--initial-branch=main", str(seed))
    (seed / "README.md").write_text("# demo\n", encoding="utf-8")
    _run_git("add", ".", cwd=seed)
    _run_git(
        "-c", "user.name=seed",
        "-c", "user.email=seed@example.com",
        "commit", "-m", "seed commit",
        cwd=seed,
    )
    _run_git("push", str(origin), "main", cwd=seed)
    return origin


def head_sha(repo: Path) -> str:
    return _run_git("rev-parse", "HEAD", cwd=repo)


def branch_names(repo: Path) -> set[str]:
    output = _run_git("for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=repo)
    return {line for line in output.splitlines() if line.strip()}


def build_injected_graph(
    settings: Settings,
    *,
    origin_repo: Path,
    gitlab: FakeGitLabTransport | None = None,
    agent_runner: FakeAgentRunner | None = None,
    verification: FakeVerificationRunner | None = None,
):
    gitlab = gitlab or FakeGitLabTransport(make_project(origin_repo), make_issue())
    agent_runner = agent_runner or FakeAgentRunner()
    verification = verification or FakeVerificationRunner(incomplete_report)
    graph = build_graph(
        settings,
        gitlab=gitlab,
        agent_runner=agent_runner,
        verification_runner=verification,
        clock=lambda: FIXED_NOW,
    )
    return graph, gitlab, agent_runner, verification


def invoke_workflow(graph, project_id: str = "group/demo", issue_iid: int = 7) -> dict:
    return graph.invoke(
        {
            "project_id": project_id,
            "issue_iid": issue_iid,
            "target_branch": None,
            "mr_title": None,
            "mr_description": None,
            "draft_mr": False,
        },
        config={"recursion_limit": 30},
    )


def clone_dir(output_dir: Path) -> Path:
    matches = sorted(
        p
        for p in output_dir.iterdir()
        if p.is_dir() and (p / "repo").is_dir()
    )
    assert matches, "workflow did not create a workspace under the output dir"
    return matches[0] / "repo"


class TestWorkflowTransition:
    def test_incomplete_verification_never_publishes_or_succeeds(
        self, tmp_path: Path, origin_repo: Path
    ):
        settings = make_settings(tmp_path / "runs")
        gitlab = FakeGitLabTransport(make_project(origin_repo), make_issue())
        agent = FakeAgentRunner()
        verification = FakeVerificationRunner(incomplete_report)
        graph = build_graph(
            settings,
            gitlab=gitlab,
            agent_runner=agent,
            verification_runner=verification,
            clock=lambda: FIXED_NOW,
        )

        result = invoke_workflow(graph)

        # Truthful terminal state: incomplete verification is not success.
        assert result["status"] == "checks_not_configured"
        assert result.get("published") is False
        assert not result.get("merge_request")
        assert not result.get("commit_sha")
        assert not result.get("publish_error")

        # No publication side effects on the transport.
        assert gitlab.publication_calls() == []
        assert [name for name, _ in gitlab.calls] == ["get_project", "get_issue"]

        # No automatic branch creation, push, or repair loop.
        assert agent.repair_invocations == 0
        assert len(agent.calls) == 1
        assert Path(agent.calls[0]["repo_dir"], ".git").is_dir()

        clone = clone_dir(settings.output_dir)
        assert branch_names(origin_repo) == {"main"}
        assert head_sha(origin_repo) == head_sha(clone)
        tracked = _run_git("ls-remote", str(origin_repo))
        assert "refs/heads/agent/" not in tracked

    def test_workspace_is_clone_only_and_lands_in_run_dir(
        self, tmp_path: Path, origin_repo: Path
    ):
        settings = make_settings(tmp_path / "runs")
        graph, gitlab, agent, verification = build_injected_graph(
            settings, origin_repo=origin_repo
        )

        result = invoke_workflow(graph)

        assert result["run_dir"].startswith(str(settings.output_dir))
        assert "20260102T030405" in Path(result["run_dir"]).name
        assert result["branch"] == "main"
        assert result["target_branch"] == "main"
        assert verification.calls[0]["repo_dir"] == Path(result["repo_dir"])

    def test_fully_passing_verification_still_publishes_nothing(
        self, tmp_path: Path, origin_repo: Path
    ):
        settings = make_settings(tmp_path / "runs")
        gitlab = FakeGitLabTransport(make_project(origin_repo), make_issue())
        agent = FakeAgentRunner()
        verification = FakeVerificationRunner(passing_report)
        graph = build_graph(
            settings,
            gitlab=gitlab,
            agent_runner=agent,
            verification_runner=verification,
            clock=lambda: FIXED_NOW,
        )

        result = invoke_workflow(graph)

        assert result["status"] == "checks_passed"
        assert result.get("published") is False
        assert gitlab.publication_calls() == []
        assert branch_names(origin_repo) == {"main"}
        assert isinstance(result["working_tree_summary"], str)
        assert result["working_tree_summary"]

    def test_failed_verification_repairs_within_budget_then_stops(
        self, tmp_path: Path, origin_repo: Path
    ):
        settings = replace(make_settings(tmp_path / "runs"), max_fix_attempts=1)

        def failing_report(round_number: int) -> dict:
            report = passing_report(round_number)
            report["passed"] = False
            report["checks"]["test"]["status"] = "failed"
            report["checks"]["test"]["exit_code"] = 1
            return report

        verification = FakeVerificationRunner(failing_report)
        agent = FakeAgentRunner()
        graph = build_graph(
            settings,
            gitlab=FakeGitLabTransport(make_project(origin_repo), make_issue()),
            agent_runner=agent,
            verification_runner=verification,
            clock=lambda: FIXED_NOW,
        )

        result = invoke_workflow(graph)

        assert result["status"] == "checks_failed"
        assert result.get("published") is False
        assert agent.repair_invocations == 1
        assert len(verification.calls) == 2
        assert gitlab_publication_absent(graph, result)


def gitlab_publication_absent(graph, result: dict) -> bool:
    """The compiled transition graph exposes no publication outcome at all."""
    return not result.get("merge_request") and not result.get("commit_sha")


class TestApplicationBoundary:
    def _run_main(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        argv: list[str],
        *,
        graph_factory=None,
    ) -> tuple[int, str, str]:
        if graph_factory is not None:
            monkeypatch.setattr(workflow_module, "build_graph", graph_factory)
        monkeypatch.setattr(sys, "argv", argv)
        import main as main_module

        code = main_module.main()
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    def base_argv(self, tmp_path: Path) -> list[str]:
        return [
            "main.py",
            "--project",
            "group/demo",
            "--issue",
            "7",
            "--output-dir",
            str(tmp_path / "runs"),
        ]

    def test_main_reports_not_success_for_incomplete_verification(
        self,
        tmp_path: Path,
        origin_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        settings = make_settings(tmp_path / "runs")

        def fake_build_graph(settings_from_main, **kwargs):
            # The real build_graph is replaced by an injected variant; main
            # must run this graph unmodified and report its outcome truthfully.
            assert settings_from_main.output_dir == settings.output_dir
            return build_graph(
                settings_from_main,
                gitlab=FakeGitLabTransport(make_project(origin_repo), make_issue()),
                agent_runner=FakeAgentRunner(),
                verification_runner=FakeVerificationRunner(incomplete_report),
                clock=lambda: FIXED_NOW,
            )

        code, out, err = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), graph_factory=fake_build_graph
        )

        assert code == 3
        assert "SUCCESS" not in out
        assert "VALIDATION INCOMPLETE" in out
        assert "BUILD_COMMAND" in out
        assert "published" in out.lower()

        report_path = next((tmp_path / "runs").rglob("poc-result.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["status"] == "checks_not_configured"
        assert report["published"] is False
        assert "merge_request" not in report
        assert "commit_sha" not in report

    def test_main_exit_zero_only_for_full_local_validation(
        self,
        tmp_path: Path,
        origin_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        def fake_build_graph(settings_from_main, **kwargs):
            return build_graph(
                settings_from_main,
                gitlab=FakeGitLabTransport(make_project(origin_repo), make_issue()),
                agent_runner=FakeAgentRunner(),
                verification_runner=FakeVerificationRunner(passing_report),
                clock=lambda: FIXED_NOW,
            )

        code, out, err = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), graph_factory=fake_build_graph
        )

        assert code == 0
        assert "SUCCESS" not in out
        assert "VALIDATION PASSED" in out
        assert "Nothing was published" in out

    def test_malformed_env_integer_gives_actionable_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        monkeypatch.setenv("OPENHANDS_MAX_ITERATIONS", "not-a-number")

        code, out, err = self._run_main(monkeypatch, capsys, self.base_argv(tmp_path))

        assert code == 2
        assert "ERROR:" in err
        assert "OPENHANDS_MAX_ITERATIONS" in err
        assert "Traceback" not in err

    def test_negative_max_fix_attempts_gives_actionable_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        argv = self.base_argv(tmp_path) + ["--max-fix-attempts", "-1"]

        code, out, err = self._run_main(monkeypatch, capsys, argv)

        assert code == 2
        assert "ERROR:" in err
        assert "--max-fix-attempts" in err
        assert "Traceback" not in err
