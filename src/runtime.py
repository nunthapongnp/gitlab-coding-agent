"""Native local runtime. Read-only tools are capabilities, not an OS sandbox."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import signal
import sys
import uuid
import time
from dataclasses import dataclass

from .git_ops import _git_auth_env


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if key not in {'GITLAB_TOKEN', 'LLM_API_KEY', 'GIT_CONFIG_COUNT'}
            and not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}


@dataclass(frozen=True)
class Workspace:
    run_id: str
    checkout: Path
    artifacts: Path
    branch: str
    commit: str


class ReadOnlyTools:
    names = ('list', 'read', 'search')

    def __init__(self, root):
        self.root = Path(root).resolve()

    def _path(self, name):
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root) or '.git' in path.relative_to(self.root).parts:
            raise ValueError('Path is outside the readable source tree')
        return path

    def call(self, operation, path='.', query=''):
        target = self._path(path)
        if operation == 'list':
            return '\n'.join(sorted(str(item.relative_to(self.root)).replace('\\', '/')
                                    for item in target.rglob('*')
                                    if item.is_file() and '.git' not in item.relative_to(self.root).parts
                                    and item.resolve().is_relative_to(self.root)))[:100000]
        if operation in ('read', 'search'):
            if not target.is_file() or target.stat().st_size > 1_000_000:
                raise ValueError('Read requires a source file of at most 1 MB')
            text = target.read_text(encoding='utf-8', errors='replace')
            if operation == 'read':
                return text
            return '\n'.join(f'{i}: {line}' for i, line in enumerate(text.splitlines(), 1)
                             if query in line)[:100000]
        raise ValueError('Unsupported read-only operation')


class LocalRuntime:
    def __init__(self, *, model_runner=None):
        self.model_runner = model_runner
        self.active = None

    def run_command(self, argv, *, cwd, timeout, env=None):
        """Explicit cwd and cancellation ownership also serve later role adapters."""
        self.active = subprocess.Popen(argv, cwd=cwd, env=env or clean_environment(),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True,
                                       start_new_session=os.name != 'nt')
        try:
            stdout, stderr = self.active.communicate(timeout=timeout)
            if self.active.returncode:
                raise RuntimeError('Local command failed')
            return stdout
        except subprocess.TimeoutExpired:
            self.cancel()
            raise RuntimeError('Local command timed out') from None
        finally:
            self.active = None

    def cancel(self):
        if self.active is not None and self.active.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.active.pid), '/T', '/F'],
                               capture_output=True, check=False)
            else:
                os.killpg(self.active.pid, signal.SIGKILL)
            self.active.communicate()

    def prepare_workspace(self, project, settings, *, command_runner=None, branch=None,
                          run_id=None, root=None, deadline=None, clock=time.monotonic):
        from urllib.parse import urlsplit

        url = project['http_url_to_repo']
        parsed = urlsplit(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Repository URL must not contain credentials or query parameters')
        branch = branch if branch is not None else project['default_branch']
        if not isinstance(branch, str) or not branch or branch.startswith('-'):
            raise ValueError('Project default branch is missing or invalid')
        run_id = run_id or uuid.uuid4().hex
        root = Path(root) if root is not None else settings.output_dir.resolve() / run_id
        # Never nest a product checkout inside another Git working tree.
        if any((parent / '.git').exists() for parent in (root, *root.parents)):
            raise ValueError('Output directory must be outside Git checkouts')
        artifacts = root / 'artifacts'
        artifacts.mkdir(parents=True, exist_ok=True)
        checkout = root / 'checkout'
        command = command_runner or self.run_command
        def timeout(limit):
            remaining = limit if deadline is None else min(limit, deadline - clock())
            if remaining <= 0:
                raise RuntimeError('Execute Run deadline expired')
            return remaining
        command(['git', '-c', 'core.hooksPath=' + str(artifacts / 'disabled-hooks'),
                 'clone', '--no-hardlinks', '--branch', branch, '--', url, str(checkout)],
                cwd=root, timeout=timeout(settings.timeout_seconds),
                env=_git_auth_env(settings.gitlab_token, settings.gitlab_git_username))
        commit = command(['git', 'rev-parse', 'HEAD'], cwd=checkout,
                         timeout=timeout(30), env=clean_environment()).strip()
        return Workspace(run_id, checkout, artifacts, branch, commit)

    def snapshot(self, workspace):
        # Include ignored/untracked source, index, refs and Git metadata as well as HEAD.
        return {str(path.relative_to(workspace.checkout)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in workspace.checkout.rglob('*') if path.is_file()}

    def run_role(self, *, role, workspace, prompt, settings):
        if role != 'planner':
            raise ValueError('Role is not enabled')
        model = settings.llm_model
        options = {'model': model if model.startswith('openai/') else 'openai/' + model,
                   'base_url': settings.llm_base_url.rstrip('/'), 'api_key': settings.llm_api_key,
                   'timeout': settings.timeout_seconds, 'num_retries': 0}
        context = dict(context_id=uuid.uuid4().hex, tools=ReadOnlyTools(workspace.checkout),
                       workspace=workspace.checkout, artifacts=workspace.artifacts,
                       prompt=prompt, model_options=options, max_iterations=settings.max_iterations)
        if self.model_runner:
            return self.model_runner(**context)
        payload = {key: str(value) if isinstance(value, Path) else value
                   for key, value in context.items() if key != 'tools'}
        # SDK discovery must not import user/project plugins, hooks, or MCP tools.
        # Set these before the fresh worker imports the SDK (discovery caches home).
        role_home = workspace.artifacts / 'role-home'
        role_home.mkdir()
        role_env = clean_environment()
        role_env.update(HOME=str(role_home), USERPROFILE=str(role_home),
                        OH_PERSISTENCE_DIR=str(role_home / '.openhands'),
                        OPENHANDS_SUPPRESS_BANNER='1')
        # Credentials travel in an anonymous pipe, never a file, argument, or child environment.
        self.active = subprocess.Popen([sys.executable, '-m', 'src.planner_worker'],
                                       cwd=Path(__file__).resolve().parent.parent,
                                       env=role_env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                       start_new_session=os.name != 'nt')
        try:
            stdout, stderr = self.active.communicate(json.dumps(payload), timeout=settings.timeout_seconds)
            if self.active.returncode:
                raise RuntimeError('Planner SDK execution failed')
            return json.loads(stdout)
        except subprocess.TimeoutExpired:
            self.cancel()
            raise RuntimeError('Planner timed out') from None
        finally:
            self.active = None
