"""Operator journey at Application.invoke: controlled HTTP/model, real Git/children.

The tiny greeting fixture is unrelated to the opt-in TaskFlow Product Change.
No GitLab network, SDK completion, legacy toolchain or pipeline is required.
"""
import copy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from src.agreement import parse_plan_comment
from src.gitlab_client import GitLabClient
from src.runtime import LocalRuntime
from src.workflow import Application, ApplicationServices, OperationInput
from test_explore import settings
from test_implement import Transport
from test_publication import GIT, git


@pytest.fixture(scope='module')
def greeting_seed(tmp_path_factory):
    seed = tmp_path_factory.mktemp('manual-greeting-seed')
    git(seed, 'init', '-b', 'main')
    git(seed, 'config', 'user.name', 'Human')
    git(seed, 'config', 'user.email', 'human@example.test')
    git(seed, 'config', 'core.autocrlf', 'false')
    (seed / 'frontend').mkdir()
    (seed / 'frontend/greeting.txt').write_text('Hello\n')
    (seed / 'frontend/package.json').write_text('{"scripts":{"test":"unused-harness"}}')
    (seed / '.gitattributes').write_text('* text eol=lf\n')
    git(seed, 'add', '.')
    git(seed, 'commit', '-m', 'Human baseline')
    return seed


@pytest.fixture
def journey(tmp_path, greeting_seed):
    """Public command/model/HTTP/clock seams; workspace preparation is real."""
    remote = tmp_path / 'remote.git'
    subprocess.run([str(GIT), 'clone', '--bare', str(greeting_seed), str(remote)],
                   check=True, capture_output=True)
    transport = Transport(remote)
    transport.issue.update(title='Friendly greeting', description='Change Hello to Welcome in frontend/greeting.txt.')
    transport.mr.update(title='Draft: Human greeting change', description='Human context\nCloses #2')
    # No branch/MR exists until the human handoff below.
    transport.branches = {'main'}
    transport.related = []
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    roles, commands = [], []
    config = settings(tmp_path)
    runtime = LocalRuntime()
    build = [sys.executable, '-c', 'from pathlib import Path; assert Path("greeting.txt").read_text().strip(); print("greeting build validation")']

    def model(**context):
        payload = json.loads(context['prompt'])
        role = payload['role']
        roles.append((role, context['context_id'], payload))
        if role == 'planner':
            assert context['tools'].call('read', 'frontend/greeting.txt') == 'Hello\n'
            with pytest.raises(ValueError):
                context['tools'].call('write', 'frontend/greeting.txt')
            return dict(requirement='Use the Welcome greeting', scope=['frontend'],
                checklist=[dict(id='T1', description='Replace the greeting', acceptance_ids=['A1'])],
                acceptance=[dict(id='A1', description='Greeting reads Welcome')],
                verification=[dict(description='Validate greeting build; run any existing greeting tests')],
                commands=[dict(module='frontend', cwd='frontend', argv=build,
                               evidence='Tiny fixture build validation')], unresolved_questions=[])
        if role == 'coder':
            context['tools'].call('write', 'frontend/greeting.txt', content='Welcome\n',
                                  reason='Approved greeting', acceptance_ids=['A1'])
            return dict(summary='Greeting now reads Welcome', blockers=[])
        with pytest.raises(ValueError):
            context['tools'].call('write', 'frontend/greeting.txt')
        return dict(schema='coding-agent.review/1', decision='APPROVE',
            content_identity=payload['product_change']['identity'], plan_comment_id=123,
            acceptance_coverage=[dict(acceptance_id='A1', status='HUMAN_REQUIRED',
                reason='Source greeting is present; human confirms presentation.', locations=['frontend/greeting.txt'])],
            change_coverage=[dict(path=row['path'], acceptance_ids=['A1'], reason='Approved greeting')
                             for row in payload['product_change']['files']],
            findings=[], human_checks=[dict(acceptance_id='A1', description='Confirm greeting in the target UI.')])

    runtime.model_runner = model

    def command(argv, **kwargs):
        commands.append(tuple(argv))
        if argv[0] == 'git':
            return runtime.run_command([str(GIT), *argv[1:]], **kwargs)
        return runtime.run_command_result(argv, **kwargs)

    def app():
        # A new application and runtime are supplied for implement below.
        return Application(settings=config, services=ApplicationServices(
            gitlab=client, role_runtime=runtime, command_runner=command, clock=lambda: 100.0))

    def handoff(*, tests=False, max_repairs=3, coder=None):
        nonlocal config, runtime
        explored = app().invoke(OperationInput('explore', 'team/demo', 2))
        assert explored.succeeded, explored.message
        assert transport.issue['labels'] == ['team', 'agent::needs-review']
        assert len(transport.notes) == 1
        assert git(explored.workspace, 'status', '--porcelain') == ''
        assert git(remote, 'rev-parse', 'main') == explored.inspection['commit']
        agreement = parse_plan_comment(transport.notes[0]['body'], project_id=42, issue_iid=2)
        assert agreement['requirement'] == 'Use the Welcome greeting'
        # Remove the complete explore run under an explicitly resolved temp guard.
        explore_root = explored.workspace.parent.resolve()
        assert explore_root.is_relative_to(tmp_path.resolve()) and explore_root != tmp_path.resolve()
        shutil.rmtree(explore_root)
        assert not explored.workspace.exists() and not explored.diagnostics.exists()
        # Human approval and new branch/empty draft MR are external actions.
        transport.issue['labels'] = ['team', 'agent::approved']
        human = tmp_path / 'human'
        subprocess.run([str(GIT), 'clone', str(remote), str(human)], check=True, capture_output=True)
        git(human, 'checkout', '-b', 'human/task-2')
        if tests:
            # Existing test passes at baseline and fails only for the bad edit.
            (human / 'frontend/test_greeting.py').write_text(
                'from pathlib import Path\ndef test_greeting():\n    assert Path("greeting.txt").read_text().strip() in {"Hello", "Welcome"}, "greeting regression E17"\n')
            (human / 'frontend/scripts').mkdir()
            (human / 'frontend/scripts/test.py').write_text(
                'from runpy import run_path\nrun_path("test_greeting.py")["test_greeting"]()\nprint("1 test passed")\n')
            (human / 'frontend/README.md').write_text('"' + sys.executable + '" -B scripts/test.py\n')
            git(human, 'add', '.')
            git(human, '-c', 'user.name=Human', '-c', 'user.email=human@example.test',
                'commit', '-m', 'Human existing regression check')
        git(human, 'push', 'origin', 'human/task-2')
        transport.branches.add('human/task-2')
        transport.related = [{'iid': 12, 'project_id': 42}]
        initial = git(remote, 'rev-parse', 'human/task-2')
        config = replace(config, max_fix_attempts=max_repairs)
        runtime = LocalRuntime(model_runner=coder or model)
        return explored, initial, app().invoke(OperationInput('implement', 'team/demo', 2, 12))

    return handoff, transport, remote, roles, commands, model


