"""Plan-inspection graph tests (ticket 03).

Fully offline: the fixture repository is a local bare Git repo, so the real
``git_ops.clone_repository`` runs without network; the Planner role runs
through a controlled fake transport (no Docker, no LLM, no GitLab).

Seams under test (per docs/p0/specs/README.md "Shared testing seam"):
- ``build_plan_graph(settings, *, operation_factory=None,
  planner_transport=None, clone_fn=None)`` — real inspection graph with
  injected planner transport;
- ``run_routed_workflow(..., gitlab=FakeGitLabTransport, plan_graph=...)``
  dispatch is unaffected.
"""

from __future__ import annotations

import json
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.planner_runner import (
    PlannerRunError,
    PlannerSetupError,
    verify_worker_connectivity,
)
from src.workflow import (
    MODE_PLAN,
    STATUS_INSPECTION_BLOCKED,
    STATUS_INSPECTION_COMPLETE,
    STATUS_INSPECTION_FAILED,
    build_plan_graph,
    run_routed_workflow,
)
from src.workspace import ConcurrentOperationError
from tests.test_workflow_routing import (
    FakeGitLabTransport,
    FakeModeGraph,
    make_issue,
)

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path | None = None, env_extra: dict | None = None):
    import os

    env = os.environ.copy()
    env.update(
        {
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
        }
    )
    if env_extra:
        env.update(env_extra)
    subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, env=env, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


@pytest.fixture
def origin_repo(tmp_path: Path) -> Path:
    """A local bare Git repository used as the fixture 'GitLab' origin."""
    work = tmp_path / "origin-work"
    work.mkdir()
    (work / "src").mkdir()
    (work / "src" / "app.py").write_text("print('hello')\n")
    (work / "docs").mkdir()
    (work / "docs" / "guide.md").write_text("# guide\n")
    (work / "requirements.txt").write_text("requests>=2\n")
    (work / "pyproject.toml").write_text("[project]\nname = 'demo'\n")
    (work / ".gitlab-ci.yml").write_text("test:\n  script: pytest\n")
    _git(["init", "-b", "main"], cwd=work)
    _git(["add", "."], cwd=work)
    _git(["commit", "-m", "initial"], cwd=work)

    bare = tmp_path / "origin.git"
    _git(["clone", "--bare", str(work), str(bare)])
    # A second branch to exercise the explicit --branch baseline.
    _git(["init", "-b", "release/1.4", str(tmp_path / "release-work")])
    release_work = tmp_path / "release-work"
    (release_work / "release.txt").write_text("release notes\n")
    _git(["add", "."], cwd=release_work)
    _git(["commit", "-m", "release branch"], cwd=release_work)
    _git(["push", str(bare), "release/1.4"], cwd=release_work)
    return bare


def make_project_with_origin(origin: Path) -> dict:
    return {
        "id": 101,
        "path": "demo",
        "name": "demo",
        "path_with_namespace": "group/demo",
        "default_branch": "main",
        "http_url_to_repo": str(origin),
        "web_url": "http://gitlab.invalid/group/demo",
    }


def make_inspection_settings(tmp_path: Path, **overrides):
    from src.config import Settings

    defaults = dict(
        gitlab_url="http://gitlab.invalid",
        gitlab_token=None,
        gitlab_git_username="agent-bot",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="ollama/test-model",
        ollama_api_key="ollama",
        max_iterations=5,
        timeout_seconds=60,
        max_fix_attempts=3,
        check_timeout_seconds=60,
        execution_max_runtime_seconds=7200,
        setup_command=None,
        build_command=None,
        test_command=None,
        git_author_name="agent-bot",
        git_author_email="ai@agent.bot",
        output_dir=tmp_path / "runs",
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "artifacts",
        control_root=tmp_path / "control",
        planner_agent_server_url="http://planner.invalid:8000",
        agent_server_api_key="worker-key",
        planner_workspace_mount="/workspace",
    )
    defaults.update(overrides)
    return Settings(**defaults)


