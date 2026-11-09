from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _int_env(name: str, default: str, minimum: int) -> int:
    """Read an integer env var, raising an actionable error when malformed
    or below the required minimum. Values are never silently clamped."""
    raw = os.getenv(name, default)
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(
            f"{name} must be an integer, got {raw!r}. "
            f"Fix the environment value or set it to the default {default!r}."
        ) from None
    if value < minimum:
        raise ValueError(
            f"{name} must be an integer >= {minimum}, got {raw!r}. "
            f"Fix the environment value or set it to the default {default!r}."
        )
    return value


# LiteLLM provider prefixes we recognize when normalizing the configured
# model string. Anything else in the first segment is treated as part of the
# model name (only relevant when the value carries no known provider prefix).
KNOWN_MODEL_PROVIDERS = frozenset(
    {
        "openai", "ollama", "ollama_chat", "anthropic", "azure", "azure_ai",
        "bedrock", "gemini", "vertex_ai", "groq", "mistral", "cohere",
        "openrouter", "litellm_proxy", "github", "xai", "deepseek", "vllm",
        "lmstudio", "together",
    }
)


def normalize_model_string(raw: str, *, env_name: str = "OLLAMA_MODEL") -> str:
    """Normalize the configured model/provider string exactly once.

    Honors the configured value; the only transformations are:

    - surrounding whitespace is stripped;
    - stacked provider prefixes are collapsed once
      (``openai/ollama/llama3`` → ``ollama/llama3``,
      ``ollama/openai/gpt-4o`` → ``openai/gpt-4o``): prefixes are never
      accumulated (implementation plan §13.1);
    - a bare model name without any provider prefix gets the documented
      default provider ``ollama/`` exactly once (``gemma4:e4b`` →
      ``ollama/gemma4:e4b``).

    Invalid values raise an actionable error naming the environment
    variable instead of being silently repaired.
    """
    value = (raw or "").strip()
    if not value:
        raise ValueError(
            f"{env_name} must be a non-empty model string with an optional "
            f"provider prefix (for example 'ollama/llama3.2:3b' or bare "
            f"'llama3.2:3b'), got {raw!r}. Fix the environment value."
        )
    segments = value.split("/")
    # Collapse stacked known prefixes while two known providers are stacked.
    while len(segments) > 2 and segments[0] in KNOWN_MODEL_PROVIDERS and segments[1] in KNOWN_MODEL_PROVIDERS:
        segments = segments[1:]
    if len(segments) == 1:
        segments = ["ollama", segments[0]]
    elif len(segments) > 2 or (len(segments) == 2 and segments[0] not in KNOWN_MODEL_PROVIDERS):
        raise ValueError(
            f"{env_name} must be '<provider>/<model>' with a known provider "
            f"prefix (for example ollama/llama3.2:3b or openai/gpt-4o), got "
            f"{raw!r}. Known providers: {', '.join(sorted(KNOWN_MODEL_PROVIDERS))}. "
            "Fix the environment value."
        )
    return "/".join(segments)


def split_model_provider(normalized: str) -> tuple[str, str]:
    """Split a normalized model string into (provider, bare model name)."""
    provider, _, bare = normalized.partition("/")
    return provider, bare


def _path_env(name: str, default: Path) -> Path:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return Path(raw).expanduser()


def _url_env(name: str) -> str | None:
    raw = (os.getenv(name) or "").strip()
    return raw.rstrip("/") or None


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
    execution_max_runtime_seconds: int
    setup_command: str | None
    build_command: str | None
    test_command: str | None
    git_author_name: str
    git_author_email: str
    output_dir: Path
    # Per-operation storage roots (outside the product checkout when run
    # through the Docker launchers; validated at operation creation).
    workspace_root: Path = Path.home() / ".gitlab-coding-agent" / "workspaces"
    artifact_root: Path = Path.home() / ".gitlab-coding-agent" / "artifacts"
    control_root: Path = Path.home() / ".gitlab-coding-agent" / "control"
    # Separately started Planner worker (OpenHands Agent Server) endpoint.
    # Internal worker API credentials are distinct from GitLab credentials
    # and are never forwarded to the worker itself.
    planner_agent_server_url: str | None = None
    agent_server_api_key: str | None = None

    @classmethod
    def from_env(cls, output_dir: Path | None = None) -> "Settings":
        load_dotenv()
        default_workspace_root, default_artifact_root, default_control_root = (
            Path.home() / ".gitlab-coding-agent" / "workspaces",
            Path.home() / ".gitlab-coding-agent" / "artifacts",
            Path.home() / ".gitlab-coding-agent" / "control",
        )
        # The model/provider string is normalized exactly once, here, so no
        # downstream code stacks another prefix on it.
        normalized_model = normalize_model_string(os.getenv("OLLAMA_MODEL", "gemma4:e4b"))
        return cls(
            gitlab_url=os.getenv("GITLAB_URL", "https://gitlab.com").rstrip("/"),
            gitlab_token=os.getenv("GITLAB_TOKEN") or None,
            gitlab_git_username=(os.getenv("GITLAB_GIT_USERNAME") or "agent-bot").strip() or "agent-bot",
            ollama_base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/"),
            ollama_model=normalized_model,
            ollama_api_key=os.getenv("OLLAMA_API_KEY", "ollama"),
            # Defaults per implementation plan section 10; values are never
            # silently clamped.
            max_iterations=_int_env("OPENHANDS_MAX_ITERATIONS", "60", 1),
            timeout_seconds=_int_env("OPENHANDS_TIMEOUT_SECONDS", "1800", 1),
            max_fix_attempts=_int_env("MAX_FIX_ATTEMPTS", "3", 0),
            check_timeout_seconds=_int_env("CHECK_TIMEOUT_SECONDS", "1200", 1),
            execution_max_runtime_seconds=_int_env(
                "EXECUTION_MAX_RUNTIME_SECONDS", "7200", 1
            ),
            setup_command=(os.getenv("SETUP_COMMAND") or "").strip() or None,
            build_command=(os.getenv("BUILD_COMMAND") or "").strip() or None,
            test_command=(os.getenv("TEST_COMMAND") or "").strip() or None,
            git_author_name=os.getenv("GIT_AUTHOR_NAME", "agent-bot"),
            git_author_email=os.getenv("GIT_AUTHOR_EMAIL", "ai@agent.bot"),
            output_dir=output_dir or Path("runs"),
            workspace_root=_path_env("WORKSPACE_ROOT", default_workspace_root),
            artifact_root=_path_env("ARTIFACT_ROOT", default_artifact_root),
            control_root=_path_env("CONTROL_ROOT", default_control_root),
            planner_agent_server_url=_url_env("PLANNER_AGENT_SERVER_URL"),
            agent_server_api_key=os.getenv("AGENT_SERVER_API_KEY") or None,
        )
