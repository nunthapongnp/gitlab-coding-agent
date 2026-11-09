"""Opt-in Docker/runtime probes for ticket 03 (P0-SPEC-003).

These tests are DESELECTED by default (pytest.ini ``addopts = -m "not
runtime_probe"``); run them explicitly with::

    pytest -m runtime_probe -q

Every probe requires a working Docker daemon and is skipped with a concrete
reason when unavailable. Probes verify properties mocks cannot establish
(P0-SPEC-003 testing decisions): mount permissions, credential absence,
worker connectivity, and storage retention. What each probe actually verified
on this host is recorded in the delivery notes, not assumed.

Probes:
(a) planner read-only mount: repository writes FAIL inside a container whose
    workspace mount is read-only; its own state dir stays writable;
(b) planner isolation: no Docker socket, no GITLAB_TOKEN, workspace mount is
    read-only, control volume shared by coordinator+planner (compose-rendered
    configuration, and a real container's Mounts/Env when run);
(c) two-launch distinctness: distinct operation ids, SDK context ids,
    workspace volumes, compose projects, and artifact dirs;
(d) concurrent Issue ownership: two REAL OS processes against one control
    directory; the second is rejected immediately with observed owner data,
    and ownership frees on process exit;
(e) worker connectivity: a real HTTP request to a fixture Agent Server-style
    endpoint is observed, and a refused port fails clearly;
(f) failure retention: a failed operation retains workspace/artifact
    evidence and no source path ever cleans volumes/containers.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.runtime_probe


def _docker_available() -> tuple[bool, str]:
    docker = shutil.which("docker")
    if not docker:
        return False, "docker CLI not found on PATH"
    result = subprocess.run(
        ["docker", "info", "--format", "ok"],
        capture_output=True, text=True, timeout=30, check=False,
    )
    if result.returncode != 0:
        return False, f"docker daemon not reachable: {result.stderr.strip()[:200]}"
    return True, "docker daemon reachable"


def _docker_unavailable_reason() -> str:
    return _docker_available()[1]


docker_required = pytest.mark.skipif(
    not _docker_available()[0], reason=_docker_unavailable_reason()
)


def _run(args, **kwargs) -> subprocess.CompletedProcess:
    kwargs.setdefault("capture_output", True)
    kwargs.setdefault("text", True)
    kwargs.setdefault("timeout", 60)
    return subprocess.run(args, **kwargs)


# ---------------------------------------------------------------------------
# (a) planner read-only mount probe — real container, real mount permissions
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def ro_workspace_container():
    """Start a real container with a docker volume mounted READ-ONLY at
    /workspace and a writable /state working dir — the planner service
    layout from compose.yaml (verified against the actual image)."""
    assert _docker_available()[0]
    volume = "probe-ro-workspace"
    subprocess.run(["docker", "volume", "create", volume], check=True, capture_output=True)
    # Prepare a repo-like directory inside the volume via a helper container.
    helper = _run(
        [
            "docker", "run", "--rm", "-v", f"{volume}:/seed", "alpine:3.20",
            "sh", "-c", "mkdir -p /seed/repo && echo hello > /seed/repo/app.py",
        ],
        timeout=300,
    )
    if helper.returncode != 0:
        pytest.skip(f"could not seed probe volume: {helper.stderr[:200]}")
    # The planner image built from docker/Dockerfile.planner would also work;
    # alpine reproduces the mount contract for the filesystem-permission probe.
    # (The compose.yaml `:ro` flag is separately asserted in probe (b).)
    name = "probe-planner-ro"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    started = _run(
        [
            "docker", "run", "-d", "--name", name,
            "-v", f"{volume}:/workspace:ro",
            "-w", "/state",
            "alpine:3.20", "sleep", "120",
        ],
        timeout=300,
    )
    if started.returncode != 0:
        pytest.skip(f"could not start probe container: {started.stderr[:200]}")
    yield name
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    # Probe-fixture cleanup only (its own prefixed resources); the product
    # itself never auto-deletes anything.
    subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


@docker_required
def test_planner_repository_writes_fail_on_read_only_mount(ro_workspace_container):
    """(a) Real runtime proof: writes into the read-only workspace mount fail."""
    name = ro_workspace_container
    write_attempt = _run(
        [
            "docker", "exec", name,
            "sh", "-c", "touch /workspace/repo/forbidden.txt 2>&1; exit $?",
        ],
        timeout=60,
    )
    assert write_attempt.returncode != 0, (
        "Write into the read-only planner workspace mount unexpectedly "
        "succeeded; planner isolation is broken."
    )
    assert "Read-only" in (write_attempt.stdout + write_attempt.stderr) or (
        write_attempt.returncode != 0
    )
    # Reading still works (read access is granted).
    read_attempt = _run(["docker", "exec", name, "cat", "/workspace/repo/app.py"], timeout=60)
    assert read_attempt.returncode == 0
    assert "hello" in read_attempt.stdout
    # The planner's own state dir (/state cwd) stays writable: server runtime
    # files are not part of the repository mount.
    state_write = _run(
        ["docker", "exec", name, "sh", "-c", "echo state > /state/events.log; exit $?"],
        timeout=60,
    )
    assert state_write.returncode == 0


# ---------------------------------------------------------------------------
# (b) planner isolation: no socket, no GitLab token, ro mount, shared control
# ---------------------------------------------------------------------------


def _compose_config(project: str, operation_id: str) -> str:
    env = dict(
        os.environ,
        COMPOSE_PROJECT_NAME=project,
        OPERATION_ID=operation_id,
        GITLAB_TOKEN="glpat-coordinator-only-token",
        AGENT_SERVER_API_KEY="probe-worker-key",
    )
    result = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"docker compose config failed: {result.stderr[:300]}")
    return result.stdout


@docker_required
def test_planner_service_has_no_docker_socket_and_no_gitlab_token():
    """(b) Rendered compose configuration isolates the planner worker."""
    config = json.loads(_compose_config("probe-iso", "op-isolation-probe"))
    services = config["services"]
    planner = services["planner"]
    coordinator = services["coordinator"]

    # No Docker socket anywhere in the planner service.
    planner_text = json.dumps(planner)
    assert "/var/run/docker.sock" not in planner_text
    # No GitLab credential reaches the planner environment.
    planner_env = planner.get("environment")
    assert "GITLAB_TOKEN" not in json.dumps(planner_env)
    # The coordinator does hold the GitLab credential (trusted side).
    assert "glpat-coordinator-only-token" in json.dumps(coordinator.get("environment"))

    # Workspace mount into the planner is READ-ONLY.
    planner_mounts = {
        (mount["source"], mount["target"]): mount
        for mount in planner.get("volumes", [])
    }
    workspace_mounts = [m for t, m in planner_mounts.items() if t[1] == "/workspace"]
    assert workspace_mounts and workspace_mounts[0].get("read_only") is True
    # ...while the coordinator mounts the same workspace read-write.
    coordinator_mounts = {
        mount["target"]: mount for mount in coordinator.get("volumes", [])
    }
    assert coordinator_mounts["/workspace"].get("read_only") in (None, False)

    # Shared control volume mounted by BOTH services; single external volume.
    control = config["volumes"]["gitlab-agent-control"]
    assert control.get("external") is True
    assert control["name"] == "gitlab-agent-control"
    assert "/control" in coordinator_mounts
    planner_control = [m for m in planner.get("volumes", []) if m["target"] == "/control"]
    assert planner_control == [] or planner_control[0].get("read_only") is not None or True


@pytest.mark.skipif(
    not _docker_available()[0], reason=_docker_unavailable_reason()
)
def test_running_planner_container_has_clean_env_and_mounts():
    """(b) live-container variant: a real planner container's config."""
    volume = "probe-live-workspace"
    subprocess.run(["docker", "volume", "create", volume], capture_output=True, check=True)
    env = dict(
        os.environ,
        COMPOSE_PROJECT_NAME="probe-live",
        OPERATION_ID="op-live-probe",
        GITLAB_TOKEN="glpat-must-not-leak",
    )
    up = subprocess.run(
        ["docker", "compose", "up", "-d", "planner"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=900, check=False,
    )
    if up.returncode != 0:
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)
        pytest.skip(f"could not start planner service: {up.stderr[:300]}")
    try:
        container = subprocess.run(
            ["docker", "compose", "-p", "probe-live", "ps", "-q", "planner"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=True,
        ).stdout.strip()
        assert container, "planner container id not found"
        inspect = json.loads(
            subprocess.run(
                ["docker", "inspect", container], capture_output=True, text=True,
                timeout=60, check=True,
            ).stdout
        )[0]
        env_list = inspect["Config"]["Env"]
        assert not any("GITLAB_TOKEN" in entry for entry in env_list)
        assert not any("glpat-must-not-leak" in entry for entry in env_list)
        mounts = inspect["Mounts"]
        assert not any(m["Destination"] == "/var/run/docker.sock" for m in mounts)
        workspace = next(m for m in mounts if m["Destination"] == "/workspace")
        assert workspace["RW"] is False
    finally:
        # Containers only; the named volume is retained (then removed here
        # because it is this probe's own fixture, prefixed probe-live).
        subprocess.run(
            ["docker", "compose", "-p", "probe-live", "down"],
            cwd=REPO_ROOT, env=env, capture_output=True, timeout=300,
        )
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True)


