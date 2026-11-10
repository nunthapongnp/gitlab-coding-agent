from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """An actionable configuration error that never includes supplied values."""


def _limit(environ: Mapping[str, str], name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(environ.get(name, str(default)))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer >= {minimum}") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True)
class Settings:
    gitlab_url: str
    gitlab_token: str | None = field(repr=False)
    gitlab_git_username: str
    llm_base_url: str
    llm_model: str
    llm_api_key: str = field(repr=False)
    max_iterations: int
    timeout_seconds: int
    max_fix_attempts: int
    execution_max_runtime_seconds: int
    check_timeout_seconds: int
    setup_command: str | None
    build_command: str | None
    test_command: str | None
    git_author_name: str
    git_author_email: str
    output_dir: Path

    def validate(self) -> None:
        missing = [name for name, value in (
            ("GITLAB_TOKEN", self.gitlab_token),
            ("LLM_BASE_URL", self.llm_base_url),
            ("LLM_MODEL", self.llm_model),
            ("LLM_API_KEY", self.llm_api_key),
        ) if not value or not value.strip()]
        if missing:
            raise ConfigurationError("Set required configuration: " + ", ".join(missing))
        for name, value, minimum in (
            ("MAX_FIX_ATTEMPTS", self.max_fix_attempts, 0),
            ("EXECUTION_MAX_RUNTIME_SECONDS", self.execution_max_runtime_seconds, 1),
            ("OPENHANDS_MAX_ITERATIONS", self.max_iterations, 1),
            ("OPENHANDS_TIMEOUT_SECONDS", self.timeout_seconds, 1),
            ("CHECK_TIMEOUT_SECONDS", self.check_timeout_seconds, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ConfigurationError(f"{name} must be an integer >= {minimum}")

    @classmethod
    def from_env(cls, output_dir: Path | None = None, *, environ: Mapping[str, str] | None = None) -> "Settings":
        if environ is None:
            load_dotenv()
            environ = os.environ
        return cls(
            gitlab_url=environ.get("GITLAB_URL", "https://gitlab.com").rstrip("/"),
            gitlab_token=environ.get("GITLAB_TOKEN") or None,
            gitlab_git_username=(environ.get("GITLAB_GIT_USERNAME") or "agent-bot").strip() or "agent-bot",
            llm_base_url=environ.get("LLM_BASE_URL", "").strip().rstrip("/"),
            llm_model=environ.get("LLM_MODEL", "").strip(),
            llm_api_key=environ.get("LLM_API_KEY", "").strip(),
            max_iterations=_limit(environ, "OPENHANDS_MAX_ITERATIONS", 35),
            timeout_seconds=_limit(environ, "OPENHANDS_TIMEOUT_SECONDS", 1800),
            max_fix_attempts=_limit(environ, "MAX_FIX_ATTEMPTS", 3, 0),
            execution_max_runtime_seconds=_limit(environ, "EXECUTION_MAX_RUNTIME_SECONDS", 3600),
            check_timeout_seconds=_limit(environ, "CHECK_TIMEOUT_SECONDS", 1200),
            setup_command=(environ.get("SETUP_COMMAND") or "").strip() or None,
            build_command=(environ.get("BUILD_COMMAND") or "").strip() or None,
            test_command=(environ.get("TEST_COMMAND") or "").strip() or None,
            git_author_name=environ.get("GIT_AUTHOR_NAME", "agent-bot"),
            git_author_email=environ.get("GIT_AUTHOR_EMAIL", "ai@agent.bot"),
            output_dir=output_dir or Path("runs"),
        )
