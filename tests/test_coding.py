import base64
from dataclasses import replace
import json
import sys

import pytest

from src.runtime import LocalRuntime
from src.workflow import Application, ApplicationServices, OperationInput, OutcomeStatus
from test_implement import Transport, approved
from test_explore import git, plan, settings, source


@pytest.fixture
def controlled_coding(tmp_path):
    """Accepted public workflow with controlled Git responses for non-history cases."""
    from src.runtime import Workspace
    from src.agreement import PLAN_SCHEMA, render_plan_comment
    from src.gitlab_client import GitLabClient

    transport = Transport(tmp_path / 'unused-source')
    transport.issue['labels'] = ['team', 'agent::approved']
    fixture_plan = {**plan(), 'commands': [dict(module=module, cwd=module,
        argv=[sys.executable, '-c', 'from pathlib import Path; assert Path.cwd().is_dir(); print("fixture build validation")'],
        evidence='Controlled fixture directory validation at the command boundary')
        for module in ['frontend', 'backend']]}
    transport.notes = [{'id': 123, 'body': render_plan_comment({**fixture_plan, 'schema': PLAN_SCHEMA,
        'project_id': 42, 'issue_iid': 2, 'branch': 'main', 'commit': 'a' * 40})}]
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    app = Application(settings=settings(tmp_path), services=ApplicationServices(gitlab=client, clock=lambda: 100.0))
    class Runtime(LocalRuntime):
        def prepare_workspace(self, project, settings, *, run_id, root, branch, **kwargs):
            checkout, artifacts = root / 'checkout', root / 'artifacts'
            checkout.mkdir()
            (checkout / 'frontend').mkdir()
            (checkout / 'backend').mkdir()
            return Workspace(run_id, checkout, artifacts, branch, 'a' * 40)

        def run_command(self, argv, **kwargs):
            if argv[0] != 'git':
                return super().run_command(argv, **kwargs)
            args = argv[1:]
            if args == ['rev-parse', 'HEAD']:
                return 'a' * 40
            if args == ['symbolic-ref', '--short', 'HEAD']:
                return 'human/task-2'
            if args == ['show-ref']:
                return 'a' * 40 + ' refs/heads/human/task-2'
            return ''

    runtime = Runtime()
    def invoke(model, *, api_key=None, issue_description=None):
        transport.mr['labels'] = ['team', 'agent::implement']
        transport.mr_notes.clear()
        if api_key is not None:
            app.settings = replace(app.settings, llm_api_key=api_key)
        if issue_description is not None:
            transport.issue['description'] = issue_description
        runtime.model_runner = model
        app.services = replace(app.services, role_runtime=runtime)
        return app.invoke(OperationInput('implement', 'team/demo', 2, 12))

    return invoke


def test_denied_configuration_edit_forces_controlled_stop_even_if_coder_ignores_error(controlled_coding):
    def coder(**context):
        for path in ('frontend/package.json', 'frontend/vite.config.ts', 'backend/build.gradle', 'backend/migrations/001.sql'):
            with pytest.raises(ValueError, match='explicit approved coverage'):
                context['tools'].call('write', path, content='{}',
                    reason='Convenient upgrade', acceptance_ids=['A1'])
        return {'summary': 'All done', 'blockers': [], 'checks': [{'status': 'PASS'}],
                'push': {'status': 'SUCCESS'}}

    result = controlled_coding(coder)
    assert result.execution['status'] == 'BLOCKED'
    assert 'explicit approved coverage' in ' '.join(result.execution['blockers'])
    assert result.execution['checks'] == []
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert not (result.workspace / 'frontend/package.json').exists()


def test_scope_setup_and_unchanged_stops_never_reuse_prior_coder_context(controlled_coding, monkeypatch):
    monkeypatch.setenv('GITLAB_TOKEN', 'coordinator-gitlab-secret')
    monkeypatch.setenv('LLM_API_KEY', 'coordinator-model-secret')
    context_ids = set()
    for scenario, expected in [('scope', 'requires a new requirement'), ('setup', 'Repository command failed'),
                               ('host', 'host toolchain'), ('unchanged', 'no Product Change'),
                               ('narrative', 'structured coding outcome')]:
        def coder(**context):
            assert context['context_id'] not in context_ids
            context_ids.add(context['context_id'])
            assert context['state'] == {}
            context['state']['memory'] = 'This run only'
            if scenario == 'narrative':
                return 'Completed successfully'
            if scenario == 'scope':
                return {'summary': 'Stopped', 'blockers': ['Approved behavior requires a new requirement and human reset.']}
            if scenario == 'setup':
                output = context['tools'].call('command', argv=[sys.executable, '-c',
                    'import os,json; print(json.dumps({k:os.environ.get(k) for k in ["GITLAB_TOKEN","LLM_API_KEY"]}))'],
                    reason='Inspect sanitized command environment')
                assert json.loads(output) == {'GITLAB_TOKEN': None, 'LLM_API_KEY': None}
                with pytest.raises(ValueError, match='dependency/environment blockers'):
                    context['tools'].call('command', argv=[sys.executable, '-c', 'raise SystemExit(2)'],
                                          reason='Normal repository dependency setup')
            if scenario == 'host':
                with pytest.raises(ValueError, match='host toolchain'):
                    context['tools'].call('command', argv=['npm', 'install', '--global', 'npm@latest'],
                                          reason='Upgrade host to work around setup')
            return {'summary': 'Coder finished', 'blockers': []}

        result = controlled_coding(coder)
        assert result.execution['status'] == 'BLOCKED', scenario
        assert expected in ' '.join(result.execution['blockers']), (scenario, result.execution)
        assert not result.succeeded
        assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
        if scenario == 'setup':
            assert result.execution['commands'][-1]['status'] == 'BLOCKED'