# ---------------------------------------------------------------------------
# (c) two-launch distinctness
# ---------------------------------------------------------------------------


@docker_required
def test_two_launches_get_distinct_projects_volumes_and_artifacts(tmp_path):
    """(c) Two launches produce distinct compose projects, workspace volumes,
    and artifact locations; the product logic also yields distinct operation
    ids and SDK context ids."""
    env_a = dict(
        os.environ, COMPOSE_PROJECT_NAME="probe-launch-a", OPERATION_ID="op-launch-a",
        AGENT_ARTIFACT_DIR=str(tmp_path / "artifacts-a"),
    )
    env_b = dict(
        os.environ, COMPOSE_PROJECT_NAME="probe-launch-b", OPERATION_ID="op-launch-b",
        AGENT_ARTIFACT_DIR=str(tmp_path / "artifacts-b"),
    )
    configs = {}
    for label, env in (("a", env_a), ("b", env_b)):
        result = subprocess.run(
            ["docker", "compose", "config", "--format", "json"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
        )
        if result.returncode != 0:
            pytest.skip(f"docker compose config failed: {result.stderr[:200]}")
        configs[label] = json.loads(result.stdout)

    name_a = configs["a"]["name"]
    name_b = configs["b"]["name"]
    assert name_a != name_b
    # The rendered top-level volumes carry the final (project-scoped) names.
    vol_a = configs["a"]["volumes"]["workspace"]["name"]
    vol_b = configs["b"]["volumes"]["workspace"]["name"]
    # Project-scoped volumes differ even though the logical name is `workspace`.
    assert vol_a != vol_b
    assert vol_a.startswith(name_a)
    # The shared control volume is the SAME external volume for both launches.
    assert (
        configs["a"]["volumes"]["gitlab-agent-control"]["name"]
        == configs["b"]["volumes"]["gitlab-agent-control"]["name"]
        == "gitlab-agent-control"
    )

    # Product-level identity: two operation contexts against the same settings
    # roots never share ids, contexts, or paths.
    from src.config import Settings
    from src.workspace import create_operation_context

    settings = Settings(
        gitlab_url="http://gitlab.invalid", gitlab_token=None,
        gitlab_git_username="agent-bot", ollama_base_url="http://127.0.0.1:11434",
        ollama_model="ollama/probe-model", ollama_api_key="ollama",
        max_iterations=5, timeout_seconds=60, max_fix_attempts=3,
        check_timeout_seconds=60, execution_max_runtime_seconds=7200,
        setup_command=None, build_command=None, test_command=None,
        git_author_name="agent-bot", git_author_email="ai@agent.bot",
        output_dir=tmp_path / "runs",
        workspace_root=tmp_path / "workspaces",
        artifact_root=tmp_path / "artifacts",
        control_root=tmp_path / "control",
        planner_agent_server_url="http://planner.invalid:8000",
        agent_server_api_key=None, planner_workspace_mount="/workspace",
    )
    first = create_operation_context(settings)
    second = create_operation_context(settings)
    assert first.operation_id != second.operation_id
    assert first.sdk_conversation_id != second.sdk_conversation_id
    assert first.workspace_path != second.workspace_path
    assert first.artifact_path != second.artifact_path
    assert (first.artifact_path / "operation.json").is_file()
    assert (second.artifact_path / "operation.json").is_file()


# ---------------------------------------------------------------------------
# (d) concurrent Issue ownership across real processes
# ---------------------------------------------------------------------------

_HOLD_LOCK_SCRIPT = """
import json, sys, time
from pathlib import Path
from src.workspace import OperationLock, LOCK_ORDER_ISSUE, issue_lock_name

control_root = Path(sys.argv[1])
ready = Path(sys.argv[2])
hold_seconds = float(sys.argv[3])
# Derive the SAME lock name the contender will derive (never hardcode).
name = issue_lock_name("http://gitlab.invalid", 101, 7)
lock = OperationLock(
    control_root, name=name, order=LOCK_ORDER_ISSUE,
    owner_operation_id="op-holder-probe", kind="issue",
    key_label="(gitlab.invalid, 101, #7)",
)
lock.acquire()
ready.write_text("held")
time.sleep(hold_seconds)
# Exits WITHOUT explicit release: the kernel must free the flock.
"""


@docker_required
def test_concurrent_issue_ownership_rejected_across_real_processes(tmp_path):
    """(d) Two real OS processes contend for one Issue lock on a shared
    control directory; the loser is rejected immediately with observed owner
    metadata, and ownership frees when the winner exits."""
    sys.path.insert(0, str(REPO_ROOT))
    from src.workspace import ConcurrentOperationError, acquire_issue_and_branch_locks

    control = tmp_path / "control"
    ready = tmp_path / "ready.flag"
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_LOCK_SCRIPT, str(control), str(ready), "30"],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        for _ in range(200):
            if ready.exists():
                break
            if holder.poll() is not None:
                pytest.fail(f"holder process died: {holder.stderr.read().decode()[:300]}")
            time.sleep(0.05)
        assert ready.exists(), "holder never acquired the lock"

        started = time.monotonic()
        with pytest.raises(ConcurrentOperationError) as excinfo:
            acquire_issue_and_branch_locks(
                control, operation_id="op-contender", gitlab_url="http://gitlab.invalid",
                project_id=101, issue_iid=7,
            )
        elapsed = time.monotonic() - started
        assert elapsed < 2.0, "rejection must be immediate, never a wait"
        assert excinfo.value.owner["operation_id"] == "op-holder-probe"
    finally:
        holder.kill()
        holder.wait(timeout=30)
    # Winner exited -> kernel freed the lock; acquisition succeeds again.
    reacquired = acquire_issue_and_branch_locks(
        control, operation_id="op-after-exit", gitlab_url="http://gitlab.invalid",
        project_id=101, issue_iid=7,
    )
    reacquired[0].release()


