"""Per-operation workspace, artifact, and shared-control-lock management.

Implements the M3 workspace design (P0-SPEC-003, implementation plan §4.2):

- Every launch gets a unique operation identifier.
- Every operation gets its own workspace volume reference (fresh clone;
  a previous workspace is never reused) and its own artifact directory
  OUTSIDE the product checkout (logs, evidence, reports).
- A shared control volume path holds active lock/ownership metadata only.
  Locks are POSIX advisory locks (``fcntl.flock``), so ownership is
  released by process exit even after a crash, and concurrent ownership
  is rejected immediately (no waiting).
- Retained evidence is never auto-deleted: released lock files stay on
  the control volume with a ``released_at`` marker, and nothing in this
  module removes workspaces or artifacts.
"""

from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit

try:  # POSIX only; native Windows process execution is out of scope (P0-SPEC-003).
    import fcntl
except ImportError:  # pragma: no cover - Windows host development only
    fcntl = None  # type: ignore[assignment]


class OperationWorkspaceError(RuntimeError):
    """Invalid operation/workspace configuration; nothing was started."""


class ConcurrentOperationError(RuntimeError):
    """Another live invocation already owns the requested lock.

    Reported as an immediate rejection (exit-code 2 in the CLI boundary):
    we never wait for, steal, or clean up another operation's ownership.
    Carries the observed owner metadata for operator diagnostics.
    """

    def __init__(self, message: str, *, lock_name: str, owner: dict | None = None):
        super().__init__(message)
        self.lock_name = lock_name
        self.owner = owner or {}


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def generate_operation_id(now: datetime | None = None) -> str:
    """Generate a unique operation identifier for this launch.

    Two launches always receive different identifiers: the value embeds a
    UTC timestamp plus a random token, and callers may pass a frozen clock
    in tests to prove the random suffix still disambiguates.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"op-{stamp}-{uuid.uuid4().hex[:8]}"


def generate_sdk_context_id() -> str:
    """Generate a fresh SDK conversation identifier for this operation.

    Each operation starts a new conversation context; no persistent or
    restored conversation identifier is ever reused (P0-SPEC-003 story 15).
    """
    return str(uuid.uuid4())


@dataclass(frozen=True)
class OperationContext:
    """Identity and storage references for one launch of the application.

    ``workspace_path`` is where the coordinator prepares the fresh clone
    (a per-operation volume mount inside Docker); ``artifact_path`` is the
    per-operation artifact directory, always outside the product checkout;
    ``sdk_conversation_id`` is a fresh context identifier for the worker
    conversation. Nothing here points at a previous operation's storage.
    """

    operation_id: str
    workspace_root: Path
    artifact_root: Path
    control_root: Path
    created_at: str = field(default_factory=_utcnow_iso)
    sdk_conversation_id: str = field(default_factory=generate_sdk_context_id)

    @property
    def workspace_path(self) -> Path:
        return self.workspace_root / self.operation_id

    @property
    def artifact_path(self) -> Path:
        return self.artifact_root / self.operation_id

    def describe(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "created_at": self.created_at,
            "sdk_conversation_id": self.sdk_conversation_id,
            "workspace_path": str(self.workspace_path),
            "artifact_path": str(self.artifact_path),
            "control_root": str(self.control_root),
        }


# Product checkout root: the coding-agent repository itself. Workspaces and
# artifacts must live outside it so agent logs/memory can never leak into a
# Product Change (P0-SPEC-003 story 12).
def product_checkout_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _reject_paths_inside_checkout(*paths: Path) -> None:
    checkout = product_checkout_root().resolve()
    for path in paths:
        resolved = Path(path).expanduser().resolve()
        if resolved == checkout or checkout in resolved.parents:
            raise OperationWorkspaceError(
                f"Path {resolved} is inside the product checkout ({checkout}). "
                "Workspaces, artifacts, and control storage must live outside "
                "the product checkout so agent state cannot leak into product "
                "changes. Set WORKSPACE_ROOT / ARTIFACT_ROOT / CONTROL_ROOT to "
                "locations outside it."
            )


def create_operation_context(
    settings,
    *,
    operation_id: str | None = None,
) -> OperationContext:
    """Create per-operation storage references and the artifact directory.

    Refuses to reuse a previous operation's workspace (fresh-run contract)
    and never deletes or resets anything: if the workspace path already
    exists and is not empty, that is an operator-visible error.
    """
    workspace_root = Path(settings.workspace_root).expanduser()
    artifact_root = Path(settings.artifact_root).expanduser()
    control_root = Path(settings.control_root).expanduser()
    _reject_paths_inside_checkout(workspace_root, artifact_root, control_root)

    context = OperationContext(
        operation_id=operation_id or generate_operation_id(),
        workspace_root=workspace_root,
        artifact_root=artifact_root,
        control_root=control_root,
    )

    workspace_path = context.workspace_path
    if workspace_path.exists() and any(workspace_path.iterdir()):
        raise OperationWorkspaceError(
            f"Workspace {workspace_path} already exists and is not empty "
            f"(operation {context.operation_id}). A new attempt can never "
            "reuse a previous workspace; choose a fresh location and keep "
            "the existing workspace for inspection."
        )

    # Artifact storage is created eagerly (evidence has a home even if the
    # very next step fails); the workspace itself is created only by the
    # fresh checkout step.
    context.artifact_path.mkdir(parents=True, exist_ok=True)
    (context.artifact_path / "operation.json").write_text(
        json.dumps(context.describe(), indent=2), encoding="utf-8"
    )
    return context


# ---------------------------------------------------------------------------
# Shared control volume locks
# ---------------------------------------------------------------------------

LOCK_DIRECTORY_NAME = "locks"

# Stable acquisition order (implementation plan §4.2): the Issue lock is
# acquired first, then the source-branch lock. Release happens in reverse
# order. Ticket 03 exercises only the Issue lock; Execute (later tickets)
# adds the branch lock through the same ordered helper.
LOCK_ORDER_ISSUE = 0
LOCK_ORDER_BRANCH = 1


def _sanitize_key(raw: str) -> str:
    """Make an arbitrary identifier safe for a control-volume file name."""
    cleaned = "".join(c if c.isalnum() or c in "-_" else "-" for c in raw)
    return cleaned.strip("-") or "unknown"


def issue_lock_name(gitlab_url: str, project_id: str | int, issue_iid: int) -> str:
    """Lock key protecting (GitLab instance, Project ID, Issue IID)."""
    host = urlsplit(gitlab_url).netloc or "unknown-host"
    return f"issue-{_sanitize_key(host)}-{_sanitize_key(str(project_id))}-{issue_iid}"


def branch_lock_name(gitlab_url: str, project_id: str | int, branch: str) -> str:
    """Lock key protecting (GitLab instance, Project ID, source branch)."""
    host = urlsplit(gitlab_url).netloc or "unknown-host"
    return (
        f"branch-{_sanitize_key(host)}-{_sanitize_key(str(project_id))}"
        f"-{_sanitize_key(quote(branch, safe=''))}"
    )


def _read_owner_metadata(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


class OperationLock:
    """Advisory lock on the shared control volume.

    ``flock(LOCK_EX | LOCK_NB)`` gives the agreed semantics:

    - concurrent ownership is rejected immediately (never queued or waited);
    - the kernel releases the lock when the owning process exits, even on
      crash, so stale ownership cannot block future operations forever;
    - the lock file itself is retained (with a ``released_at`` marker after
      release) and is never deleted here: nothing on the control volume is
      auto-removed, and the owner record stays available for inspection.
    """

    def __init__(
        self,
        control_root: Path,
        *,
        name: str,
        order: int,
        owner_operation_id: str,
        kind: str,
        key_label: str,
    ):
        self.path = Path(control_root) / LOCK_DIRECTORY_NAME / f"{name}.lock"
        self.name = name
        self.order = order
        self.owner_operation_id = owner_operation_id
        self.kind = kind
        self.key_label = key_label
        self._fd: int | None = None
        self._acquired_at: str | None = None

    # -- ownership metadata --------------------------------------------------
    def _write_metadata(self, extra: dict) -> None:
        payload = {
            "lock": self.name,
            "kind": self.kind,
            "key": self.key_label,
            "pid": os.getpid(),
            "operation_id": self.owner_operation_id,
            "host": socket.gethostname(),
            "acquired_at": self._acquired_at,
            **extra,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    # -- acquire/release -------------------------------------------------------
    def acquire(self) -> "OperationLock":
        if fcntl is None:  # pragma: no cover - Windows host development only
            raise OperationWorkspaceError(
                "Shared control-volume locking requires POSIX advisory locks "
                "(fcntl). Native Windows process execution is out of scope for "
                "this prototype; launch the application through the Docker "
                "launchers (scripts/launch.sh or scripts/launch.ps1)."
            )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # O_RDWR so we can rewrite the metadata; the file is created once and
        # retained afterwards (never deleted).
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            os.close(fd)
            owner = _read_owner_metadata(self.path) or {}
            self._raise_conflict(owner)
        self._fd = fd
        self._acquired_at = _utcnow_iso()
        self._write_metadata({})
        return self

    def _raise_conflict(self, owner: dict) -> None:
        owner_desc = (
            f"operation {owner.get('operation_id', '?')} (pid {owner.get('pid', '?')}, "
            f"host {owner.get('host', '?')}, acquired at {owner.get('acquired_at', '?')})"
            if owner
            else "an unrecorded owner"
        )
        raise ConcurrentOperationError(
            f"Lock '{self.name}' is already owned by {owner_desc}. "
            "Concurrent ownership of the same work is rejected immediately; "
            "nothing was started, edited, or published. If the owning "
            "invocation has exited, its lock is already released by process "
            "exit and the owner record above is retained evidence only.",
            lock_name=self.name,
            owner=owner,
        )

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            # Retain the file and the ownership record with a release marker;
            # this is metadata, never deleted, but it no longer blocks anyone.
            self._write_metadata({"released_at": _utcnow_iso()})
        finally:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None

    @property
    def is_held(self) -> bool:
        return self._fd is not None

    def __enter__(self) -> "OperationLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


def acquire_issue_and_branch_locks(
    control_root: Path,
    *,
    operation_id: str,
    gitlab_url: str,
    project_id: str | int,
    issue_iid: int,
    branch: str | None = None,
) -> list[OperationLock]:
    """Acquire operation locks in the stable order: Issue first, then branch.

    Raises ``ConcurrentOperationError`` on the first conflict; any locks
    already taken are released before propagating, so partial ownership is
    never left behind.
    """
    locks: list[OperationLock] = [
        OperationLock(
            control_root,
            name=issue_lock_name(gitlab_url, project_id, issue_iid),
            order=LOCK_ORDER_ISSUE,
            owner_operation_id=operation_id,
            kind="issue",
            key_label=f"({gitlab_url}, {project_id}, #{issue_iid})",
        )
    ]
    if branch is not None:
        locks.append(
            OperationLock(
                control_root,
                name=branch_lock_name(gitlab_url, project_id, branch),
                order=LOCK_ORDER_BRANCH,
                owner_operation_id=operation_id,
                kind="branch",
                key_label=f"({gitlab_url}, {project_id}, {branch})",
            )
        )
    locks.sort(key=lambda lock: lock.order)  # stable: Issue, then branch
    acquired: list[OperationLock] = []
    try:
        for lock in locks:
            acquired.append(lock.acquire())
    except BaseException:
        for lock in reversed(acquired):
            lock.release()
        raise
    return acquired


def release_locks(locks: list[OperationLock]) -> None:
    """Release in reverse acquisition order; release errors never mask the
    original outcome (the kernel also drops flock ownership on process exit)."""
    for lock in reversed(locks):
        try:
            lock.release()
        except OSError:
            pass
