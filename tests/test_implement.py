import copy
import json
import shutil
import sys
from urllib.parse import unquote, urlsplit

import pytest
import requests

from src.gitlab_client import GitLabClient
from src.runtime import LocalRuntime
from src.workflow import Application, ApplicationServices, OperationInput, OutcomeStatus
from test_explore import git, plan, settings, source


class Transport:
    def __init__(self, source):
        self.project = {'id': 42, 'path_with_namespace': 'team/demo', 'default_branch': 'main',
                        'http_url_to_repo': str(source)}
        self.issue = {'iid': 2, 'project_id': 42, 'title': 'Task IDs', 'description': 'Show task IDs',
                      'labels': ['team', 'agent::explore']}
        self.notes = []
        self.mr = {'iid': 12, 'project_id': 42, 'source_project_id': 42, 'target_project_id': 42,
                   'state': 'opened', 'source_branch': 'human/task-2', 'target_branch': 'main',
                   'title': 'Draft: Task IDs', 'draft': True, 'description': 'Human notes',
                   'labels': ['team', 'agent::implement']}
        self.related = [{'iid': 12, 'project_id': 42}]
        self.mr_notes = []
        self.branches = {'main', 'human/task-2'}
        self.calls = []

    def request(self, method, url, **kwargs):
        path = unquote(urlsplit(url).path.removeprefix('/api/v4/'))
        self.calls.append((method, path, copy.deepcopy(kwargs.get('json'))))
        payload, status = None, 200
        if path == 'projects/team/demo' or path == 'projects/42':
            payload = self.project
        elif path == 'projects/42/issues/2':
            payload = self.issue
            if method == 'PUT':
                data = kwargs['json']
                self.issue['labels'].remove(data['remove_labels'])
                self.issue['labels'].append(data['add_labels'])
        elif path == 'projects/42/issues/2/notes':
            if method == 'POST':
                payload = {'id': 123, 'body': kwargs['json']['body']}
                self.notes.append(payload)
            else:
                payload = self.notes if kwargs['params']['page'] == 1 else []
        elif path == 'projects/42/issues/2/related_merge_requests':
            payload = self.related if kwargs['params']['page'] == 1 else []
        elif path == 'projects/42/merge_requests/12':
            payload = self.mr
            if method == 'PUT':
                data = kwargs['json']
                self.mr['labels'].remove(data['remove_labels'])
                self.mr['labels'].append(data['add_labels'])
        elif path == 'projects/42/merge_requests/12/notes':
            if method == 'POST':
                payload = {'id': 456, 'body': kwargs['json']['body']}
                self.mr_notes.append(payload)
            else:
                payload = self.mr_notes if kwargs['params']['page'] == 1 else []
        elif path.startswith('projects/42/repository/branches/'):
            branch = path.removeprefix('projects/42/repository/branches/')
            payload = {'name': branch} if branch in self.branches else {'message': '404 Branch Not Found'}
            status = 200 if branch in self.branches else 404
        else:
            pytest.fail(f'Unexpected GitLab request: {method} {path}')
        response = requests.Response()
        response.status_code = status
        response._content = json.dumps(payload).encode()
        return response


@pytest.fixture
def approved(source, tmp_path):
    transport = Transport(source)
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    runtime = LocalRuntime(model_runner=lambda **context: plan())
    app = Application(settings=settings(tmp_path), services=ApplicationServices(
        gitlab=client, role_runtime=runtime, clock=lambda: 100.0))
    explored = app.invoke(OperationInput('explore', 'team/demo', 2))
    assert explored.succeeded, explored.message
    shutil.rmtree(explored.workspace.parent)
    transport.issue['labels'] = ['team', 'agent::approved']
    # These tiny coding/preflight fixtures have no Angular/Java toolchain. The
    # agreed command seam validates their local deliverables with real children.
    from src.agreement import parse_plan_comment, render_plan_comment
    agreement = parse_plan_comment(transport.notes[0]['body'])
    agreement['commands'] = [dict(module=module, cwd=module,
        argv=[sys.executable, '-c', f'from pathlib import Path; assert Path("{manifest}").is_file(); print("fixture build validation")'],
        evidence='Controlled fixture module validation at the approved command boundary')
        for module, manifest in [('frontend', 'package.json'), ('backend', 'pom.xml')]]
    transport.notes[0]['body'] = render_plan_comment(agreement)
    transport.calls.clear()
    git(source, 'checkout', '-b', 'human/task-2')
    (source / 'human.txt').write_text('Existing human work')
    git(source, 'add', 'human.txt')
    git(source, 'commit', '-m', 'Human preparation')
    return app, transport, source


