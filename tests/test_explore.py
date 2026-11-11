import json
import subprocess
from pathlib import Path

import pytest

from src.config import Settings
from src.workflow import Application, ApplicationServices, OperationInput, OutcomeStatus


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.email', 'test@example.test')
    git(repo, 'config', 'user.name', 'Test')
    for name, content in {'frontend/package.json': '{"scripts":{"build":"ng build"}}',
                          'backend/pom.xml': '<project/>', 'AGENTS.md': 'Read both modules.'}.items():
        file = repo / name
        file.parent.mkdir(exist_ok=True)
        file.write_text(content)
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'pilot')
    return repo


class GitLab:
    labels = ['agent::explore', 'team']
    notes = []

    def __init__(self, source):
        self.source = source

    def get_project(self, project):
        return {'id': 42, 'default_branch': 'main', 'http_url_to_repo': str(self.source)}

    def get_issue(self, project, iid):
        return {'iid': iid, 'project_id': 42, 'title': 'Task identifiers',
                'description': 'Show task IDs', 'labels': self.labels}

    def get_issue_notes(self, project, iid):
        return self.notes


def settings(tmp_path, model='team-model'):
    return Settings.from_env(output_dir=tmp_path / 'runs', environ={
        'GITLAB_TOKEN': 'secret-gitlab', 'LLM_API_KEY': 'secret-model',
        'LLM_BASE_URL': 'https://proxy.example/v1/', 'LLM_MODEL': model})


def plan():
    return {'requirement': 'Show task IDs', 'scope': ['frontend', 'backend'],
            'checklist': [{'id': 'T1', 'description': 'Expose IDs', 'acceptance_ids': ['A1']}],
            'acceptance': [{'id': 'A1', 'description': 'Task IDs visible'}],
            'verification': [{'description': 'Build both modules'}],
            'commands': [], 'unresolved_questions': []}


def test_explore_inspects_fresh_checkout_and_returns_unpublished_observations(source, tmp_path):
    from src.runtime import LocalRuntime

    contexts = []
    initial = (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'),
               (source / '.git/index').read_bytes())

    def model(**context):
        contexts.append(context)
        tools = context['tools']
        assert tools.call('read', 'AGENTS.md') == 'Read both modules.'
        assert 'frontend/package.json' in tools.call('list')
        assert 'ng build' in tools.call('search', 'frontend/package.json', 'build')
        for forbidden in ['edit', 'terminal', 'commit', 'push', 'gitlab_write']:
            with pytest.raises(ValueError):
                tools.call(forbidden)
        with pytest.raises(ValueError):
            tools.call('read', '../artifacts/plan.json')
        return plan()

    app = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=GitLab(source), role_runtime=LocalRuntime(model_runner=model)))
    first = app.invoke(OperationInput('explore', 'team/demo', 2))
    second = app.invoke(OperationInput('explore', 'team/demo', 2))
    assert first.status == OutcomeStatus.UNAVAILABLE
    assert first.inspection['commit'] == initial[0]
    assert first.inspection['project_id'] == 42
    assert first.inspection['modules'] == ['backend', 'frontend']
    assert first.inspection['plan']['requirement'] == 'Show task IDs'
    assert first.workspace != second.workspace
    assert len(contexts) == 2
    assert contexts[0]['context_id'] != contexts[1]['context_id']
    assert first.workspace.name == 'checkout'
    assert first.diagnostics.parent == first.workspace.parent
    assert first.diagnostics != first.workspace
    assert git(first.workspace, 'status', '--porcelain') == ''
    assert git(first.workspace, 'branch', '--show-current') == 'main'
    assert initial == (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain'),
                       (source / '.git/index').read_bytes())
    artifact = json.loads((first.diagnostics / 'inspection.json').read_text())
    assert artifact == first.inspection
    assert 'secret-' not in json.dumps(artifact)


@pytest.mark.parametrize('labels,notes', [([], []), (['agent::explore', 'agent::approved'], []),
    (['agent::explore'], [{'body': '<!-- coding-agent:execution-plan --> old plan'}])])