def test_escaped_credentials_are_redacted_before_prompt_and_artifact_serialization(controlled_coding):
    secret = 'model-"key\\with\nnewlines'
    prompts = []
    def coder(**context):
        prompts.append(json.loads(context['prompt']))
        return {'summary': 'Observed ' + secret, 'blockers': ['Environment unavailable']}

    result = controlled_coding(coder, api_key=secret, issue_description='Show task IDs; ' + secret)
    assert prompts[0]['issue']['description'] == 'Show task IDs; [REDACTED]'
    assert json.loads((result.diagnostics / 'coding-result.json').read_text())['summary'] == 'Observed [REDACTED]'
    assert result.execution['summary'] == 'Observed [REDACTED]'


def test_relevant_json_assets_do_not_require_configuration_approval(controlled_coding):
    def coder(**context):
        context['tools'].call('write', 'frontend/assets/task-labels.json', content='{"task": "TASK-2"}',
                              reason='Task identifier display asset', acceptance_ids=['A1'])
        return {'summary': 'Task label asset', 'blockers': []}

    result = controlled_coding(coder)
    assert (result.workspace / 'frontend/assets/task-labels.json').read_text() == '{"task": "TASK-2"}'


@pytest.mark.parametrize('failure', ['crash', 'commit', 'stage', 'metadata'])
def test_rejected_coding_retains_full_content_inventory_without_adopting_history(approved, failure):
    app, transport, source = approved
    initial = git(source, 'rev-parse', 'HEAD')

    def coder(**context):
        root = context['workspace']
        (root / 'human.txt').unlink()
        (root / 'frontend/new image.bin').write_bytes(b'\x00\xff\x01\x0d\x0a')
        if failure == 'crash':
            raise RuntimeError('secret-model must not leak')
        if failure == 'metadata':
            (root / '.git/config').write_text('[remote "origin"]\nurl = https://unapproved.example/repo\n')
            return {'summary': 'Changed metadata', 'blockers': []}
        git(root, 'add', '--', 'human.txt', 'frontend/new image.bin')
        if failure == 'commit':
            git(root, '-c', 'user.name=Agent', '-c', 'user.email=agent@test', 'commit', '-m', 'Unexpected commit')
        return {'summary': 'Done', 'blockers': []}

    app.services = replace(app.services, role_runtime=LocalRuntime(model_runner=coder))
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    candidate = result.execution
    assert candidate['status'] == 'BLOCKED'
    assert candidate['changed_files'] == ['frontend/new image.bin', 'human.txt']
    files = candidate['product_change']['files']
    assert base64.b64decode(files[0]['after']['content_base64']) == b'\x00\xff\x01\x0d\x0a'
    assert base64.b64decode(files[1]['before']['content_base64']) == b'Existing human work'
    assert files[1]['after'] is None
    assert candidate['product_change']['initial_head'] == initial
    assert not result.succeeded
    if failure != 'crash':
        assert 'Unexpected commit, history, branch or staged content' in candidate['blockers'][0]
    assert 'secret-model' not in (result.diagnostics / 'coding-result.json').read_text()
    assert git(source, 'rev-parse', 'HEAD') == initial


def test_explicit_dependency_scope_allows_configuration_without_exhaustive_file_approval(approved):
    from src.agreement import parse_plan_comment, render_plan_comment
    app, transport, source = approved
    reference = 'Update frontend dependencies to display task identifiers.'
    agreement = parse_plan_comment(transport.notes[0]['body'])
    agreement['checklist'][0]['description'] = reference
    transport.notes[0]['body'] = render_plan_comment(agreement)

    def coder(**context):
        context['tools'].call('write', 'frontend/package.json',
            content='{"scripts":{"build":"ng build"},"dependencies":{"task-ids":"1.0.0"}}',
            reason='Approved task ID dependency', acceptance_ids=['A1'], approval_reference=reference)
        context['tools'].call('command', argv=[sys.executable, '-c',
            'from pathlib import Path; Path("package-lock.json").write_text("{}")'], path='frontend',
            reason='Normal scoped dependency lockfile setup', approval_reference=reference)
        return {'summary': 'Add scoped task ID dependency', 'blockers': []}

    app.services = replace(app.services, role_runtime=LocalRuntime(model_runner=coder))
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.execution['status'] == 'UNVERIFIED', result.execution
    assert result.execution['changed_files'] == ['frontend/package-lock.json', 'frontend/package.json']


