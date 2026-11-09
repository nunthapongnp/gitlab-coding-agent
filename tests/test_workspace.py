"""Workspace, operation identity, and control-lock tests (ticket 03).

These tests are fully offline: locks live in tmp_path (a stand-in for the
shared control volume), no Docker, GitLab, or LLM is touched.

Properties under test (P0-SPEC-003 / implementation plan §4.2):
- two launches get different operation identifiers, workspace paths,
  artifact paths, and SDK conversation contexts;
- a previous workspace is never reused;
- concurrent lock ownership is rejected immediately (never waited on);
- acquisition order is stable (Issue lock first, then source branch);
- retained evidence is never deleted (released lock files remain).
"""

from __future__ import annotations

import json
import multiprocessing
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.config import (
    Settings,
    normalize_model_string,
    split_model_provider,
)
from src.workspace import (
    LOCK_ORDER_BRANCH,
    LOCK_ORDER_ISSUE,
    ConcurrentOperationError,
    OperationContext,
    OperationWorkspaceError,
    acquire_issue_and_branch_locks,
    branch_lock_name,
    create_operation_context,
    generate_operation_id,
    generate_sdk_context_id,
    issue_lock_name,
    product_checkout_root,
)


def make_settings(tmp_path: Path, **overrides) -> Settings:
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
        planner_agent_server_url=None,
        agent_server_api_key=None,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class TestOperationIdentity:
    def test_two_operation_ids_differ(self):
        assert generate_operation_id() != generate_operation_id()

    def test_operation_id_differs_even_with_frozen_clock(self):
        frozen = datetime(2026, 11, 9, 12, 0, 0, tzinfo=timezone.utc)
        a = generate_operation_id(now=frozen)
        b = generate_operation_id(now=frozen)
        assert a != b
        assert a.startswith("op-20261109T120000Z-")
        assert b.startswith("op-20261109T120000Z-")

    def test_sdk_context_ids_are_fresh_per_operation(self):
        # No persistent conversation context is restored between operations.
        ids = {generate_sdk_context_id() for _ in range(50)}
        assert len(ids) == 50

    def test_two_contexts_get_distinct_storage_and_contexts(self, tmp_path):
        settings = make_settings(tmp_path)
        first = create_operation_context(settings)
        second = create_operation_context(settings)

        assert first.operation_id != second.operation_id
        assert first.workspace_path != second.workspace_path
        assert first.artifact_path != second.artifact_path
        assert first.sdk_conversation_id != second.sdk_conversation_id
        # Distinct artifact locations, both outside the product checkout.
        checkout = product_checkout_root().resolve()
        for context in (first, second):
            resolved = context.artifact_path.resolve()
            assert checkout not in resolved.parents
        assert (first.artifact_path / "operation.json").is_file()
        assert (second.artifact_path / "operation.json").is_file()

    def test_context_records_operation_metadata(self, tmp_path):
        settings = make_settings(tmp_path)
        context = create_operation_context(settings)
        record = json.loads((context.artifact_path / "operation.json").read_text())
        assert record["operation_id"] == context.operation_id
        assert record["sdk_conversation_id"] == context.sdk_conversation_id
        assert record["workspace_path"] == str(context.workspace_path)

    def test_previous_workspace_is_never_reused(self, tmp_path):
        settings = make_settings(tmp_path)
        context = create_operation_context(settings)
        context.workspace_path.mkdir(parents=True)
        (context.workspace_path / "stale.txt").write_text("old evidence")

        with pytest.raises(OperationWorkspaceError) as excinfo:
            create_operation_context(settings, operation_id=context.operation_id)

        assert "never" in str(excinfo.value)
        # The stale workspace is retained, never auto-deleted or reset.
        assert (context.workspace_path / "stale.txt").read_text() == "old evidence"

    def test_paths_inside_product_checkout_are_rejected(self, tmp_path):
        checkout = product_checkout_root()
        settings = make_settings(tmp_path, workspace_root=checkout / "runs" / "ws")

        with pytest.raises(OperationWorkspaceError) as excinfo:
            create_operation_context(settings)

        message = str(excinfo.value)
        assert "outside" in message
        assert "WORKSPACE_ROOT" in message


