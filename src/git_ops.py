from __future__ import annotations

import base64
import os
import subprocess
from pathlib import Path


class GitOperationError(RuntimeError):
    pass


def _git(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
    timeout: int = 600,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd) if cwd else None,
            env=env or os.environ.copy(),
            text=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise GitOperationError("Git was not found. Install Git and add it to PATH.") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitOperationError(f"Git command timed out: {' '.join(args[:3])}") from exc
    if check and result.returncode != 0:
        # Never include environment variables or auth headers in error messages.
        raise GitOperationError(
            f"git {' '.join(args[:3])} failed (exit {result.returncode}):\n{result.stdout[-8000:]}"
        )
    return result


def _git_auth_env(token: str | None, username: str = "oauth2") -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GCM_INTERACTIVE"] = "never"
    env.pop("GITLAB_TOKEN", None)
    env.pop("OLLAMA_API_KEY", None)
    if token:
        # Send credentials through a one-shot Git configuration environment;
        # they are not put in the remote URL or persisted .git/config.
        auth = base64.b64encode(f"{username}:{token}".encode("utf-8")).decode("ascii")
        env["GIT_CONFIG_COUNT"] = "2"
        env["GIT_CONFIG_KEY_0"] = "http.extraheader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {auth}"
        # Prevent fallback to a host credential helper that might use unrelated credentials.
        env["GIT_CONFIG_KEY_1"] = "credential.helper"
        env["GIT_CONFIG_VALUE_1"] = ""
    return env


def clone_repository(
    repo_url: str,
    destination: Path,
    token: str | None = None,
    *,
    branch: str | None = None,
    git_username: str = "oauth2",
) -> Path:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and any(destination.iterdir()):
        raise GitOperationError(f"Workspace already exists and is not empty: {destination}")
    args = ["clone", "--depth", "1"]
    if branch:
        args.extend(["--branch", branch])
    args.extend([repo_url, str(destination)])
    _git(args, env=_git_auth_env(token, username=git_username))
    return destination


def create_issue_branch(repo_dir: Path, issue_iid: int, suffix: str | None = None) -> str:
    branch = f"agent/issue-{issue_iid}"
    if suffix:
        safe_suffix = "".join(c for c in suffix if c.isalnum() or c in "-_")
        if safe_suffix:
            branch = f"{branch}-{safe_suffix}"
    _git(["switch", "-c", branch], cwd=repo_dir)
    return branch


def get_working_tree_summary(repo_dir: Path) -> str:
    status = _git(["status", "--short"], cwd=repo_dir).stdout.strip()
    diff_stat = _git(["diff", "--stat"], cwd=repo_dir).stdout.strip()
    staged_stat = _git(["diff", "--cached", "--stat"], cwd=repo_dir).stdout.strip()
    parts = []
    if status:
        parts.append("git status --short:\n" + status)
    if diff_stat:
        parts.append("unstaged diff --stat:\n" + diff_stat)
    if staged_stat:
        parts.append("staged diff --stat:\n" + staged_stat)
    return "\n\n".join(parts) if parts else "No working-tree changes detected."


def commit_and_push_branch(
    repo_dir: Path,
    *,
    branch: str,
    token: str | None,
    author_name: str,
    author_email: str,
    commit_message: str,
    git_username: str = "oauth2",
) -> str:
    """Stage changes (excluding agent summary), create a local commit, and push."""
    if not token:
        raise GitOperationError(
            "GITLAB_TOKEN is required to publish. Use a token with api and write_repository scopes."
        )

    # Prevent accidentally publishing the internal agent summary, even if the
    # agent happened to stage it during its work.
    summary_in_index = _git(["ls-files", "--stage", "--", ".agent-summary.md"], cwd=repo_dir).stdout.strip()
    if summary_in_index:
        _git(["restore", "--staged", "--", ".agent-summary.md"], cwd=repo_dir)
    _git(["add", "-A", "--", ".", ":(exclude).agent-summary.md"], cwd=repo_dir)
    staged = _git(["diff", "--cached", "--quiet"], cwd=repo_dir, check=False)
    if staged.returncode == 0:
        raise GitOperationError(
            "No source changes are staged; refusing to create an empty commit or merge request."
        )
    if staged.returncode != 1:
        raise GitOperationError(f"Could not inspect staged changes: {staged.stdout}")

    env = os.environ.copy()
    env.pop("GITLAB_TOKEN", None)
    env.pop("OLLAMA_API_KEY", None)
    env.update(
        {
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
    )
    # Ignore host/global Git hooks for this generated commit and push. A local
    # hook could otherwise execute arbitrary repository/host commands.
    hooks_dir = repo_dir / ".git" / "openhands-disabled-hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_config = ["-c", f"core.hooksPath={hooks_dir}"]
    _git(
        [*hooks_config, "-c", "commit.gpgsign=false", "commit", "-m", commit_message],
        cwd=repo_dir,
        env=env,
    )
    commit_sha = _git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
    _git(
        [*hooks_config, "push", "--set-upstream", "origin", branch],
        cwd=repo_dir,
        env=_git_auth_env(token, username=git_username),
    )
    return commit_sha