def test_explore_rejects_invalid_gitlab_state_before_creating_workspace(source, tmp_path, labels, notes):
    client = GitLab(source)
    client.labels, client.notes = labels, notes
    result = Application(settings=settings(tmp_path), services=ApplicationServices(gitlab=client)).invoke(
        OperationInput('explore', 'team/demo', 2))
    assert result.status == OutcomeStatus.FAILED
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('alias,expected', [('team-model', 'openai/team-model'),
    ('openai/team-model', 'openai/team-model'), ('team/alias', 'openai/team/alias')])
def test_proxy_connection_preserves_api_root_and_alias(source, tmp_path, alias, expected):
    from src.runtime import LocalRuntime

    def model(**context):
        assert context['model_options'] == {'model': expected, 'base_url': 'https://proxy.example/v1',
            'api_key': 'secret-model', 'timeout': 1800, 'num_retries': 0}
        return plan()

    result = Application(settings=settings(tmp_path, alias), services=ApplicationServices(
        gitlab=GitLab(source), role_runtime=LocalRuntime(model_runner=model))).invoke(
            OperationInput('explore', 'team/demo', 2))
    assert result.inspection is not None


def test_explore_fetches_every_page_of_issue_discussion(source, tmp_path):
    import requests
    from src.gitlab_client import GitLabClient
    from src.runtime import LocalRuntime

    class Transport:
        def request(self, method, url, **kwargs):
            assert method == 'GET'
            response = requests.Response()
            response.status_code = 200
            if url.endswith('/notes'):
                page = kwargs['params']['page']
                body = [{'body': 'Discussion ' + str(page)}] if page < 3 else []
            elif url.endswith('/issues/2'):
                body = GitLab(source).get_issue(42, 2)
            else:
                body = GitLab(source).get_project(42)
            response._content = json.dumps(body).encode()
            return response

    client = GitLabClient('https://gitlab.test', 'secret-gitlab')
    client.session = Transport()

    def model(**context):
        assert 'Discussion 1' in context['prompt']
        assert 'Discussion 2' in context['prompt']
        return plan()

    result = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=client, role_runtime=LocalRuntime(model_runner=model))).invoke(
            OperationInput('explore', 'team/demo', 2))
    assert result.inspection is not None


@pytest.mark.parametrize('behavior', ['mutate', 'malformed', 'crash'])
def test_planner_failures_preserve_diagnostics_without_claiming_success(source, tmp_path, behavior):
    from src.runtime import LocalRuntime

    def model(**context):
        if behavior == 'mutate':
            (context['workspace'] / 'AGENTS.md').write_text('Changed')
        elif behavior == 'malformed':
            return {'requirement': 'Incomplete'}
        else:
            raise RuntimeError('secret-gitlab secret-model')
        return plan()

    result = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=GitLab(source), role_runtime=LocalRuntime(model_runner=model))).invoke(
            OperationInput('explore', 'team/demo', 2))
    assert result.status == OutcomeStatus.FAILED
    assert result.diagnostics.is_dir()
    assert (result.diagnostics / 'failure.json').is_file()
    assert 'secret-' not in (result.diagnostics / 'failure.json').read_text()
    assert git(source, 'status', '--porcelain') == ''


def test_application_constructs_sdk_with_only_readonly_tool_and_structured_submit(source, tmp_path):
    from types import SimpleNamespace
    from src.runtime import LocalRuntime
    from src.planner_worker import run_sdk
    from openhands.sdk.tool import registry

    class LLM:
        def __init__(self, **kwargs):
            assert kwargs['model'] == 'openai/team-model'
            assert kwargs['base_url'] == 'https://proxy.example/v1'

    class Agent:
        def __init__(self, *, llm, tools):
            assert len(tools) == 1
            self.tools = tools

    class Conversation:
        def __init__(self, *, agent, workspace, max_iteration_per_run):
            assert Path(workspace).name == 'sdk-workspace'
            assert Path(workspace).parent.name == 'artifacts'
            self.tool_name = agent.tools[0].name

        def send_message(self, prompt):
            assert json.loads(prompt)['role'] == 'planner'

        def run(self):
            import openhands.sdk as real_sdk
            tool = registry.resolve_tool(real_sdk.Tool(name=self.tool_name), None)[0]
            assert tool.executor(tool.action_type(operation='read', path='AGENTS.md')).text == 'Read both modules.'
            with pytest.raises(ValueError):
                tool.action_type(operation='edit')
            tool.executor(tool.action_type(operation='submit', observations=plan()))

        def close(self):
            pass

    import openhands.sdk as real_sdk
    sdk = SimpleNamespace(LLM=LLM, Agent=Agent, Tool=real_sdk.Tool, Conversation=Conversation)
    def model(**context):
        return run_sdk(context, sdk=sdk)
    result = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=GitLab(source), role_runtime=LocalRuntime(model_runner=model))).invoke(
            OperationInput('explore', 'team/demo', 2))
    assert result.inspection is not None


