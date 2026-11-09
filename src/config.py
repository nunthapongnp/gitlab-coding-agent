from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _int_env(name: str, default: str, minimum: int) -> int:
    """Read an integer env var, raising an actionable error when malformed."""
    raw = os.getenv(name, default)
    try:
        return max(minimum, int(raw))
    except ValueError:
        raise ValueError(
            f"{name} must be an integer, got {raw!r}. "
            f"Fix the environment value or set it to the default {default!r}."
        ) from None


@dataclass(frozen=True)
class Settings:
    gitlab_url: str
    gitlab_token: str | None
    gitlab_git_username: str
    ollama_base_url: str
    ollama_model: str
    ollama_api_key: str
    max_iterations: int
    timeout_seconds: int
    max_fix_attempts: int
    check_timeout_seconds: int
    setup_command: str | None
    build_command: str | None
    test_command: str | None
    git_author_name: str
    git_author_email: str
    output_dir: Path

    @classmethod
    def from_env(cls, output_dir: Path | None = None) -> "Settings":
        load_dotenv()
        return cls(
            gitlab_url=os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/"),
            gitlab_token=os.getenv("GITLAB_TOKEN") or None,
            gitlab_git_username=(os.getenv("GITLAB_GIT_USERNAME") or "agent-bot").strip() or "agent-bot",
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:e4b"),
            ollama_api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            max_iterations=_int_env("OPENHANDS_MAX_ITERATIONS", "35", 1),
            timeout_seconds=_int_env("OPENHANDS_TIMEOUT_SECONDS", "1800", 1),
            max_fix_attempts=_int_env("MAX_FIX_ATTEMPTS", "3", 0),
            check_timeout_seconds=_int_env("CHECK_TIMEOUT_SECONDS", "1200", 1),
            setup_command=(os.getenv("SETUP_COMMAND") or "").strip() or None,
            build_command=(os.getenv("BUILD_COMMAND") or "").strip() or None,
            test_command=(os.getenv("TEST_COMMAND") or "").strip() or None,
            git_author_name=os.getenv("GIT_AUTHOR_NAME", "agent-bot"),
            git_author_email=os.getenv("GIT_AUTHOR_EMAIL", "ai@agent.bot"),
            output_dir=output_dir or Path("runs"),
        )