def test_implement_returns_complete_local_candidate_preserving_human_history(approved):
    app, transport, source = approved
    history = git(source, 'log', '--format=%H')
    calls = []

    def coder(**context):
        calls.append(context)
        agreement = json.loads(context['prompt'])
        assert agreement['approved_plan']['requirement'] == 'Show task IDs'
        assert agreement['issue']['description'] == 'Show task IDs'
        tools = context['tools']
        tools.call('write', 'frontend/task ids.ts', content='export const taskId = 2;\n',
                   reason='Show task identifiers', acceptance_ids=['A1'])
        tools.call('write', 'backend/Task.java', content='class Task { int id = 2; }\n',
                   reason='Expose task identifier', acceptance_ids=['A1'])
        return {'summary': 'Expose task IDs', 'blockers': []}

    app.services = replace(app.services, role_runtime=LocalRuntime(model_runner=coder))
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.execution is not None, result
    candidate = result.execution
    assert candidate['status'] == 'UNVERIFIED'
    assert candidate['changed_files'] == ['backend/Task.java', 'frontend/task ids.ts']
    inventory = candidate['product_change']['files']
    assert base64.b64decode(inventory[1]['after']['content_base64']) == b'export const taskId = 2;\n'
    assert inventory[1]['before'] is None
    assert len(candidate['product_change']['identity']) == 64
    assert git(result.workspace, 'log', '--format=%H') == history
    assert git(result.workspace, 'diff', '--cached', '--name-only') == ''
    assert git(source, 'status', '--porcelain') == ''
    assert not result.succeeded and result.status == OutcomeStatus.UNAVAILABLE
    assert len(calls) == 1
    assert calls[0]['artifacts'] == result.diagnostics
    coding_result = json.loads((result.diagnostics / 'coding-result.json').read_text())
    assert all(candidate[key] == value for key, value in coding_result.items())
    assert [(method, path) for method, path, _ in transport.calls if method == 'POST'] == [
        ('POST', 'projects/42/merge_requests/12/notes')]
    assert {item.name for item in result.workspace.iterdir()} == {'.git', 'frontend', 'backend', 'AGENTS.md', 'human.txt'}


def test_coder_sdk_offers_scoped_tools_and_keeps_conversation_only_within_run(approved):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    app, transport, source = approved
    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            tools = [item['function'] for item in request['tools']]
            tool = next(item for item in tools if 'operation' in item['parameters'].get('properties', {}))
            assert len(tools) == 2
            assert request['model'] == 'team-model'
            operations = tool['parameters']['properties']['operation']['enum']
            assert set(operations) == {'list', 'read', 'search', 'write', 'delete', 'command', 'submit'}
            if len(requests) == 1:
                name, arguments = tool['name'], dict(operation='write', path='frontend/task.ts',
                    content='export const taskId = 2;', reason='Task identifier', acceptance_ids=['A1'])
            elif len(requests) == 2:
                name, arguments = tool['name'], dict(operation='submit', outcome={'summary': 'Task identifier visible', 'blockers': []})
            else:
                name, arguments = next(item['name'] for item in tools if item != tool), {'message': 'Complete'}
            body = json.dumps({'id': 'test', 'object': 'chat.completion', 'created': 1,
                'model': 'team-model', 'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                    'message': {'role': 'assistant', 'content': None, 'tool_calls': [
                        {'id': str(len(requests)), 'type': 'function',
                         'function': {'name': name, 'arguments': json.dumps(arguments)}}]}}],
                'usage': {'prompt_tokens': 10, 'completion_tokens': 10, 'total_tokens': 20}}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    proxy = ThreadingHTTPServer(('127.0.0.1', 0), Proxy)
    thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    thread.start()
    runtime = LocalRuntime()
    app.settings = replace(app.settings, llm_base_url=f'http://127.0.0.1:{proxy.server_port}/v1', timeout_seconds=60)
    app.services = replace(app.services, role_runtime=runtime)
    try:
        result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join()
    assert result.execution['status'] == 'UNVERIFIED', result.execution
    assert result.execution['changed_files'] == ['frontend/task.ts']
    assert len(requests) == 3
    assert (result.workspace / 'frontend/task.ts').read_text() == 'export const taskId = 2;'
    runtime.close_coder(result.run_context.run_id)
    for path in result.diagnostics.rglob('*'):
        if path.is_file():
            assert b'secret-model' not in path.read_bytes()
            assert b'secret-gitlab' not in path.read_bytes()