class FakePlannerTransport:
    """Controlled stand-in for the remote Agent Server transport.

    Records every call (prompt, worker working dir, operation context) and
    returns a deterministic planner result. Fails on demand to exercise the
    failure-retention paths.
    """

    def __init__(self, *, raise_error: Exception | None = None):
        self.raise_error = raise_error
        self.calls: list[dict] = []

    def run(self, *, prompt, repo_dir, worker_working_dir, operation, settings):
        self.calls.append(
            {
                "prompt": prompt,
                "repo_dir": Path(repo_dir),
                "worker_working_dir": worker_working_dir,
                "operation": operation,
                "settings": settings,
            }
        )
        if self.raise_error is not None:
            raise self.raise_error
        return {
            "status": "completed",
            "summary": "Inspected fixture modules; no writes attempted.",
            "conversation_id": operation.sdk_conversation_id,
            "worker": {"healthy": True, "endpoint": "http://planner.invalid:8000"},
            "transport": "fake",
        }


def run_plan_inspection(origin: Path, settings, transport: FakePlannerTransport, **kwargs):
    graph = build_plan_graph(settings, planner_transport=transport, **kwargs)
    return run_routed_workflow(
        settings,
        project_id="group/demo",
        issue_iid=7,
        gitlab=FakeGitLabTransport(
            make_project_with_origin(origin), make_issue(["agent::plan"])
        ),
        plan_graph=graph,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDefaultBaselineInspection:
    def test_inspection_completes_with_observable_evidence(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        result = run_plan_inspection(origin_repo, settings, transport)

        assert result["status"] == STATUS_INSPECTION_COMPLETE
        assert result["mode"] == MODE_PLAN
        operation = result["operation"]
        # Evidence recorded OUTSIDE the product checkout, under the
        # operation's artifact path.
        evidence_path = Path(result["evidence_path"])
        assert evidence_path.is_file()
        assert evidence_path == (
            Path(operation["artifact_path"]) / "inspection" / "inspection-evidence.json"
        )
        evidence = json.loads(evidence_path.read_text())
        # Observable module/source/configuration evidence from the clone.
        assert evidence["baseline_branch"] == "main"
        assert evidence["inspected_commit"]
        module_paths = {module["path"] for module in evidence["modules"]}
        assert "src" in module_paths and "docs" in module_paths
        assert evidence["source_file_count"] >= 1
        assert any("pyproject.toml" in name for name in evidence["config_files"])
        assert any(".gitlab-ci.yml" in name for name in evidence["config_files"])
        # Toolchain hints are recorded with provenance and UNVERIFIED status.
        assert evidence["verification_hints"]["build"]["status"] == "UNVERIFIED"
        assert evidence["verification_hints"]["build"]["origin"] == "DETECTED"
        assert evidence["planner"]["status"] == "completed"
        assert evidence["planner"]["conversation_id"] == operation["sdk_conversation_id"]

    def test_explicit_branch_baseline_is_used_as_clone_ref(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        result = run_routed_workflow(
            settings,
            project_id="group/demo",
            issue_iid=7,
            branch_override="release/1.4",
            gitlab=FakeGitLabTransport(
                make_project_with_origin(origin_repo), make_issue(["agent::plan"])
            ),
            plan_graph=build_plan_graph(settings, planner_transport=transport),
        )

        assert result["status"] == STATUS_INSPECTION_COMPLETE
        repo_dir = Path(result["workspace_repo_dir"])
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_dir, text=True, capture_output=True, check=True,
        ).stdout.strip()
        assert branch == "release/1.4"
        assert (repo_dir / "release.txt").is_file()
        evidence = json.loads(Path(result["evidence_path"]).read_text())
        assert evidence["baseline_branch"] == "release/1.4"

    def test_fresh_clone_is_per_operation_and_read_only_intent_held(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()
        result = run_plan_inspection(origin_repo, settings, transport)

        repo_dir = Path(result["workspace_repo_dir"])
        assert repo_dir.is_dir()
        # The clone lives on the operation workspace path, not in the checkout.
        checkout = Path(__file__).resolve().parents[1]
        assert checkout not in repo_dir.resolve().parents


class TestTransportInteraction:
    def test_planner_prompt_is_read_only_scoped_and_carries_no_token(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(
            tmp_path, gitlab_token="glpat-secret-do-not-leak"
        )
        transport = FakePlannerTransport()

        result = run_plan_inspection(origin_repo, settings, transport)
        assert result["status"] == STATUS_INSPECTION_COMPLETE

        call = transport.calls[0]
        # Behavioral assertions: the prompt carries the inspected work items
        # (issue data + baseline branch + operation identity), and never the
        # coordinator's GitLab credentials.
        assert "Add a greeting" in call["prompt"]  # issue title
        assert "Print a greeting on startup." in call["prompt"]  # description
        assert "main" in call["prompt"]  # inspected baseline branch
        assert result["operation"]["operation_id"] in call["prompt"]
        assert "glpat-secret-do-not-leak" not in call["prompt"]
        # The worker working dir is translated to the planner mount layout.
        assert call["worker_working_dir"] == (
            f"/workspace/{result['operation']['operation_id']}/repo"
        )

    def test_two_operations_get_distinct_ids_contexts_and_artifacts(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        first = run_plan_inspection(origin_repo, settings, transport)
        second = run_plan_inspection(origin_repo, settings, transport)

        assert first["status"] == second["status"] == STATUS_INSPECTION_COMPLETE
        first_op, second_op = first["operation"], second["operation"]
        assert first_op["operation_id"] != second_op["operation_id"]
        assert (
            first_op["sdk_conversation_id"] != second_op["sdk_conversation_id"]
        )
        assert first_op["artifact_path"] != second_op["artifact_path"]
        assert first_op["workspace_path"] != second_op["workspace_path"]
        # Each transport call ran against its own fresh clone path.
        assert transport.calls[0]["repo_dir"] != transport.calls[1]["repo_dir"]
        # Both evidence files exist independently.
        for op in (first_op, second_op):
            assert (Path(op["artifact_path"]) / "inspection" / "inspection-evidence.json").is_file()

    def test_planner_run_error_fails_with_partial_evidence_retained(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport(
            raise_error=PlannerRunError("worker conversation blew up")
        )

        result = run_plan_inspection(origin_repo, settings, transport)

        assert result["status"] == STATUS_INSPECTION_FAILED
        assert "worker conversation blew up" in result["error_message"]
        # Partial coordinator-side evidence is still recorded and retained.
        evidence_path = Path(result["evidence_path"])
        assert evidence_path.is_file()
        evidence = json.loads(evidence_path.read_text())
        assert evidence["planner"]["status"] == "failed"
        assert evidence["modules"]  # deterministic collection still ran
        # The fresh clone workspace is retained, never deleted/reset.
        assert Path(result["workspace_repo_dir"]).is_dir()

    def test_planner_setup_error_blocks_inspection(self, tmp_path, origin_repo):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport(
            raise_error=PlannerSetupError("worker endpoint unreachable")
        )

        result = run_plan_inspection(origin_repo, settings, transport)

        assert result["status"] == STATUS_INSPECTION_BLOCKED
        assert "unreachable" in result["error_message"]


class TestPlannerContributionInEvidence:
    """The inspection evidence must show the planner's own contribution.

    Checkbox 6 requires inspection evidence to be produced *through* the
    Planner role, so the recorded evidence must differ when the planner
    contributes versus when it fails.
    """

    def test_planner_observations_flow_into_recorded_evidence(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)
        transport = FakePlannerTransport()

        result = run_plan_inspection(origin_repo, settings, transport)

        assert result["status"] == STATUS_INSPECTION_COMPLETE
        evidence = json.loads(Path(result["evidence_path"]).read_text())
        # The fake transport's distinctive content is what the recorded
        # evidence carries — not just the coordinator's deterministic walk.
        assert evidence["planner"]["summary"] == (
            "Inspected fixture modules; no writes attempted."
        )
        assert evidence["planner"]["transport"] == "fake"
        assert evidence["planner"]["conversation_id"] == (
            result["operation"]["sdk_conversation_id"]
        )

    def test_evidence_differs_between_planner_success_and_failure(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path)

        ok = run_plan_inspection(
            origin_repo, settings, FakePlannerTransport()
        )
        failed = run_plan_inspection(
            origin_repo,
            settings,
            FakePlannerTransport(raise_error=PlannerRunError("planner died")),
        )

        ok_evidence = json.loads(Path(ok["evidence_path"]).read_text())
        failed_evidence = json.loads(Path(failed["evidence_path"]).read_text())
        assert ok_evidence["planner"]["status"] == "completed"
        assert failed_evidence["planner"]["status"] == "failed"
        assert ok_evidence["planner"] != failed_evidence["planner"]


class TestRuntimeSetupFailuresClearly:
    """Missing/invalid worker runtime setup fails clearly (checkbox 6)."""

    def test_worker_working_dir_misconfiguration_is_actionable(
        self, tmp_path, origin_repo
    ):
        from src.workspace import (
            OperationContext,
            OperationWorkspaceError,
            generate_operation_id,
        )

        settings = make_inspection_settings(tmp_path)
        # An injected operation factory rooted OUTSIDE settings.workspace_root
        # mirrors a misconfigured deployment (e.g. compose mounts a different
        # volume root than the coordinator writes to).
        elsewhere = tmp_path / "elsewhere-workspaces"
        context = OperationContext(
            operation_id=generate_operation_id(),
            workspace_root=elsewhere,
            artifact_root=tmp_path / "artifacts",
            control_root=tmp_path / "control",
        )

        def foreign_operation_factory(_settings):
            return context

        with pytest.raises(OperationWorkspaceError) as excinfo:
            run_plan_inspection(
                origin_repo,
                settings,
                FakePlannerTransport(),
                operation_factory=foreign_operation_factory,
            )

        assert "WORKSPACE_ROOT" in str(excinfo.value)
        assert "PLANNER_WORKSPACE_MOUNT" in str(excinfo.value)


class TestSetupAndLockBoundaries:
    def test_missing_worker_endpoint_blocks_before_clone_or_lock(
        self, tmp_path, origin_repo
    ):
        settings = make_inspection_settings(tmp_path, planner_agent_server_url=None)
        transport = FakePlannerTransport()

        result = run_plan_inspection(origin_repo, settings, transport)

        assert result["status"] == STATUS_INSPECTION_BLOCKED
        message = result["error_message"]
        assert "PLANNER_AGENT_SERVER_URL" in message
        assert "worker" in message.lower()
        # Nothing ran: no operation created, no workspace, no lock taken.
        assert transport.calls == []
        assert not (tmp_path / "workspaces").exists()
        assert not (tmp_path / "control" / "locks").exists()

    def test_concurrent_issue_ownership_propagates_immediately(
        self, tmp_path, origin_repo
    ):
        from src.workspace import acquire_issue_and_branch_locks

        settings = make_inspection_settings(tmp_path)
        # Pre-hold the Issue lock as another live operation would.
        holder = acquire_issue_and_branch_locks(
            settings.control_root,
            operation_id="op-other-invocation",
            gitlab_url=settings.gitlab_url,
            project_id=101,
            issue_iid=7,
        )
        transport = FakePlannerTransport()
        graph = build_plan_graph(settings, planner_transport=transport)

        with pytest.raises(ConcurrentOperationError) as excinfo:
            run_routed_workflow(
                settings,
                project_id="group/demo",
                issue_iid=7,
                gitlab=FakeGitLabTransport(
                    make_project_with_origin(origin_repo), make_issue(["agent::plan"])
                ),
                plan_graph=graph,
            )

        assert excinfo.value.owner["operation_id"] == "op-other-invocation"
        assert transport.calls == []
        holder[0].release()

    def test_clone_failure_fails_clearly_and_retains_artifacts(
        self, tmp_path, origin_repo
    ):
        from src.git_ops import GitOperationError

        settings = make_inspection_settings(tmp_path)

        def failing_clone(*args, **kwargs):
            raise GitOperationError("simulated clone failure (bad ref)")

        transport = FakePlannerTransport()
        result = run_plan_inspection(
            origin_repo, settings, transport, clone_fn=failing_clone
        )

        assert result["status"] == STATUS_INSPECTION_FAILED
        assert "clone" in result["error_message"].lower()
        assert transport.calls == []
        # The operation artifact directory exists and is retained.
        artifact_path = Path(result["operation"]["artifact_path"])
        assert (artifact_path / "operation.json").is_file()
        assert artifact_path.is_dir()
        # The lock was released (released_at recorded).
        lock_files = list((tmp_path / "control" / "locks").glob("issue-*.lock"))
        assert lock_files
        metadata = json.loads(lock_files[0].read_text())
        assert metadata["released_at"]


class TestWorkerConnectivityOffline:
    """``verify_worker_connectivity`` against a local fixture HTTP endpoint.

    Demonstrates the connectivity check contract without any real worker:
    200 on /health means demonstrated connectivity; anything else is a clear
    PlannerSetupError, never an assumption.
    """

    @pytest.fixture
    def fixture_health_server(self):
        hits: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                hits.append(self.path)
                if self.path == "/health":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"OK")
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b"not found")

            def log_message(self, *args):  # silence
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}", hits
        server.shutdown()

    def test_healthy_worker_connectivity_is_demonstrated(self, fixture_health_server):
        host, hits = fixture_health_server
        result = verify_worker_connectivity(host)
        assert result["healthy"] is True
        assert result["status_code"] == 200
        assert result["endpoint"] == host
        assert "/health" in hits  # a real HTTP request arrived

    def test_unhealthy_worker_connectivity_fails_clearly(self, fixture_health_server):
        host, _ = fixture_health_server
        with pytest.raises(PlannerSetupError) as excinfo:
            verify_worker_connectivity(f"{host}/api")
        # A reachable-but-unhealthy endpoint is also a clear setup failure,
        # never an assumption of connectivity.
        assert "HTTP 404" in str(excinfo.value)
        assert "healthy Agent Server is required" in str(excinfo.value)

    @pytest.fixture
    def auth_rejecting_server(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(401)
                self.end_headers()
                self.wfile.write(b"unauthorized")

            def log_message(self, *args):  # silence
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_port}"
        server.shutdown()

    def test_rejected_session_key_names_the_internal_credential(
        self, auth_rejecting_server
    ):
        with pytest.raises(PlannerSetupError) as excinfo:
            verify_worker_connectivity(auth_rejecting_server, api_key="wrong-key")
        # The actionable failure names the internal worker credential, not
        # the coordinator's GitLab token.
        assert "AGENT_SERVER_API_KEY" in str(excinfo.value)
        assert "HTTP 401" in str(excinfo.value)
        assert "GITLAB_TOKEN" not in str(excinfo.value)

    def test_malformed_worker_url_fails_with_actionable_setup_error(self):
        with pytest.raises(PlannerSetupError) as excinfo:
            verify_worker_connectivity("not-a-url")
        assert "PLANNER_AGENT_SERVER_URL" in str(excinfo.value)


class TestRoutedWorkflowSeamUnaffected:
    def test_injected_fake_mode_graph_still_dispatches(self, tmp_path):
        from src.config import Settings

        settings = make_inspection_settings(tmp_path)
        plan_graph = FakeModeGraph()

        result = run_routed_workflow(
            settings,
            project_id="group/demo",
            issue_iid=7,
            gitlab=FakeGitLabTransport(
                make_project_with_origin(tmp_path / "unused.git"),
                make_issue(["agent::plan"]),
            ),
            plan_graph=plan_graph,
        )

        assert result["status"] == "fake_mode_ran"
        assert plan_graph.states[0]["mode"] == MODE_PLAN
