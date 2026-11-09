"""Planner-role invocation against a separately started OpenHands Agent Server.

Role isolation (P0-SPEC-003 / ADR 0002):

- The Planner worker is a SEPARATELY STARTED OpenHands Agent Server
  (``python -m openhands.agent_server``), reached over its supported remote
  API. The coordinator never nests agent execution inside its own process.
- The worker receives NO coordinator GitLab credentials and NO host Docker
  socket; its repository mount is read-only. These properties are enforced by
  the runtime layout (Docker compose mounts / entrypoint), not by prompts.
- Every operation gets a fresh SDK conversation context
  (``OperationContext.sdk_conversation_id``); no conversation is restored.

Transport seam: the remote transport is injectable. Offline tests supply a
controlled fake (no Docker/LLM/network). The real adapter
(``RemoteAgentServerTransport``) is exercised by the opt-in Docker runtime
probes, which is where connectivity and mount permissions are actually
proven — offline tests never claim those properties.

Verified against openhands-sdk 1.60.2 (see requirements.lock):
- server: ``python -m openhands.agent_server --host 0.0.0.0 --port 8000``,
  ``GET /health`` for liveness;
- client: ``Workspace(host=..., api_key=..., working_dir=...)`` passed to
  ``Conversation(...)`` produces a ``RemoteConversation``; the session API key
  is sent as the ``X-Session-API-Key`` header.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import requests

from .checks import detect_check_commands
from .config import Settings, split_model_provider
from .openhands_runner import build_llm_kwargs
from .workspace import OperationContext

PLANNER_PROMPT_PATH = Path(__file__).resolve().parent / "prompt" / "planner_prompt.md"

# Source-code suffixes considered when collecting module/source evidence.
SOURCE_SUFFIXES = frozenset(
    {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go", ".rs",
     ".rb", ".php", ".cs", ".c", ".cc", ".cpp", ".h", ".hpp", ".swift", ".sh"}
)
IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", ".venv", "venv", "node_modules", "build", "dist", "target",
     "__pycache__", ".idea", ".vscode"}
)
# Configuration / manifest / CI files worth recording as inspection evidence.
CONFIG_FILE_NAMES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "Pipfile", "poetry.lock", "uv.lock", "package.json", "package-lock.json",
    "pnpm-lock.yaml", "yarn.lock", "tsconfig.json", "go.mod", "go.sum",
    "Cargo.toml", "pom.xml", "build.gradle", "build.gradle.kts", "gradlew",
    "Makefile", "Dockerfile", "docker-compose.yml", ".gitlab-ci.yml",
    ".github", "AGENTS.md", "README.md", "mvnw",
)


class PlannerSetupError(RuntimeError):
    """Planner worker endpoint/toolchain setup is missing or unusable.

    A blocking setup problem (BLOCKED-style), never silently retried and
    never confused with a successful inspection.
    """


class PlannerRunError(RuntimeError):
    """The planner conversation itself failed; available evidence is retained."""


@dataclass(frozen=True)
class InspectionEvidence:
    """Observable inspection evidence for one operation.

    Module/source/configuration discovery is performed deterministically by
    the coordinator from the fresh clone, so evidence does not depend solely
    on the LLM. The planner worker result is recorded alongside it.
    """

    operation_id: str
    baseline_branch: str
    inspected_commit: str | None
    modules: list[dict]
    source_file_count: int
    config_files: list[str]
    verification_hints: dict
    planner: dict
    collected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    def to_dict(self) -> dict:
        return {
            "operation_id": self.operation_id,
            "baseline_branch": self.baseline_branch,
            "inspected_commit": self.inspected_commit,
            "modules": self.modules,
            "source_file_count": self.source_file_count,
            "config_files": self.config_files,
            # Build/test hints carry provenance and are UNVERIFIED: Plan Mode
            # never runs them (implementation plan §7).
            "verification_hints": self.verification_hints,
            "planner": self.planner,
            "collected_at": self.collected_at,
        }


def record_evidence(artifact_path: Path, evidence: InspectionEvidence) -> Path:
    """Write inspection evidence to artifact storage (outside the checkout).

    Appends/overwrites only this operation's own artifact files; retained
    evidence from earlier operations is never touched or deleted.
    """
    inspection_dir = Path(artifact_path) / "inspection"
    inspection_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = inspection_dir / "inspection-evidence.json"
    evidence_path.write_text(
        json.dumps(evidence.to_dict(), indent=2),
        encoding="utf-8",
    )
    return evidence_path


def make_planner_prompt(
    issue: dict, project: dict, branch: str, operation: OperationContext
) -> str:
    title = str(issue.get("title") or "Untitled issue")
    description = str(issue.get("description") or "No description provided.")
    labels = issue.get("labels") or []
    labels_text = ", ".join(str(label) for label in labels) or "none"
    issue_url = str(issue.get("web_url") or "(not provided)")
    project_name = str(
        project.get("path_with_namespace") or project.get("name") or "repository"
    )
    template = PLANNER_PROMPT_PATH.read_text(encoding="utf-8")
    return template.format(
        project_name=project_name,
        title=title,
        issue_url=issue_url,
        labels_text=labels_text,
        description=description,
        branch=branch,
        operation_id=operation.operation_id,
    )


# ---------------------------------------------------------------------------
# Coordinator-side deterministic inspection (no LLM)
# ---------------------------------------------------------------------------

def _git_head(repo_dir: Path) -> str | None:
    import subprocess

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir), text=True, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout.strip() or None if completed.returncode == 0 else None


def collect_repository_inspection(
    repo_dir: Path,
    *,
    baseline_branch: str,
    verification_hints: dict | None = None,
) -> tuple[dict, dict, dict]:
    """Deterministically collect (modules, config_files, toolchain) evidence.

    Reads only; never installs, builds, or executes project code. Reuses the
    existing ``detect_check_commands`` patterns for toolchain hints, recording
    their provenance explicitly.
    """
    repo_dir = Path(repo_dir)
    modules: list[dict] = []
    source_paths: list[Path] = []
    for entry in sorted(repo_dir.iterdir()):
        if entry.name.startswith(".") or not entry.is_dir():
            continue
        if entry.name in IGNORED_DIRECTORY_NAMES:
            continue
        file_count = 0
        suffix_counts: dict[str, int] = {}
        for path in entry.rglob("*"):
            if not path.is_file() or ".git" in path.parts:
                continue
            file_count += 1
            suffix = path.suffix.lower()
            if suffix in SOURCE_SUFFIXES:
                suffix_counts[suffix] = suffix_counts.get(suffix, 0) + 1
                source_paths.append(path)
        modules.append(
            {
                "path": entry.name,
                "file_count": file_count,
                "source_by_suffix": dict(sorted(suffix_counts.items())),
            }
        )

    for path in repo_dir.rglob("*"):
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES and ".git" not in path.parts:
            source_paths.append(path)

    config_files = sorted(
        {
            str(path.relative_to(repo_dir))
            for name in CONFIG_FILE_NAMES
            for path in repo_dir.rglob(name)
            if path.exists() and ".git" not in path.parts
        }
    )

    hints = verification_hints or {}
    detected = detect_check_commands(
        repo_dir,
        build_command=hints.get("build_command"),
        test_command=hints.get("test_command"),
    )
    toolchain = {
        "build": {
            "command": detected["build"],
            "origin": "CONFIGURED" if hints.get("build_command") else (
                "DETECTED" if detected["build"] else None
            ),
            "status": "UNVERIFIED",
        },
        "test": {
            "command": detected["test"],
            "origin": "CONFIGURED" if hints.get("test_command") else (
                "DETECTED" if detected["test"] else None
            ),
            "status": "UNVERIFIED",
        },
    }
    return (
        modules,
        config_files,
        {
            "build": toolchain["build"],
            "test": toolchain["test"],
            "note": "Hints discovered from manifests; never executed during "
                    "Plan inspection (P0-SPEC-004 read-only inspection).",
        },
    )


def build_inspection_evidence(
    *,
    operation: OperationContext,
    repo_dir: Path,
    baseline_branch: str,
    verification_hints: dict | None = None,
    planner_result: dict | None = None,
) -> InspectionEvidence:
    modules, config_files, toolchain = collect_repository_inspection(
        repo_dir, baseline_branch=baseline_branch, verification_hints=verification_hints
    )
    source_count = sum(
        sum(module["source_by_suffix"].values()) for module in modules
    )
    return InspectionEvidence(
        operation_id=operation.operation_id,
        baseline_branch=baseline_branch,
        inspected_commit=_git_head(repo_dir),
        modules=modules,
        source_file_count=source_count,
        config_files=config_files,
        verification_hints=toolchain,
        planner=planner_result or {"status": "not_run"},
    )


# ---------------------------------------------------------------------------
# Transport seam
# ---------------------------------------------------------------------------

class PlannerTransport(Protocol):
    """Injectable transport used to run the Planner role for one operation."""

    def run(
        self,
        *,
        prompt: str,
        repo_dir: Path,
        worker_working_dir: str,
        operation: OperationContext,
        settings: Settings,
    ) -> dict:
        """Run the planner; return ``{"status", "summary", ...metadata}``."""
        ...


def verify_worker_connectivity(
    host: str, api_key: str | None = None, *, timeout: float = 10.0
) -> dict:
    """Demonstrate worker connectivity instead of assuming it.

    Probes the verified ``GET /health`` liveness endpoint of the Agent Server
    and returns structured evidence. Raises ``PlannerSetupError`` with an
    actionable message when the worker cannot be reached.
    """
    headers = {"X-Session-API-Key": api_key} if api_key else {}
    try:
        response = requests.get(f"{host}/health", headers=headers, timeout=timeout)
    except (requests.exceptions.InvalidURL, requests.exceptions.MissingSchema) as exc:
        raise PlannerSetupError(
            f"PLANNER_AGENT_SERVER_URL is not a usable URL ({host!r}): {exc}. "
            "Set it to the Agent Server worker's reachable address, e.g. "
            "http://planner:8000 when launched through compose."
        ) from exc
    except requests.RequestException as exc:
        raise PlannerSetupError(
            f"Planner worker endpoint {host} is not reachable ({type(exc).__name__}). "
            "Start the Planner Agent Server worker (compose service 'planner' via "
            "scripts/launch.sh or scripts/launch.ps1) and set "
            "PLANNER_AGENT_SERVER_URL to its reachable address. Container "
            "networking must not assume host localhost."
        ) from exc
    if response.status_code in (401, 403):
        raise PlannerSetupError(
            f"Planner worker endpoint {host} rejected the session API key "
            f"(HTTP {response.status_code}). The worker requires an internal "
            "session key: set AGENT_SERVER_API_KEY to the key the worker was "
            "started with (OH_SESSION_API_KEYS_0). This internal worker "
            "credential is separate from the coordinator's GitLab token."
        )
    if response.status_code != 200:
        raise PlannerSetupError(
            f"Planner worker endpoint {host} answered /health with HTTP "
            f"{response.status_code}; a healthy Agent Server is required "
            "before inspection can run."
        )
    return {
        "healthy": True,
        "status_code": response.status_code,
        "endpoint": host,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


class RemoteAgentServerTransport:
    """Real adapter: runs the planner on a remotely started Agent Server.

    Probe-tested (tests/runtime probes); this adapter performs the verified
    SDK calls below and must stay in sync with requirements.lock.
    """

    def _require_setup(self, settings: Settings) -> None:
        if not settings.planner_agent_server_url:
            raise PlannerSetupError(
                "Planner inspection is not configured: PLANNER_AGENT_SERVER_URL "
                "is not set. Start the separately launched Planner Agent Server "
                "worker (compose service 'planner' via scripts/launch.sh or "
                "scripts/launch.ps1) and set PLANNER_AGENT_SERVER_URL (plus "
                "AGENT_SERVER_API_KEY when the worker requires a session key). "
                "The worker runs WITHOUT GitLab credentials and WITHOUT the "
                "host Docker socket."
            )

    def run(
        self,
        *,
        prompt: str,
        repo_dir: Path,
        worker_working_dir: str,
        operation: OperationContext,
        settings: Settings,
    ) -> dict:
        self._require_setup(settings)
        connectivity = verify_worker_connectivity(
            settings.planner_agent_server_url, settings.agent_server_api_key
        )

        try:
            from openhands.sdk import Agent, Conversation, LLM, Tool, Workspace
            from openhands.tools.task_tracker import TaskTrackerTool
            from openhands.tools.terminal import TerminalTool
        except ImportError as exc:  # pragma: no cover - depends on install
            raise PlannerSetupError(
                "OpenHands SDK is not installed in the coordinator "
                f"({exc}). Install the pinned set from requirements.lock."
            ) from exc

        # Read-only role: no file-editor tool is granted. Write attempts fail
        # at the filesystem level (read-only mount), which the runtime probes
        # verify; prompts scope behavior but are not the enforcement.
        llm = LLM(**build_llm_kwargs(settings), timeout=settings.timeout_seconds)
        agent = Agent(
            llm=llm,
            tools=[Tool(name=TerminalTool.name), Tool(name=TaskTrackerTool.name)],
        )
        # Fresh SDK context per operation: the conversation id is the
        # operation's fresh context id; nothing is restored.
        conversation = Conversation(
            agent=agent,
            workspace=Workspace(
                host=settings.planner_agent_server_url,
                api_key=settings.agent_server_api_key,
                working_dir=worker_working_dir,
            ),
            conversation_id=uuid.UUID(operation.sdk_conversation_id),
            max_iteration_per_run=settings.max_iterations,
        )
        try:
            conversation.send_message(prompt)
            conversation.run()
        except Exception as exc:
            # Release the worker conversation on failure too; retained
            # evidence is unaffected (conversation ids are never reused).
            try:
                conversation.close()
            except Exception:  # pragma: no cover - close failure is not fatal
                pass
            raise PlannerRunError(
                f"Planner conversation failed on worker "
                f"{settings.planner_agent_server_url}: {type(exc).__name__}: {exc}. "
                "Available evidence is retained; nothing was published."
            ) from exc
        summary = _last_agent_message(conversation)
        conversation.close()
        return {
            "status": "completed",
            "summary": summary,
            "conversation_id": operation.sdk_conversation_id,
            "worker": connectivity,
            "transport": "remote_agent_server",
        }


def _last_agent_message(conversation) -> str:
    try:
        state = conversation.get_state()
        events = getattr(state, "events", None) or []
    except Exception:  # pragma: no cover - depends on SDK internals
        return "Planner conversation completed; transcript unavailable."
    messages: list[str] = []
    for event in events:
        if type(event).__name__ == "MessageEvent" and getattr(event, "role", "") == "assistant":
            content = getattr(event, "content", None)
            if callable(content):
                content = content()
            if isinstance(content, list):
                content = " ".join(
                    part.text if hasattr(part, "text") else str(part) for part in content
                )
            if content:
                messages.append(str(content))
    return messages[-1] if messages else (
        "Planner conversation completed; no assistant message captured."
    )
