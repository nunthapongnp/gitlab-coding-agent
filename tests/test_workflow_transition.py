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
    """Recording agent runner; never imports OpenHands or talks to a model.

    With ``change_file`` set, each invocation writes a file into the clone so
    the workflow has working-tree changes to preserve as a local commit.
    """

    def __init__(self, summary: str = "Agent implemented the issue.", change_file: str | None = None):
        self.summary = summary
        self.change_file = change_file
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> str:
        self.calls.append(kwargs)
        if self.change_file:
            (Path(kwargs["repo_dir"]) / self.change_file).write_text(
                "agent change\n", encoding="utf-8"
            )
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


def make_check_report(round_number: int, *, configuration_complete: bool) -> dict:
    """A check report shaped like run_build_and_test's output.

    ``configuration_complete=False`` yields the report produced when build/test
    commands are unset; ``True`` yields a fully passing report. No commands
    actually run.
    """
    if configuration_complete:
        commands: dict[str, str | None] = {"setup": None, "build": "echo build", "test": "echo test"}
        specs = {
            "setup": ("not_requested", None, 0.0, "No SETUP_COMMAND configured."),
            "build": ("passed", "echo build", 0.01, ""),
            "test": ("passed", "echo test", 0.01, ""),
            "git_diff_check": ("passed", "git diff --check", 0.01, ""),
            "git_cached_diff_check": ("passed", "git diff --cached --check", 0.01, ""),
        }
    else:
        commands = {"setup": None, "build": None, "test": None}
        specs = {
            "setup": ("not_requested", None, 0.0, "No SETUP_COMMAND configured."),
            "build": ("not_configured", None, 0.0, "No build command detected."),
            "test": ("not_configured", None, 0.0, "No test command detected."),
            "git_diff_check": ("not_configured", None, 0.0, "No git_diff_check command detected."),
            "git_cached_diff_check": (
                "not_configured",
                None,
                0.0,
                "No git_cached_diff_check command detected.",
            ),
        }

    checks = {
        name: {
            "name": name,
            "command": command,
            "status": status,
            "exit_code": 0 if status == "passed" else None,
            "duration_seconds": duration,
            "log_file": None,
            "output_excerpt": excerpt,
        }
        for name, (status, command, duration, excerpt) in specs.items()
    }
    return {
        "round": round_number,
        "configuration_complete": configuration_complete,
        "commands": commands,
        "checks": checks,
        "passed": configuration_complete,
    }


def incomplete_report(round_number: int) -> dict:
    """A report like run_build_and_test produces when build/test are unset."""
    return make_check_report(round_number, configuration_complete=False)


