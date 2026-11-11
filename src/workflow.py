"""Application invocation boundary for the manual Phase 0 workflow.

The automatic POC graph has been retired. Later slices enable operations here;
there is deliberately no fallback to branch creation or unverified publication.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from collections.abc import Callable, Mapping
from pathlib import Path
import time
from typing import Any

from .config import ConfigurationError, Settings
from .gitlab_client import GitLabClient


class OutcomeStatus(str, Enum):
    SUCCESS = "success"
    INVALID_INVOCATION = "invalid_invocation"
    CONFIGURATION_ERROR = "configuration_error"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


@dataclass(frozen=True)
class OperationOutcome:
    status: OutcomeStatus
    message: str
    inspection: dict | None = None
    workspace: Path | None = None
    diagnostics: Path | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == OutcomeStatus.SUCCESS

    @property
    def exit_code(self) -> int:
        return {
            OutcomeStatus.SUCCESS: 0,
            OutcomeStatus.INVALID_INVOCATION: 2,
            OutcomeStatus.CONFIGURATION_ERROR: 3,
            OutcomeStatus.UNAVAILABLE: 4,
            OutcomeStatus.FAILED: 1,
        }[self.status]


@dataclass(frozen=True)
class OperationInput:
    operation: str
    project_id: str
    issue_iid: int
    mr_iid: int | None = None

    def validate(self) -> None:
        if self.operation not in ("explore", "implement"):
            raise ValueError("Choose the explore or implement operation explicitly.")
        if not isinstance(self.project_id, str) or not self.project_id.strip():
            raise ValueError("Supply a Project ID or namespace/project path.")
        if self.project_id.isdecimal() and int(self.project_id) < 1:
            raise ValueError("Project ID must be positive.")
        if type(self.issue_iid) is not int or self.issue_iid < 1:
            raise ValueError("Supply a positive Issue IID.")
        if self.operation == "implement" and (type(self.mr_iid) is not int or self.mr_iid < 1):
            raise ValueError("Implement requires a positive existing MR IID.")
        if self.operation == "explore" and self.mr_iid is not None:
            raise ValueError("Explore does not accept an MR; use implement for an existing MR.")


@dataclass(frozen=True)
class ApplicationServices:
    """External adapters; unavailable operations do not construct or call them."""

    gitlab: GitLabClient | None = None
    role_runtime: Any = None
    command_runner: Callable[..., Any] | None = None
    clock: Callable[[], float] = time.monotonic


class Application:
    def __init__(self, *, settings: Settings | None = None,
                 services: ApplicationServices | None = None,
                 environ: Mapping[str, str] | None = None):
        self.settings = settings
        self.services = services if services is not None else ApplicationServices()
        self.environ = environ

    def invoke(self, request: OperationInput, *, output_dir: Path | None = None,
               gitlab_url: str | None = None) -> OperationOutcome:
        try:
            request.validate()
        except ValueError as exc:
            return OperationOutcome(OutcomeStatus.INVALID_INVOCATION, str(exc))
        try:
            settings = self.settings or Settings.from_env(output_dir=output_dir, environ=self.environ)
            if gitlab_url is not None:
                settings = replace(settings, gitlab_url=gitlab_url.strip().rstrip("/"))
            settings.validate()
        except ConfigurationError as exc:
            return OperationOutcome(OutcomeStatus.CONFIGURATION_ERROR, str(exc))
        if request.operation == 'explore':
            from .explore import InspectionError, inspect
            try:
                workspace, inspection = inspect(request, settings, self.services)
                return OperationOutcome(OutcomeStatus.UNAVAILABLE,
                    'Read-only inspection complete; plan review/publication is not enabled. No executable plan published.',
                    inspection, workspace.checkout, workspace.artifacts)
            except InspectionError as exc:
                return OperationOutcome(OutcomeStatus.FAILED, str(exc),
                    workspace=exc.workspace.checkout, diagnostics=exc.workspace.artifacts)
            except ValueError as exc:
                return OperationOutcome(OutcomeStatus.FAILED, str(exc))
            except Exception:
                return OperationOutcome(OutcomeStatus.FAILED, 'Explore failed; no executable plan published.')
        return OperationOutcome(OutcomeStatus.UNAVAILABLE,
                                f"The {request.operation} operation is not enabled yet; "
                                "install the remaining Phase 0 workflow capabilities before retrying.")