class TestModelNormalization:
    def test_bare_model_gets_default_provider_once(self):
        assert normalize_model_string("gemma4:e4b") == "ollama/gemma4:e4b"

    def test_configured_provider_is_honored_unchanged(self):
        assert normalize_model_string("ollama/llama3.2:3b") == "ollama/llama3.2:3b"
        assert normalize_model_string("openai/gpt-4o") == "openai/gpt-4o"

    def test_stacked_prefix_collapses_once(self):
        assert normalize_model_string("openai/ollama/llama3.2:3b") == "ollama/llama3.2:3b"
        assert normalize_model_string("ollama/openai/gpt-4o") == "openai/gpt-4o"

    def test_prefix_never_accumulates_across_calls(self):
        once = normalize_model_string("openai/ollama/llama3.2:3b")
        again = normalize_model_string(once)
        assert again == "ollama/llama3.2:3b"

    @pytest.mark.parametrize(
        "bad", ["", "   ", "/model", "unknown-provider/model", "ollama/chat/x"]
    )
    def test_invalid_values_raise_actionable_error(self, bad):
        with pytest.raises(ValueError) as excinfo:
            normalize_model_string(bad, env_name="OLLAMA_MODEL")
        assert "OLLAMA_MODEL" in str(excinfo.value)

    def test_from_env_normalizes_once(self, monkeypatch, tmp_path):
        monkeypatch.setenv("OLLAMA_MODEL", "openai/ollama/llama3.2:3b")
        settings = Settings.from_env(output_dir=tmp_path)
        assert settings.ollama_model == "ollama/llama3.2:3b"
        monkeypatch.delenv("OLLAMA_MODEL")
        settings = Settings.from_env(output_dir=tmp_path)
        assert settings.ollama_model == "ollama/gemma4:e4b"

    def test_split_model_provider(self):
        assert split_model_provider("ollama/llama3.2:3b") == ("ollama", "llama3.2:3b")


class TestLockNames:
    def test_issue_lock_key_covers_instance_project_issue(self):
        a = issue_lock_name("https://gitlab.com", 42, 7)
        b = issue_lock_name("https://gitlab.example.org", 42, 7)
        c = issue_lock_name("https://gitlab.com", 43, 7)
        d = issue_lock_name("https://gitlab.com", 42, 8)
        assert len({a, b, c, d}) == 4

    def test_branch_lock_key_covers_branch(self):
        assert branch_lock_name("https://gitlab.com", 42, "feature/x") != (
            branch_lock_name("https://gitlab.com", 42, "feature/y")
        )
        # Same instance+project+branch as another invocation = same lock.
        assert branch_lock_name("https://gitlab.com", 42, "feature/x") == (
            branch_lock_name("https://gitlab.com", 42, "feature/x")
        )


class TestIssueLock:
    def test_acquire_then_release_records_ownership_metadata(self, tmp_path):
        locks = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-A", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7,
        )
        assert len(locks) == 1
        lock = locks[0]
        assert lock.is_held
        metadata = json.loads(lock.path.read_text())
        assert metadata["operation_id"] == "op-A"
        assert metadata["pid"] > 0
        assert metadata["kind"] == "issue"
        assert "acquired_at" in metadata

        lock.release()
        assert not lock.is_held
        # The lock file is retained (never auto-deleted) with a release marker.
        metadata = json.loads(lock.path.read_text())
        assert "released_at" in metadata
        assert metadata["operation_id"] == "op-A"

    def test_concurrent_ownership_rejected_immediately(self, tmp_path):
        locks = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-A", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7,
        )
        try:
            with pytest.raises(ConcurrentOperationError) as excinfo:
                acquire_issue_and_branch_locks(
                    tmp_path, operation_id="op-B", gitlab_url="http://gitlab.invalid",
                    project_id=101, issue_iid=7,
                )
        finally:
            locks[0].release()

        message = str(excinfo.value)
        assert "op-A" in message  # observed owner, not lock-helper internals
        assert "rejected immediately" in message
        assert excinfo.value.owner["operation_id"] == "op-A"
        # After release, the same lock can be acquired again (flock semantics).
        reacquired = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-B", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7,
        )
        reacquired[0].release()

    def test_different_issues_do_not_conflict(self, tmp_path):
        first = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-A", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7,
        )
        second = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-B", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=8,
        )
        assert first[0].path != second[0].path
        first[0].release()
        second[0].release()

    def test_cross_process_ownership_rejected_and_released_on_exit(self, tmp_path):
        """Externally observed ownership: a real second process is rejected
        while the first holds the lock, and the lock frees when that process
        exits (advisory lock released by process exit, no cleanup needed)."""
        lock_path = tmp_path / "locks" / "issue-gitlab-invalid-101-7.lock"
        ready = tmp_path / "ready.flag"

        proc = multiprocessing.Process(
            target=_hold_lock_then_exit,
            args=(str(tmp_path), str(ready)),
        )
        proc.start()
        for _ in range(200):
            if ready.exists():
                break
            proc.join(timeout=0.05)
        assert ready.exists(), "helper process never acquired the lock"

        # Second live process: rejected immediately with observed ownership.
        with pytest.raises(ConcurrentOperationError) as excinfo:
            acquire_issue_and_branch_locks(
                tmp_path, operation_id="op-B", gitlab_url="http://gitlab.invalid",
                project_id=101, issue_iid=7,
            )
        assert excinfo.value.owner["operation_id"] == "op-holder"

        # Tell the holder to exit without releasing; the kernel frees the lock.
        (tmp_path / "stop.flag").write_text("stop")
        proc.join(timeout=10)
        assert proc.exitcode == 0
        # Owner process exited -> lock is free again, without any cleanup step.
        reacquired = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-C", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7,
        )
        reacquired[0].release()
        assert lock_path.is_file()  # retained, never deleted