def test_operator_journey_delivers_one_commit_after_gitlab_only_handoff(journey):
    handoff, transport, remote, roles, commands, _ = journey
    metadata = {key: copy.deepcopy(transport.mr[key]) for key in
                ('title', 'description', 'draft', 'source_branch', 'target_branch')}
    explored, initial, result = handoff()
    assert result.succeeded, result.execution
    assert result.workspace != explored.workspace
    assert result.run_context.plan_comment_id == 123
    assert result.run_context.initial_source_commit == initial
    assert [role for role, _, _ in roles] == ['planner', 'coder', 'reviewer']
    assert len({context for _, context, _ in roles}) == 3
    skipped = next(check for check in result.execution['checks'] if check['kind'] == 'test')
    assert skipped['status'] == 'SKIPPED' and skipped['executed'] is False
    assert skipped['reason'] == 'Configured runner has no test cases.'
    assert skipped['source'] == 'test source inspection'
    build = next(check for check in result.execution['checks'] if check['kind'] == 'build')
    assert build['status'] == 'PASS' and build['exit_code'] == 0
    assert Path(build['log_file']).is_file()
    commit = result.execution['commit']['sha']
    assert git(remote, 'rev-parse', 'human/task-2') == commit
    assert git(remote, 'rev-list', '--count', initial + '..' + commit) == '1'
    assert git(remote, 'rev-parse', commit + '^') == initial
    assert git(remote, 'show', commit + ':frontend/greeting.txt') == 'Welcome'
    assert git(remote, 'diff-tree', '--no-commit-id', '--name-only', '-r', commit) == 'frontend/greeting.txt'
    assert transport.issue['labels'] == ['team', 'agent::approved']
    assert transport.mr['labels'] == ['team', 'agent::needs-review']
    assert all(transport.mr[key] == value for key, value in metadata.items())
    assert len(transport.notes) == len(transport.mr_notes) == 1
    transitions = [(path, data) for method, path, data in transport.calls if method == 'PUT']
    assert transitions == [
        ('projects/42/issues/2', {'remove_labels': 'agent::explore', 'add_labels': 'agent::in-progress'}),
        ('projects/42/issues/2', {'remove_labels': 'agent::in-progress', 'add_labels': 'agent::needs-review'}),
        ('projects/42/merge_requests/12', {'remove_labels': 'agent::implement', 'add_labels': 'agent::in-progress'}),
        ('projects/42/merge_requests/12', {'remove_labels': 'agent::in-progress', 'add_labels': 'agent::needs-review'})]
    assert len([cmd for cmd in commands if 'push' in cmd]) == 1
    assert not any('--force' in cmd for cmd in commands)
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    assert result.execution['label_update']['status'] == 'UPDATED'
    assert result.execution['review']['human_checks']
    assert 'SUCCESS' in transport.mr_notes[0]['body']
    assert (result.diagnostics / 'execution-result.json').is_file()


