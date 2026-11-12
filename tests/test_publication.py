"""Delivery through Application, with real temporary Git history and bare remotes."""
import copy
import hashlib
import json
import base64
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agreement import PLAN_SCHEMA, render_plan_comment
from src.gitlab_client import GitLabClient
from src.runtime import CommandExecutionError, CommandResult, LocalRuntime, Workspace
from src.workflow import Application, ApplicationServices, OperationInput
from test_explore import plan, settings
from test_implement import Transport
from test_review import approval
from test_execution import execution_app


# Git for Windows' cmd launcher creates a second process for each read. Use the
# same installed real Git binary directly when available; never fake its results.
GIT = Path(shutil.which('git') or 'git')
_native_git = GIT.parent.parent / 'mingw64/bin/git.exe'
if _native_git.is_file():
    GIT = _native_git


def git(repo, *args):
    return subprocess.check_output([str(GIT), '-C', str(repo), *args], text=True).strip()


def real_git(runtime, argv, kwargs):
    return runtime.run_command([str(GIT), *argv[1:]], **kwargs)


@pytest.fixture(scope='module')
def publication_seed(tmp_path_factory):
    seed = tmp_path_factory.mktemp('publication-seed')
    git(seed, 'init', '-b', 'main')
    git(seed, 'config', 'user.name', 'Human')
    git(seed, 'config', 'user.email', 'human@example.test')
    git(seed, 'config', 'core.autocrlf', 'false')
    (seed / 'frontend').mkdir()
    (seed / 'frontend/package.json').write_text('{"scripts":{"build":"fixture-build"}}')
    (seed / 'frontend/old.ts').write_text('old\n')
    (seed / 'frontend/t.ts').write_bytes(b'export const human = "preserved";\n')
    (seed / 'frontend/display.sh').write_text('echo old\n')
    (seed / '.gitattributes').write_text('frontend/*.ts text eol=lf\nfrontend/*.filtered filter=publication-test\n')
    git(seed, 'add', '.')
    git(seed, 'update-index', '--chmod=+x', 'frontend/display.sh')
    git(seed, 'commit', '-m', 'Human baseline')
    git(seed, 'checkout', '-b', 'human/task-2')
    (seed / 'human.txt').write_text('Human source work\n')
    git(seed, 'add', 'human.txt')
    git(seed, 'commit', '-m', 'Human source work')
    return seed, git(seed, 'rev-parse', 'HEAD')