def _hold_lock_then_exit(control_root_str: str, ready_path: str) -> None:
    """Multiprocessing target (module level so it can be pickled): acquire the
    Issue lock, signal readiness, then exit WITHOUT releasing. The kernel must
    drop the advisory lock on process exit."""
    from src.workspace import OperationLock, issue_lock_name

    lock = OperationLock(
        Path(control_root_str),
        name=issue_lock_name("http://gitlab.invalid", 101, 7),
        order=LOCK_ORDER_ISSUE,
        owner_operation_id="op-holder",
        kind="issue",
        key_label="(gitlab.invalid, 101, #7)",
    )
    lock.acquire()
    Path(ready_path).write_text("held")
    # Hold the lock until the parent acknowledges rejection, then exit
    # WITHOUT releasing: the kernel must drop the lock on process exit.
    stop = Path(control_root_str) / "stop.flag"
    for _ in range(600):
        if stop.exists():
            break
        time.sleep(0.05)
    sys.exit(0)


class TestStableAcquisitionOrder:
    def test_issue_lock_acquired_before_branch_lock(self, tmp_path):
        acquired_order: list[str] = []
        real_acquire = None
        from src import workspace as workspace_module

        real_acquire = workspace_module.OperationLock.acquire

        def recording_acquire(self):
            acquired_order.append(f"{self.order}:{self.kind}")
            return real_acquire(self)

        workspace_module.OperationLock.acquire = recording_acquire
        try:
            locks = acquire_issue_and_branch_locks(
                tmp_path, operation_id="op-A", gitlab_url="http://gitlab.invalid",
                project_id=101, issue_iid=7, branch="feature/x",
            )
        finally:
            workspace_module.OperationLock.acquire = real_acquire

        assert [lock.kind for lock in locks] == ["issue", "branch"]
        assert acquired_order == [f"{LOCK_ORDER_ISSUE}:issue", f"{LOCK_ORDER_BRANCH}:branch"]

    def test_branch_conflict_releases_already_held_issue_lock(self, tmp_path):
        holder = acquire_issue_and_branch_locks(
            tmp_path, operation_id="op-A", gitlab_url="http://gitlab.invalid",
            project_id=101, issue_iid=7, branch="feature/x",
        )

        with pytest.raises(ConcurrentOperationError):
            acquire_issue_and_branch_locks(
                tmp_path, operation_id="op-B", gitlab_url="http://gitlab.invalid",
                project_id=101, issue_iid=9, branch="feature/x",
            )

        # op-B must not keep partial ownership of issue #9.
        for lock in tmp_path.joinpath("locks").glob("issue-*9*.lock"):
            metadata = json.loads(lock.read_text())
            if metadata.get("operation_id") == "op-B":
                assert "released_at" in metadata
        holder[0].release()