def passing_report(round_number: int) -> dict:
    """A fully-configured, fully-passing report (no commands actually run)."""
    return make_check_report(round_number, configuration_complete=True)


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
        graph, gitlab, agent, verification = build_injected_graph(
            settings, origin_repo=origin_repo
        )

        result = invoke_workflow(graph)

        # Truthful terminal state: incomplete verification is not success.
        assert result["status"] == "checks_not_configured"
        assert result.get("published") is False
        assert "merge_request" not in result
        assert "publish_error" not in result
        # The fake agent makes no working-tree changes, so no local commit.
        assert not result.get("commit_sha")

        # Only the issue read happens on the transport; no publication calls.
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
        graph, gitlab, agent, verification = build_injected_graph(
            settings,
            origin_repo=origin_repo,
            verification=FakeVerificationRunner(passing_report),
        )

        result = invoke_workflow(graph)

        assert result["status"] == "checks_passed"
        assert result.get("published") is False
        assert gitlab.publication_calls() == []
        assert branch_names(origin_repo) == {"main"}
        assert isinstance(result["working_tree_summary"], str)
        assert result["working_tree_summary"]

    def test_local_commit_preserves_changes_without_publishing(
        self, tmp_path: Path, origin_repo: Path
    ):
        settings = make_settings(tmp_path / "runs")
        agent = FakeAgentRunner(change_file="feature.py")
        graph, gitlab, _, verification = build_injected_graph(
            settings,
            origin_repo=origin_repo,
            agent_runner=agent,
            verification=FakeVerificationRunner(passing_report),
        )

        result = invoke_workflow(graph)

        assert result["status"] == "checks_passed"
        assert result.get("published") is False
        commit_sha = result["commit_sha"]
        assert commit_sha

        # The local commit exists in the cloned repo's history at HEAD.
        clone = clone_dir(settings.output_dir)
        assert head_sha(clone) == commit_sha
        assert head_sha(clone) != head_sha(origin_repo)
        subject = _run_git("log", "-1", "--format=%s", cwd=clone)
        assert "issue #7" in subject
        assert "not published" in subject

        # Nothing reached the remote: same branches, no agent refs, no sha.
        assert branch_names(origin_repo) == {"main"}
        tracked = _run_git("ls-remote", str(origin_repo))
        assert commit_sha not in tracked
        assert "refs/heads/agent/" not in tracked
        assert gitlab.publication_calls() == []
        assert [name for name, _ in gitlab.calls] == ["get_project", "get_issue"]

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
        graph, gitlab, agent, verification = build_injected_graph(
            settings,
            origin_repo=origin_repo,
            agent_runner=agent,
            verification=verification,
        )

        result = invoke_workflow(graph)

        assert result["status"] == "checks_failed"
        assert result.get("published") is False
        assert agent.repair_invocations == 1
        assert len(verification.calls) == 2
        # No publication outcome of any kind is exposed by the result.
        assert "merge_request" not in result
        assert "publish_error" not in result
        assert gitlab.publication_calls() == []


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
        transports: list[FakeGitLabTransport] = []

        def fake_build_graph(settings_from_main, **kwargs):
            # The real build_graph is replaced by an injected variant; main
            # must run this graph unmodified and report its outcome truthfully.
            assert settings_from_main.output_dir == settings.output_dir
            graph, gitlab, agent, verification = build_injected_graph(
                settings_from_main, origin_repo=origin_repo
            )
            transports.append(gitlab)
            return graph

        code, out, err = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), graph_factory=fake_build_graph
        )

        assert code == 3
        assert "SUCCESS" not in out
        assert "VALIDATION INCOMPLETE" in out
        assert "BUILD_COMMAND" in out
        assert "published" in out.lower()

        # Observed transport calls: only the issue read, never publication.
        transport = transports[0]
        assert transport.calls == [
            ("get_project", ("group/demo",)),
            ("get_issue", ("group/demo", 7)),
        ]
        assert transport.publication_calls() == []

        report_path = next((tmp_path / "runs").rglob("poc-result.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["status"] == "checks_not_configured"
        assert report["published"] is False
        assert "merge_request" not in report
        # The fake agent makes no working-tree changes, so no local commit.
        assert report["commit_sha"] is None

    def test_main_exit_zero_only_for_full_local_validation(
        self,
        tmp_path: Path,
        origin_repo: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
    ):
        transports: list[FakeGitLabTransport] = []

        def fake_build_graph(settings_from_main, **kwargs):
            graph, gitlab, agent, verification = build_injected_graph(
                settings_from_main,
                origin_repo=origin_repo,
                agent_runner=FakeAgentRunner(change_file="feature.py"),
                verification=FakeVerificationRunner(passing_report),
            )
            transports.append(gitlab)
            return graph

        code, out, err = self._run_main(
            monkeypatch, capsys, self.base_argv(tmp_path), graph_factory=fake_build_graph
        )

        assert code == 0
        assert "SUCCESS" not in out
        assert "VALIDATION PASSED" in out
        assert "Nothing was published" in out

        # Observed transport calls: only the issue read, never publication.
        transport = transports[0]
        assert transport.calls == [
            ("get_project", ("group/demo",)),
            ("get_issue", ("group/demo", 7)),
        ]
        assert transport.publication_calls() == []

        report_path = next((tmp_path / "runs").rglob("poc-result.json"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report["status"] == "checks_passed"
        assert report["published"] is False
        # The local commit is recorded in the report, on stdout, and in the
        # clone's history — while nothing was pushed.
        commit_sha = report["commit_sha"]
        assert commit_sha
        assert commit_sha in out
        assert head_sha(Path(report["repo_dir"])) == commit_sha

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

    @pytest.mark.parametrize(
        ("env_name", "env_value", "minimum_hint"),
        [
            ("OPENHANDS_MAX_ITERATIONS", "0", ">= 1"),
            ("CHECK_TIMEOUT_SECONDS", "0", ">= 1"),
            ("MAX_FIX_ATTEMPTS", "-1", ">= 0"),
        ],
    )
    def test_below_minimum_env_integer_is_rejected_not_clamped(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture,
        env_name: str,
        env_value: str,
        minimum_hint: str,
    ):
        monkeypatch.setenv(env_name, env_value)

        code, out, err = self._run_main(monkeypatch, capsys, self.base_argv(tmp_path))

        assert code == 2
        assert "ERROR:" in err
        assert env_name in err
        assert minimum_hint in err
        assert f"got '{env_value}'" in err
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