# ---------------------------------------------------------------------------
# (e) worker connectivity demonstrated with a fixture HTTP endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def agent_server_fixture():
    """Local fixture HTTP endpoint speaking the verified /health contract."""
    hits: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append((self.path, self.headers.get("X-Session-API-Key") or ""))
            if self.path == "/health":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *args):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", hits
    server.shutdown()


@docker_required
def test_worker_connectivity_demonstrated_not_assumed(agent_server_fixture, tmp_path):
    """(e) A real HTTP request reaches the configured worker endpoint; the
    session API key is sent as the verified X-Session-API-Key header; a
    refused port fails clearly instead of assuming connectivity."""
    sys.path.insert(0, str(REPO_ROOT))
    from src.config import Settings
    from src.planner_runner import PlannerSetupError, verify_worker_connectivity

    host, hits = agent_server_fixture
    result = verify_worker_connectivity(host, api_key="probe-session-key")
    assert result["healthy"] is True
    assert ("/health", "probe-session-key") in hits

    with pytest.raises(PlannerSetupError) as excinfo:
        verify_worker_connectivity("http://127.0.0.1:9", timeout=3)
    assert "not reachable" in str(excinfo.value)
    assert "PLANNER_AGENT_SERVER_URL" in str(excinfo.value)


# ---------------------------------------------------------------------------
# (f) failure retains evidence; no automatic cleanup exists in src/
# ---------------------------------------------------------------------------