@pytest.fixture
def publication_app(tmp_path, publication_seed, git_read_cache):
    seed, head = publication_seed
    remote = tmp_path / 'remote.git'
    subprocess.run([str(GIT), 'clone', '--bare', str(seed), str(remote)], check=True, capture_output=True)
    transport = Transport(remote)
    transport.issue['labels'] = ['team', 'agent::approved']
    agreement = {**plan(), 'schema': PLAN_SCHEMA, 'project_id': 42, 'issue_iid': 2,
        'branch': 'main', 'commit': head, 'scope': ['frontend'],
        'commands': [dict(module='frontend', cwd='frontend', argv=['fixture-build'],
                          evidence='Controlled frontend build validation')]}
    transport.notes = [{'id': 123, 'body': render_plan_comment(agreement)}]
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    calls, roles = [], []
    clock = [100.0]
    setup_workspace = [None]
    class Runtime(LocalRuntime):
        def prepare_workspace(self, project, config, *, root, branch, run_id, **kwargs):
            checkout = root / 'checkout'
            shutil.copytree(seed, checkout)
            self.checkout = checkout
            if setup_workspace[0]:
                setup_workspace[0](checkout)
            return Workspace(run_id, checkout, root / 'artifacts', branch, head)
        def close_coder(self, run_id):
            calls.append(('close_coder',))
            super().close_coder(run_id)
    runtime = Runtime()
    def invoke(*, change=None, reviewer=None, command_hook=None, close_hook=None,
               build_failure=False, report_failure=False, setup=None):
        setup_workspace[0] = setup
        def model(**context):
            payload = json.loads(context['prompt'])
            roles.append(payload['role'])
            if payload['role'] == 'coder':
                if change:
                    change(context)
                else:
                    context['tools'].call('write', 'frontend/task.ts', content='export const taskId = "TASK-2";\n',
                        reason='Approved task display', acceptance_ids=['A1'])
                return dict(summary='Show task IDs', blockers=[])
            return reviewer(payload, context) if reviewer else approval(payload)
        runtime.model_runner = model
        if close_hook:
            close = runtime.close_coder
            def close_coder(run_id):
                close(run_id)
                close_hook(runtime)
            runtime.close_coder = close_coder
        def command(argv, **kwargs):
            calls.append(tuple(argv))
            if command_hook:
                handled = command_hook(argv, kwargs, runtime)
                if handled is not None:
                    return handled
            if argv[0] != 'git':
                if build_failure:
                    raise RuntimeError('Required build unavailable')
                return 'Controlled build PASS'
            # Reuse actual immutable prepublication read evidence only for the
            # identical complete source/.git image. Publication always uses real
            # Git, including its own history, inventory, index and remote reads.
            if argv[1] != '-c':
                kwargs['env'] = {**kwargs['env'], 'GIT_OPTIONAL_LOCKS': '0'}
                image = runtime.snapshot(Workspace('', Path(kwargs['cwd']), tmp_path, '', ''))
                key = (tuple(argv), kwargs.get('binary', False),
                       tuple(sorted(kwargs['env'].items())),
                       hashlib.sha256(json.dumps(image, sort_keys=True).encode()).hexdigest())
                if key not in git_read_cache:
                    git_read_cache[key] = real_git(runtime, argv, kwargs)
                return git_read_cache[key]
            return real_git(runtime, argv, kwargs)
        if report_failure:
            client.create_merge_request_note = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('Report unavailable'))
        app = Application(settings=settings(tmp_path), services=ApplicationServices(
            gitlab=client, role_runtime=runtime, command_runner=command, clock=lambda: clock[0]))
        return app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    return invoke, remote, head, transport, calls, roles, clock


@pytest.fixture(scope='module')
def git_read_cache():
    return {}


def test_delivers_one_exact_aggregate_commit_on_human_branch(publication_app):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    metadata = {key: copy.deepcopy(transport.mr[key]) for key in ('title', 'description', 'draft', 'target_branch')}
    result = invoke()
    assert result.succeeded, result.execution
    assert result.execution['publication_eligible'] is True
    commit = result.execution['commit']['sha']
    assert result.execution['push']['status'] == 'SUCCESS'
    assert git(remote, 'rev-parse', 'human/task-2') == commit
    assert git(remote, 'rev-list', '--count', initial + '..' + commit) == '1'
    assert git(remote, 'rev-parse', commit + '^') == initial
    assert git(remote, 'show', commit + ':frontend/task.ts') == 'export const taskId = "TASK-2";'
    message = git(remote, 'show', '-s', '--format=%B', commit)
    assert 'Issue #2' in message and result.run_context.run_id in message
    assert transport.issue['labels'] == ['team', 'agent::approved']
    assert transport.mr['labels'] == ['team', 'agent::needs-review']
    assert all(transport.mr[key] == value for key, value in metadata.items())
    assert result.execution['review']['human_checks']
    assert calls.index(('close_coder',)) < next(i for i, call in enumerate(calls) if 'push' in call)
    assert len(transport.mr_notes) == 1