@pytest.mark.parametrize('repair', [True, False], ids=['repair-rechecks-before-delivery', 'terminal-failure-retains-evidence'])
def test_real_relevant_failure_is_never_reported_as_absent_tests(journey, repair):
    handoff, transport, remote, roles, commands, model = journey
    coding_contexts, repair_evidence = [], []

    def controlled(**context):
        payload = json.loads(context['prompt'])
        if payload['role'] != 'coder':
            return model(**context)
        coding_contexts.append(context['context_id'])
        repair_evidence.append(payload.get('repair_evidence'))
        context['tools'].call('write', 'frontend/greeting.txt',
            content='Welcome\n' if len(coding_contexts) > 1 else 'Broken\n',
            reason='Approved greeting', acceptance_ids=['A1'])
        return dict(summary='Greeting implementation attempt', blockers=[])

    _, initial, result = handoff(tests=True, max_repairs=1 if repair else 0, coder=controlled)
    assert result.execution['baseline']['status'] == 'PASS'
    first = result.execution['attempts'][0]['verification']
    failed = next(check for check in first['checks'] if check['kind'] == 'test')
    assert failed['status'] == 'FAIL' and failed['executed'] and failed['exit_code'] != 0
    assert 'greeting regression E17' in Path(failed['log_file']).read_text()
    assert len(transport.mr_notes) == 1
    assert transport.mr['labels'] == ['team', 'agent::needs-review']
    assert transport.issue['labels'] == ['team', 'agent::approved']
    if repair:
        assert result.succeeded, result.execution
        assert len(coding_contexts) == 2 and len(set(coding_contexts)) == 1
        assert 'greeting regression E17' in json.dumps(repair_evidence[1])
        assert len(result.execution['repairs']) == 1
        assert result.execution['product_verification']['phase'] == 'repair-1'
        assert result.execution['product_verification']['content_identity'] == result.execution['review']['content_identity']
        assert all(check['status'] == 'PASS' for check in result.execution['checks'])
        assert git(remote, 'rev-list', '--count', initial + '..human/task-2') == '1'
        assert git(remote, 'show', 'human/task-2:frontend/greeting.txt') == 'Welcome'
    else:
        assert not result.succeeded and result.execution['status'] == 'EXHAUSTED'
        assert len(coding_contexts) == 1 and result.execution['repairs'] == []
        assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'
        assert git(remote, 'rev-parse', 'human/task-2') == initial
        assert (result.workspace / 'frontend/greeting.txt').read_text() == 'Broken\n'
        assert (result.diagnostics / 'coding-attempt-1.json').is_file()
        assert (result.diagnostics / 'execution-result.json').is_file()
        assert 'FAIL' in transport.mr_notes[0]['body'] and 'EXHAUSTED' in transport.mr_notes[0]['body']
        assert not any('push' in cmd for cmd in commands)