def test_implement_uses_gitlab_plan_and_exact_fresh_source_without_explore_state(approved):
    app, transport, source = approved
    mr_before = copy.deepcopy(transport.mr)
    history = git(source, 'log', '--format=%H')
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.UNAVAILABLE, result.message
    context = result.run_context
    assert context is not None
    assert context.plan_comment_id == 123
    assert context.agreement['requirement'] == 'Show task IDs'
    assert context.source_branch == 'human/task-2'
    assert context.target_branch == 'main'
    assert context.initial_source_commit == git(source, 'rev-parse', 'HEAD')
    assert context.accepted_at == 100.0
    assert context.deadline == 3700.0
    assert context.workspace.run_id == context.run_id
    assert context.workspace.checkout == result.workspace
    assert git(result.workspace, 'branch', '--show-current') == 'human/task-2'
    assert git(result.workspace, 'log', '--format=%H') == history
    assert git(result.workspace, 'status', '--porcelain') == ''
    assert (result.workspace / 'human.txt').read_text() == 'Existing human work'
    assert transport.issue['labels'] == ['team', 'agent::approved']
    assert transport.mr == {**mr_before, 'labels': ['team', 'agent::needs-review']}
    assert [(method, path, data) for method, path, data in transport.calls if method == 'PUT'] == [
        ('PUT', 'projects/42/merge_requests/12',
         {'remove_labels': 'agent::implement', 'add_labels': 'agent::in-progress'}),
        ('PUT', 'projects/42/merge_requests/12',
         {'remove_labels': 'agent::in-progress', 'add_labels': 'agent::needs-review'})]
    assert len(transport.mr_notes) == 1

def test_accepted_run_refuses_a_checkout_of_the_wrong_branch(approved):
    from dataclasses import replace
    app, transport, source = approved
    runtime = LocalRuntime(model_runner=lambda **context: pytest.fail('No coding role may run'))

    def command(argv, **kwargs):
        if 'clone' in argv:
            argv = list(argv)
            argv[argv.index('--branch') + 1] = 'main'
        return runtime.run_command(argv, **kwargs)

    app.services = replace(app.services, role_runtime=runtime, command_runner=command)
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert result.run_context is not None
    assert result.diagnostics.exists()
    assert transport.mr['labels'] == ['team', 'agent::needs-review']

INVALID_REQUESTS = [
    'no-approval', 'conflicting-issue-labels', 'no-plan', 'duplicate-plan', 'malformed-plan',
    'plain-text-plan', 'wrong-project-plan', 'wrong-issue-plan', 'closed-mr', 'forked-mr',
    'different-target-project', 'wrong-mr-project', 'wrong-mr-iid', 'unlinked-mr',
    'missing-source', 'missing-target', 'wrong-target', 'same-branches',
    'conflicting-mr-labels', 'stale-in-progress', 'prior-report', 'cross-project-reference',
]