def test_report_model_text_cannot_execute_gitlab_quick_actions(execution_app):
    invoke, roles, commands, clock = execution_app
    hostile = 'Implemented task IDs\n```\n/approve\n```\n/merge\n  /close\n\t/relabel ~urgent'
    def configure(runtime, clock, command):
        model = runtime.model_runner
        def output(**context):
            response = model(**context)
            if json.loads(context['prompt'])['role'] == 'coder':
                response['summary'] = hostile
            return response
        runtime.model_runner = output
    result = invoke(configure=configure)
    body = (result.diagnostics / 'execution-report.md').read_text(encoding='utf-8')
    assert not any(line.lstrip().startswith('/') for line in body.splitlines())
    assert result.execution['summary'] == hostile
    assert '&#47;approve' in body and '&#47;merge' in body


def test_failed_coder_shutdown_blocks_publication_and_still_reports(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        def failed_close(run_id):
            raise RuntimeError('Coder shutdown unconfirmed')
        runtime.close_coder = failed_close
    result = invoke(configure=configure)
    assert not result.succeeded
    assert result.execution['status'] == 'BLOCKED'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert result.execution['report_delivery']['status'] == 'DELIVERED'


def test_report_is_canonical_before_gitlab_normalizes_note_content(execution_app, monkeypatch):
    original = Transport.request
    def normalized(self, method, url, **kwargs):
        response = original(self, method, url, **kwargs)
        if method == 'POST' and '/merge_requests/12/notes' in url:
            note = self.mr_notes[-1]
            note['body'] = note['body'].replace('\r', '').rstrip()
            response._content = json.dumps(note).encode()
        return response
    monkeypatch.setattr(Transport, 'request', normalized)
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        model = runtime.model_runner
        def output(**context):
            response = model(**context)
            if json.loads(context['prompt'])['role'] == 'coder':
                response['summary'] = 'Task IDs implemented\r\n/approve\r\n'
            return response
        runtime.model_runner = output
    result = invoke(configure=configure)
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    body = (result.diagnostics / 'execution-report.md').read_bytes()
    assert b'\r' not in body and body == body.rstrip()
    assert result.execution['summary'] == 'Task IDs implemented\r\n/approve\r\n'


def test_exact_inventory_includes_binary_literal_new_path_and_deletion_but_no_build_output(publication_app):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    def change(context):
        context['tools'].call('delete', 'frontend/old.ts', reason='Replace obsolete display', acceptance_ids=['A1'])
        context['tools'].call('write', 'frontend/[task].ts', content='export const task = "TASK-2";\n',
                              reason='Task display', acceptance_ids=['A1'])
        (context['workspace'] / 'frontend/icon.bin').write_bytes(b'\x00\xffTASK-2\r\n')
        context['tools'].call('write', 'frontend/display.sh', content='echo TASK-2\n',
                              reason='Task display helper', acceptance_ids=['A1'])
    def build(argv, kwargs, runtime):
        if argv == ['fixture-build']:
            (Path(kwargs['cwd']) / 'build-output.log').write_text('Operational build output')
    result = invoke(change=change, command_hook=build)
    assert result.succeeded, result.execution
    literal_new = next(row for row in result.execution['product_change']['files'] if row['path'] == 'frontend/[task].ts')
    assert literal_new['before'] is None
    sha = result.execution['commit']['sha']
    assert set(git(remote, 'diff-tree', '--no-commit-id', '--name-only', '-r', sha).splitlines()) == {
        'frontend/old.ts', 'frontend/[task].ts', 'frontend/icon.bin', 'frontend/display.sh'}
    assert subprocess.check_output([str(GIT), '-C', str(remote), 'show', sha + ':frontend/icon.bin']) == b'\x00\xffTASK-2\r\n'
    assert git(remote, 'ls-tree', sha, 'frontend/display.sh').startswith('100755 blob ')
    assert git(remote, 'show', sha + ':frontend/t.ts') == 'export const human = "preserved";'
    assert 'build-output.log' not in git(remote, 'ls-tree', '-r', '--name-only', sha)


@pytest.mark.parametrize('mutation', ['content', 'new-file', 'staged', 'commit', 'branch', 'metadata'])
def test_late_content_index_or_history_change_cannot_use_older_approval(publication_app, mutation):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    def late(runtime):
        checkout = runtime.checkout
        if mutation == 'content':
            (checkout / 'frontend/task.ts').write_text('Unreviewed late content')
        elif mutation == 'new-file':
            (checkout / 'frontend/unreviewed.ts').write_text('Unreviewed new file')
        elif mutation == 'staged':
            git(checkout, 'add', 'human.txt')
            (checkout / 'human.txt').write_text('Unexpected staged human change')
            git(checkout, 'add', 'human.txt')
        elif mutation == 'commit':
            git(checkout, 'add', 'frontend/task.ts')
            git(checkout, 'commit', '-m', 'Unauthorized coder commit')
        elif mutation == 'branch':
            git(checkout, 'switch', '-c', 'unapproved')
        else:
            git(checkout, 'config', 'core.autocrlf', 'true')
    result = invoke(close_hook=late)
    assert not result.succeeded
    assert result.execution['status'] == 'BLOCKED'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert git(remote, 'rev-parse', 'human/task-2') == initial
    assert not any('push' in call for call in calls)
    if mutation == 'staged':
        assert git(result.workspace, 'diff', '--cached', '--name-only') == 'human.txt'
    if mutation == 'commit':
        assert git(result.workspace, 'log', '-1', '--format=%s') == 'Unauthorized coder commit'


@pytest.mark.parametrize('scenario', ['remote-moved', 'push-race', 'rejected', 'uncertain-delivered', 'uncertain-undelivered', 'transport-lost-delivered', 'report-failed'])
def test_delivery_evidence_distinguishes_remote_commit_push_and_report_outcomes(publication_app, tmp_path, scenario):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    moved = []
    def advance_remote():
        mover = tmp_path / 'human-mover'
        subprocess.run([str(GIT), 'clone', '--branch', 'human/task-2', str(remote), str(mover)],
                       check=True, capture_output=True)
        (mover / 'human.txt').write_text('Intervening human work\n')
        git(mover, 'add', 'human.txt')
        git(mover, '-c', 'user.name=Human', '-c', 'user.email=human@example.test',
            'commit', '-m', 'Concurrent human work')
        git(mover, 'push', 'origin', 'human/task-2')
        moved.append(git(mover, 'rev-parse', 'HEAD'))
    if scenario == 'rejected':
        (remote / 'hooks/pre-receive').write_text('#!/bin/sh\nexit 1\n', newline='')
    readbacks = []
    def boundary(argv, kwargs, runtime):
        if argv[0] != 'git' or argv[1] != '-c':
            return
        if 'ls-remote' in argv:
            readbacks.append(True)
            if scenario == 'remote-moved' and not moved:
                advance_remote()
        if 'push' in argv:
            if scenario == 'push-race':
                advance_remote()
            elif scenario in {'uncertain-delivered', 'transport-lost-delivered'}:
                real_git(runtime, argv, kwargs)
                if scenario == 'transport-lost-delivered':
                    raise CommandExecutionError(CommandResult(list(argv), '', 'fatal: remote connection lost', 128, .1))
                raise RuntimeError('Connection lost after remote accepted delivery')
            elif scenario == 'uncertain-undelivered':
                raise RuntimeError('Connection lost before delivery confirmation')
    result = invoke(command_hook=boundary, report_failure=scenario == 'report-failed')
    sha = result.execution['commit']['sha']
    assert result.execution['commit']['status'] == 'SUCCESS'
    assert git(result.workspace, 'rev-list', '--count', initial + '..HEAD') == '1'
    assert git(result.workspace, 'rev-parse', 'HEAD^') == initial
    pushes = [call for call in calls if 'push' in call]
    assert all('--force' not in call and '--force-with-lease' not in call
               and not any(arg.startswith('+') for arg in call) for call in pushes)
    assert len(pushes) == (0 if scenario == 'remote-moved' else 1)
    if scenario in {'uncertain-delivered', 'transport-lost-delivered', 'report-failed'}:
        assert result.execution['push']['status'] == 'SUCCESS'
        assert git(remote, 'rev-parse', 'human/task-2') == sha
        assert result.succeeded is (scenario != 'report-failed')
        if scenario == 'report-failed':
            assert result.execution['status'] == 'SUCCESS'
            assert result.execution['report_delivery']['status'] == 'UNCERTAIN'
            assert result.execution['label_update']['status'] == 'UPDATED'
    else:
        assert not result.succeeded
        assert result.execution['push']['status'] == {
            'remote-moved': 'BLOCKED', 'push-race': 'FAILED', 'rejected': 'FAILED',
            'uncertain-undelivered': 'UNCERTAIN'}[scenario]
        assert git(remote, 'rev-parse', 'human/task-2') == (moved[0] if moved else initial)
    assert len(readbacks) == (2 if scenario.startswith(('uncertain-', 'transport-lost-')) else 1)
    assert set(git(remote, 'for-each-ref', '--format=%(refname)').splitlines()) == {
        'refs/heads/main', 'refs/heads/human/task-2'}


@pytest.mark.parametrize('transformation', ['crlf', 'filter'])
def test_canonical_staged_bytes_must_match_review_without_executing_configured_filters(publication_app, tmp_path, transformation):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    sentinel = tmp_path / 'filter-executed'
    def setup(checkout):
        if transformation == 'filter':
            git(checkout, 'config', 'filter.publication-test.clean',
                'sh -c "echo executed > ' + sentinel.as_posix() + '; cat"')
    def change(context):
        path = 'frontend/task.ts' if transformation == 'crlf' else 'frontend/task.filtered'
        context['tools'].call('write', path, content='export const task = "TASK-2";\r\n',
                              reason='Task display', acceptance_ids=['A1'])
    result = invoke(change=change, setup=setup)
    assert not result.succeeded
    assert result.execution['product_verification']['status'] == result.execution['review']['status'] == 'PASS'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert git(remote, 'rev-parse', 'human/task-2') == initial
    assert not sentinel.exists()
    if transformation == 'crlf':
        staged = subprocess.check_output([str(GIT), '-C', str(result.workspace), 'show', ':frontend/task.ts'])
        assert staged == b'export const task = "TASK-2";\n'
        assert (result.workspace / 'frontend/task.ts').read_bytes().endswith(b'\r\n')
    else:
        assert git(result.workspace, 'diff', '--cached', '--name-only') == ''


@pytest.mark.parametrize('destination_kind', ['local', 'https'])
def test_publication_suppresses_source_hooks_and_exposes_auth_only_to_isolated_https_transport(
        publication_app, tmp_path, monkeypatch, destination_kind):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    sentinel = tmp_path / 'hook-ran'
    accepted_url = 'https://gitlab.test/team/demo.git'
    if destination_kind == 'https':
        transport.project['http_url_to_repo'] = accepted_url
    def setup(checkout):
        for name in ('pre-commit', 'pre-push'):
            hook = checkout / '.git/hooks' / name
            hook.write_text('#!/bin/sh\necho executed > "' + sentinel.as_posix() + '"\nexit 1\n', newline='')
            hook.chmod(0o755)
    def late(runtime):
        monkeypatch.setenv('GIT_TRACE', str(tmp_path / 'inherited-trace'))
        monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
        monkeypatch.setenv('GIT_CONFIG_KEY_0', 'core.hooksPath')
        monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(runtime.checkout / '.git/hooks'))
        monkeypatch.setenv('GIT_DIR', str(tmp_path / 'incorrect-git-dir'))
        monkeypatch.setenv('GIT_ASKPASS', 'untrusted-askpass')
        monkeypatch.setenv('GIT_SSH_COMMAND', 'untrusted-ssh')
        monkeypatch.setenv('GITLAB_TOKEN', 'inherited-token')
        monkeypatch.setenv('LLM_API_KEY', 'inherited-key')
    observed = []
    def inspect(argv, kwargs, runtime):
        if argv[0] != 'git' or argv[1] != '-c':
            return
        env = kwargs['env']
        assert not set(env) & {'GIT_TRACE', 'GIT_DIR', 'GIT_ASKPASS', 'GIT_SSH_COMMAND', 'GITLAB_TOKEN', 'LLM_API_KEY'}
        hook_setting = next(arg for arg in argv if arg.startswith('core.hooksPath='))
        hooks = Path(hook_setting.split('=', 1)[1])
        assert not hooks.is_relative_to(runtime.checkout)
        assert hooks.is_dir() and not list(hooks.iterdir())
        network = 'push' in argv or 'ls-remote' in argv
        should_auth = network and destination_kind == 'https'
        assert ('GIT_CONFIG_COUNT' in env) is should_auth
        if network:
            observed.append((list(argv), dict(env)))
        if should_auth:
            assert env['GIT_CONFIG_KEY_0'] == 'http.extraHeader'
            assert env['GIT_CONFIG_VALUE_0'] == 'Authorization: Basic ' + base64.b64encode(b'agent-bot:secret-gitlab').decode()
            assert not Path(kwargs['cwd']).is_relative_to(runtime.checkout)
            # Controlled HTTPS network seam; actual history/push still uses the
            # local bare remote. Auth is removed before that local subprocess.
            local_env = {key: value for key, value in env.items() if not key.startswith('GIT_CONFIG_')
                         or key in {'GIT_CONFIG_NOSYSTEM', 'GIT_CONFIG_SYSTEM', 'GIT_CONFIG_GLOBAL'}}
            mapped = [str(remote) if arg == accepted_url else arg for arg in argv]
            return real_git(runtime, mapped, {**kwargs, 'env': local_env})
    result = invoke(setup=setup, close_hook=late, command_hook=inspect)
    monkeypatch.undo()
    assert result.succeeded, result.execution
    assert not sentinel.exists() and not (tmp_path / 'inherited-trace').exists()
    # A slow local transport can time out after acceptance; its one readback is
    # covered by the same sanitized transport boundary, never a second push.
    assert len(observed) in {2, 3}
    assert sum('push' in argv for argv, env in observed) == 1
    if len(observed) == 3:
        assert result.execution['push']['confirmation'] == 'single remote readback'
    assert git(remote, 'rev-parse', 'human/task-2') == result.execution['commit']['sha']
    retained = [result.workspace / '.git/config', *result.diagnostics.rglob('*')]
    auth = base64.b64encode(b'agent-bot:secret-gitlab')
    for path in retained:
        if path.is_file():
            data = path.read_bytes()
            assert b'secret-gitlab' not in data and b'secret-model' not in data and auth not in data


def test_expiry_after_coder_shutdown_keeps_finalization_reserve_and_denies_publication(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        close = runtime.close_coder
        def expired(run_id):
            close(run_id)
            clock[0] = 3671.0  # Work ended at 3670; report budget lasts until 3700.
        runtime.close_coder = expired
    result = invoke(configure=configure)
    assert not result.succeeded
    assert result.execution['status'] == 'TIMEOUT'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert result.execution['report_delivery']['status'] == 'DELIVERED'


def test_budget_expiry_before_push_preserves_local_commit_without_remote_delivery(publication_app):
    invoke, remote, initial, transport, calls, roles, clock = publication_app
    def expire_at_push(argv, kwargs, runtime):
        if argv[0] == 'git' and argv[1] == '-c' and 'push' in argv:
            clock[0] = 3671.0
    result = invoke(command_hook=expire_at_push)
    assert not result.succeeded
    assert result.execution['status'] == 'TIMEOUT'
    assert result.execution['commit']['status'] == 'SUCCESS'
    assert result.execution['push']['status'] == 'UNCERTAIN'
    assert git(remote, 'rev-parse', 'human/task-2') == initial
    assert git(result.workspace, 'rev-list', '--count', initial + '..HEAD') == '1'
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
