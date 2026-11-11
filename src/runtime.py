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
import queue
import threading
import shutil
from dataclasses import dataclass

from .git_ops import _git_auth_env


def clean_environment():
    return {key: value for key, value in os.environ.items()
            if key not in {'GITLAB_TOKEN', 'LLM_API_KEY', 'GIT_CONFIG_COUNT'}
            and not key.startswith(('GIT_CONFIG_KEY_', 'GIT_CONFIG_VALUE_'))}


@dataclass(frozen=True)
class CommandResult:
    argv: list[str]
    stdout: str
    stderr: str
    exit_code: int | None
    duration_seconds: float
    reason: str = ''


class CommandExecutionError(RuntimeError):
    def __init__(self, result):
        super().__init__(result.reason or 'Local command failed')
        self.result = result


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
        self.coding_sessions = {}

    def run_command(self, argv, *, cwd, timeout, env=None):
        """Keep stdout compatibility for Git and role callers."""
        return self.run_command_result(argv, cwd=cwd, timeout=timeout, env=env).stdout

    def run_command_result(self, argv, *, cwd, timeout, env=None):
        """Retain actual argv, both streams and timing through one cancellation owner."""
        started = time.monotonic()
        argv = list(argv)
        launch = argv
        executable = shutil.which(argv[0]) or argv[0]
        if os.name == 'nt' and Path(executable).suffix.lower() in {'.cmd', '.bat'}:
            if any(any(c in argument for c in '\r\n%!&|<>^') for argument in argv):
                raise ValueError('Unsupported shell metacharacter in native batch arguments')
            argv = [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c',
                    '"' + subprocess.list2cmdline([executable, *argv[1:]]) + '"']
            # Popen's normal list conversion would escape this cmd /c tail a
            # second time; cmd requires the deliberate outer quoting verbatim.
            launch = subprocess.list2cmdline(argv[:4]) + ' ' + argv[4]
        self.active = subprocess.Popen(launch, cwd=cwd, env=env or clean_environment(),
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True,
                                       start_new_session=os.name != 'nt')
        try:
            stdout, stderr = self.active.communicate(timeout=timeout)
            if self.active.returncode:
                raise CommandExecutionError(CommandResult(list(argv), stdout, stderr,
                    self.active.returncode, time.monotonic() - started))
            return CommandResult(argv, stdout, stderr, 0, time.monotonic() - started)
        except subprocess.TimeoutExpired:
            stdout, stderr = self.cancel() or ('', '')
            raise CommandExecutionError(CommandResult(argv, stdout, stderr, None,
                time.monotonic() - started, 'Local command timed out')) from None
        finally:
            self.active = None

    def cancel(self):
        if self.active is not None and self.active.poll() is None:
            if os.name == 'nt':
                subprocess.run(['taskkill', '/PID', str(self.active.pid), '/T', '/F'],
                               capture_output=True, check=False)
            else:
                os.killpg(self.active.pid, signal.SIGKILL)
            return self.active.communicate()

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
        if role == 'coder':
            return self._run_coder(workspace, prompt, settings)
        if role not in {'planner', 'reviewer'}:
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
        role_home = workspace.artifacts / (role + '-home-' + context['context_id'])
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
                raise RuntimeError(role.capitalize() + ' SDK execution failed')
            return json.loads(stdout)
        except subprocess.TimeoutExpired:
            self.cancel()
            raise RuntimeError(role.capitalize() + ' timed out') from None
        finally:
            self.active = None

    def _run_coder(self, workspace, prompt, settings):
        from .coding import CodingTools

        agreement = json.loads(prompt)
        session = self.coding_sessions.get(workspace.run_id)
        if session is None:
            tools = CodingTools(workspace.checkout, agreement=agreement['approved_plan'],
                                issue=agreement['issue'], runtime=self, timeout=settings.timeout_seconds)
            model = settings.llm_model
            session = dict(context_id=uuid.uuid4().hex, workspace=workspace.checkout,
                artifacts=workspace.artifacts, tools=tools, state={}, max_iterations=settings.max_iterations,
                model_options={'model': model if model.startswith('openai/') else 'openai/' + model,
                    'base_url': settings.llm_base_url.rstrip('/'), 'api_key': settings.llm_api_key,
                    'timeout': settings.timeout_seconds, 'num_retries': 0})
            self.coding_sessions[workspace.run_id] = session
        if session['workspace'] != workspace.checkout:
            raise ValueError('Coding conversation belongs to another workspace')
        if self.model_runner:
            response = self.model_runner(**session, prompt=prompt)
            if isinstance(response, dict):
                response = {**response, 'changes': list(session['tools'].changes.values()),
                            'commands': session['tools'].commands, 'tool_blockers': session['tools'].blockers}
            return response
        return self._coder_worker(session, prompt, settings)

    def _coder_worker(self, session, prompt, settings):
        if 'process' not in session:
            role_home = session['artifacts'] / ('coder-home-' + session['context_id'])
            role_home.mkdir()
            role_env = clean_environment()
            role_env.update(HOME=str(role_home), USERPROFILE=str(role_home),
                            OH_PERSISTENCE_DIR=str(role_home / '.openhands'), OPENHANDS_SUPPRESS_BANNER='1')
            process = subprocess.Popen([sys.executable, '-m', 'src.coder_worker'],
                cwd=Path(__file__).resolve().parent.parent, env=role_env, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                start_new_session=os.name != 'nt')
            lines = queue.Queue()
            def read_lines():
                for line in process.stdout:
                    lines.put(line)
                lines.put(None)
            threading.Thread(target=read_lines, daemon=True).start()
            session.update(process=process, lines=lines)
        self.active = session['process']
        try:
            payload = {key: str(value) if isinstance(value, Path) else value for key, value in session.items()
                       if key in {'context_id', 'workspace', 'artifacts', 'model_options', 'max_iterations'}}
            payload.update(prompt=prompt, agreement=session['tools'].agreement, issue=session['tools'].issue)
            self.active.stdin.write(json.dumps(payload) + '\n')
            self.active.stdin.flush()
            line = session['lines'].get(timeout=settings.timeout_seconds)
            if line is None:
                raise RuntimeError('Coder worker exited before structured output')
            message = json.loads(line)
            if 'error' in message:
                raise RuntimeError('Coder SDK execution failed')
            return message['result']
        except (queue.Empty, BrokenPipeError):
            self.cancel()
            raise RuntimeError('Coder timed out or exited') from None
        finally:
            self.active = None

    def close_coder(self, run_id):
        """Release the run-local conversation after finalization; never save it for reuse."""
        session = self.coding_sessions.pop(run_id, None)
        if session and 'process' in session:
            self.active = session['process']
            try:
                self.cancel()
            finally:
                self.active = None