def test_cli_default_workspace_is_outside_framework_checkout(source, tmp_path, monkeypatch):
    from io import StringIO
    from main import main
    from src.runtime import LocalRuntime

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    observed = []

    def model(**context):
        observed.append(context['workspace'])
        return plan()

    output = StringIO()
    code = main(['explore', '--project', 'team/demo', '--issue', '2'],
                environ={'GITLAB_TOKEN': 'secret-gitlab', 'LLM_API_KEY': 'secret-model',
                         'LLM_BASE_URL': 'https://proxy.example/v1', 'LLM_MODEL': 'team-model'},
                services=ApplicationServices(gitlab=GitLab(source),
                    role_runtime=LocalRuntime(model_runner=model)), stderr=output)
    assert code == 4
    assert len(observed) == 1
    assert not observed[0].is_relative_to(Path.cwd())
    assert 'inspection complete' in output.getvalue()


def test_application_cancels_timed_out_local_command_and_its_child(source, tmp_path):
    import sys
    import time
    from src.runtime import LocalRuntime

    delayed = tmp_path / 'delayed.txt'
    ready = tmp_path / 'ready.txt'
    child = tmp_path / 'child.py'
    child.write_text('import time\nfrom pathlib import Path\n'
                     f'Path({str(ready)!r}).write_text("ready")\n'
                     'time.sleep(2)\n'
                     f'Path({str(delayed)!r}).write_text("must not happen")\n')
    parent = tmp_path / 'parent.py'
    parent.write_text('import subprocess, sys, time\n'
                      f'subprocess.Popen([sys.executable, {str(child)!r}])\n'
                      'time.sleep(20)\n')
    runtime = LocalRuntime(model_runner=lambda **context: plan())

    def command(argv, **kwargs):
        if 'clone' in argv:
            return runtime.run_command([sys.executable, str(parent)], cwd=tmp_path, timeout=1)
        return runtime.run_command(argv, **kwargs)

    result = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=GitLab(source), role_runtime=runtime, command_runner=command)).invoke(
            OperationInput('explore', 'team/demo', 2))
    assert result.status == OutcomeStatus.FAILED
    assert ready.exists()
    time.sleep(2.2)
    assert not delayed.exists()


def test_explore_runs_real_sdk_worker_against_controlled_proxy(source, tmp_path):
    from dataclasses import replace
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading

    requests = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(request)
            offered = request['tools']
            inspection = next(tool['function'] for tool in offered
                              if 'operation' in tool['function']['parameters'].get('properties', {}))
            assert len(offered) == 2  # read-only inspection + SDK finish
            assert request['model'] == 'team-model'
            if len(requests) == 1:
                name, arguments = inspection['name'], {'operation': 'read', 'path': 'AGENTS.md'}
            elif len(requests) == 2:
                assert 'Read both modules.' in json.dumps(request['messages'])
                name, arguments = inspection['name'], {'operation': 'submit', 'observations': plan()}
            else:
                finish = next(tool['function'] for tool in offered
                              if tool['function']['name'] != inspection['name'])
                name, arguments = finish['name'], {'message': 'Inspection complete'}
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
    try:
        config = replace(settings(tmp_path), llm_base_url=f'http://127.0.0.1:{proxy.server_port}/v1',
                         timeout_seconds=60)
        result = Application(settings=config, services=ApplicationServices(gitlab=GitLab(source))).invoke(
            OperationInput('explore', 'team/demo', 2))
    finally:
        proxy.shutdown()
        proxy.server_close()
        thread.join()
    assert result.inspection is not None, result
    assert result.inspection['plan']['scope'] == ['frontend', 'backend']
    assert len(requests) == 3
    assert git(result.workspace, 'status', '--porcelain') == ''
    for artifact in result.diagnostics.rglob('*'):
        if artifact.is_file():
            assert b'secret-gitlab' not in artifact.read_bytes()
            assert b'secret-model' not in artifact.read_bytes()