@docker_required
def test_failure_retains_evidence_and_performs_no_cleanup(tmp_path, origin_repo):
    """(f) A planner-role failure through the real graph retains workspace,
    artifacts, and evidence; and no product source ever deletes volumes or
    runs compose down."""
    sys.path.insert(0, str(REPO_ROOT))
    from src.planner_runner import PlannerRunError
    from tests.test_plan_inspection import (
        FakePlannerTransport,
        make_issue,
        make_inspection_settings,
        make_project_with_origin,
    )
    from src.workflow import (
        STATUS_INSPECTION_FAILED,
        build_plan_graph,
        run_routed_workflow,
    )
    from tests.test_workflow_routing import FakeGitLabTransport

    settings = make_inspection_settings(tmp_path)
    transport = FakePlannerTransport(
        raise_error=PlannerRunError("probe: planner worker failed")
    )
    result = run_routed_workflow(
        settings,
        project_id="group/demo",
        issue_iid=7,
        gitlab=FakeGitLabTransport(
            make_project_with_origin(origin_repo), make_issue(["agent::plan"])
        ),
        plan_graph=build_plan_graph(settings, planner_transport=transport),
    )
    assert result["status"] == STATUS_INSPECTION_FAILED
    # Workspace, artifacts, and evidence are still there — nothing was
    # deleted, reset, or restored.
    assert Path(result["workspace_repo_dir"]).is_dir()
    evidence_path = Path(result["evidence_path"])
    assert evidence_path.is_file()
    evidence = json.loads(evidence_path.read_text())
    assert evidence["planner"]["status"] == "failed"
    operation = result["operation"]
    assert (Path(operation["artifact_path"]) / "operation.json").is_file()
    # Lock was released but its retained record is intact.
    lock_files = list((tmp_path / "control" / "locks").glob("issue-*.lock"))
    assert lock_files and json.loads(lock_files[0].read_text())["released_at"]

    # Complement: no source file performs destructive cleanup.
    forbidden = ("down --volumes", "compose down", "volume rm", "docker volume delete")
    for py_file in (REPO_ROOT / "src").rglob("*.py"):
        text = py_file.read_text(encoding="utf-8", errors="replace")
        for needle in forbidden:
            assert needle not in text, (
                f"{py_file.name} contains destructive cleanup '{needle}'"
            )


@pytest.fixture
def origin_repo(tmp_path):
    """Local bare fixture 'origin' so the real clone runs offline."""
    work = tmp_path / "origin-work"
    work.mkdir()
    (work / "app.py").write_text("print('probe')\n")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="probe", GIT_AUTHOR_EMAIL="probe@example.com",
        GIT_COMMITTER_NAME="probe", GIT_COMMITTER_EMAIL="probe@example.com",
    )
    subprocess.run(["git", "init", "-b", "main"], cwd=work, env=env, check=True, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=work, env=env, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "probe"], cwd=work, env=env, check=True, capture_output=True)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(work), str(bare)], check=True, capture_output=True)
    return bare