def test_rejected_preflight_has_no_lifecycle_writes_or_local_work(approved):
    from dataclasses import replace
    from src.agreement import parse_plan_comment, render_plan_comment
    app, transport, source = approved
    initial = copy.deepcopy(transport.__dict__)
    for invalid in INVALID_REQUESTS:
        transport.__dict__.update(copy.deepcopy(initial))
        if invalid == 'no-approval':
            transport.issue['labels'] = ['team']
        elif invalid == 'conflicting-issue-labels':
            transport.issue['labels'].append('agent::needs-review')
        elif invalid == 'no-plan':
            transport.notes.clear()
        elif invalid == 'duplicate-plan':
            transport.notes.append(dict(transport.notes[0], id=124))
        elif invalid == 'malformed-plan':
            transport.notes[0]['body'] = '<!-- coding-agent:execution-plan -->\n{}'
        elif invalid == 'plain-text-plan':
            transport.notes[0]['body'] = '# Execution Plan\nShow task IDs'
        elif invalid in {'wrong-project-plan', 'wrong-issue-plan'}:
            agreement = parse_plan_comment(transport.notes[0]['body'])
            agreement['project_id' if invalid == 'wrong-project-plan' else 'issue_iid'] = 999
            transport.notes[0]['body'] = render_plan_comment(agreement)
        elif invalid == 'closed-mr':
            transport.mr['state'] = 'closed'
        elif invalid == 'forked-mr':
            transport.mr['source_project_id'] = 999
        elif invalid == 'different-target-project':
            transport.mr['target_project_id'] = 999
        elif invalid == 'wrong-mr-project':
            transport.mr['project_id'] = 999
        elif invalid == 'wrong-mr-iid':
            transport.mr['iid'] = 999
        elif invalid == 'unlinked-mr':
            transport.related.clear()
        elif invalid == 'missing-source':
            transport.branches.remove('human/task-2')
        elif invalid == 'missing-target':
            transport.branches.remove('main')
        elif invalid == 'wrong-target':
            transport.mr['target_branch'] = 'another-main'
        elif invalid == 'same-branches':
            transport.mr['source_branch'] = 'main'
        elif invalid == 'conflicting-mr-labels':
            transport.mr['labels'].append('agent::approved')
        elif invalid == 'stale-in-progress':
            transport.mr['labels'] = ['agent::in-progress']
        elif invalid == 'prior-report':
            transport.mr_notes = [{'id': 55, 'body': '<!-- coding-agent:execution-report run=old -->\nFailed'}]
        elif invalid == 'cross-project-reference':
            transport.related.clear()
            transport.mr['description'] = 'Closes other/project#2'

        def forbidden(*args, **kwargs):
            pytest.fail('Rejected preflight started a command or clock')
        app.services = replace(app.services, command_runner=forbidden, clock=forbidden)
        before_issue, before_mr = copy.deepcopy(transport.issue), copy.deepcopy(transport.mr)
        before_dirs = set(app.settings.output_dir.iterdir())
        result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
        assert result.status == OutcomeStatus.FAILED
        assert result.run_context is None
        assert all(method == 'GET' for method, path, data in transport.calls)
        assert set(app.settings.output_dir.iterdir()) == before_dirs
        assert transport.issue == before_issue and transport.mr == before_mr


def test_empty_draft_source_and_explicit_same_project_closing_reference_are_valid(approved):
    app, transport, source = approved
    # A second eligible MR does not affect explicit selection; no MR-list endpoint exists.
    git(source, 'checkout', 'main')
    git(source, 'branch', 'empty-source')
    transport.mr['source_branch'] = 'empty-source'
    transport.branches.add('empty-source')
    transport.related = [{'iid': 99, 'project_id': 42}]
    transport.mr['description'] = 'Human notes\n\nCloses team/demo#2'
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.UNAVAILABLE
    assert result.run_context.mr['iid'] == 12
    assert git(result.workspace, 'rev-parse', 'HEAD') == git(source, 'rev-parse', 'main')
    assert git(result.workspace, 'diff', 'origin/main', '--') == ''
    assert transport.mr['draft'] is True

def test_cli_rejected_approval_stops_without_writes(approved):
    from io import StringIO
    from main import main
    app, transport, source = approved
    transport.issue['labels'] = ['team']
    output = StringIO()
    assert main(['implement', '--project', 'team/demo', '--issue', '2', '--mr', '12'],
                environ={'GITLAB_TOKEN': 'secret-gitlab', 'LLM_API_KEY': 'secret-model',
                         'LLM_BASE_URL': 'https://proxy.example/v1', 'LLM_MODEL': 'team-model'},
                services=app.services, stderr=output) == 1
    assert 'approved' in output.getvalue()
    assert all(method == 'GET' for method, path, data in transport.calls)

@pytest.mark.parametrize('failure', ['progress', 'clone'])
def test_accepted_preparation_failure_keeps_context_and_diagnostics(approved, failure):
    from dataclasses import replace
    app, transport, source = approved
    runtime = LocalRuntime()
    if failure == 'progress':
        # A transport failure at the accepted lifecycle write must not become rejection.
        original = transport.request
        def request(method, url, **kwargs):
            if method == 'PUT':
                raise requests.ConnectionError('unavailable')
            return original(method, url, **kwargs)
        transport.request = request
    else:
        def command(argv, **kwargs):
            if 'clone' in argv:
                raise RuntimeError('clone unavailable')
            return runtime.run_command(argv, **kwargs)
        app.services = replace(app.services, command_runner=command)
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert result.run_context is not None
    metadata = json.loads((result.diagnostics / 'run-context.json').read_text())
    assert metadata['run_id'] == result.run_context.run_id
    assert metadata['plan_comment_id'] == 123
    assert metadata['mr_iid'] == 12
    assert not (result.workspace / '.git').exists()
    assert 'secret-gitlab' not in json.dumps(metadata)
    assert 'secret-model' not in json.dumps(metadata)
