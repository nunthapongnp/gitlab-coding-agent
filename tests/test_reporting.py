import copy
import json
from urllib.parse import urlsplit

import pytest
import requests

from src.workflow import OperationInput, OutcomeStatus
from test_explore import source
from test_implement import approved


@pytest.fixture
def handoff(approved):
    app, transport, source_repo = approved
    original = transport.request
    transport.report_attempts = 0
    transport.report_behavior = 'deliver'
    transport.label_failure = False
    transport.final_timeouts = []

    def request(method, url, **kwargs):
        path = urlsplit(url).path
        if method == 'POST' and path.endswith('/merge_requests/12/notes'):
            transport.report_attempts += 1
            transport.final_timeouts.append(kwargs['timeout'])
            note = {'id': 456, 'body': kwargs['json']['body']}
            if transport.report_behavior in {'deliver', 'delivered-timeout'}:
                transport.mr_notes.append(note)
            if transport.report_behavior != 'deliver':
                raise requests.Timeout('secret-gitlab secret-model')
            response = requests.Response()
            response.status_code = 201
            response._content = json.dumps(note).encode()
            return response
        if method == 'PUT' and kwargs['json']['add_labels'] == 'agent::needs-review':
            transport.final_timeouts.append(kwargs['timeout'])
            if transport.label_failure:
                raise requests.ConnectionError('secret-gitlab secret-model')
        return original(method, url, **kwargs)

    transport.request = request
    return app, transport, source_repo


def test_stopped_accepted_run_hands_off_one_truthful_report_and_local_result(handoff):
    app, transport, source_repo = handoff
    mr_before, issue_before = copy.deepcopy(transport.mr), copy.deepcopy(transport.issue)
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert transport.report_attempts == 1
    assert transport.mr == {**mr_before, 'labels': ['team', 'agent::needs-review']}
    assert transport.issue == issue_before
    body = transport.mr_notes[0]['body']
    assert f'<!-- coding-agent:execution-report run={result.run_context.run_id} -->' in body
    for expected in ['Execution Report', 'BLOCKED', 'Issue #2', 'MR !12', 'Plan comment 123',
                     'human/task-2', 'main', result.run_context.initial_source_commit,
                     'Task IDs visible', 'UNVERIFIED', 'NOT_ATTEMPTED', 'Next human action']:
        assert expected in body
    evidence = json.loads((result.diagnostics / 'execution-result.json').read_text())
    assert evidence == result.execution
    assert evidence['report_delivery'] == {'status': 'DELIVERED', 'comment_id': 456}
    assert evidence['label_update']['status'] == 'UPDATED'
    assert evidence['commit']['status'] == evidence['push']['status'] == 'NOT_ATTEMPTED'
    assert (result.diagnostics / 'execution-report.md').read_text(encoding='utf-8') == body
    assert result.diagnostics.parent == result.workspace.parent
    assert not result.diagnostics.is_relative_to(result.workspace)


def test_expired_accepted_run_retains_handoff_without_claiming_submission(handoff):
    from dataclasses import replace

    app, transport, source_repo = handoff
    now = [100.0]
    runtime = app.services.role_runtime

    def command(argv, **kwargs):
        if 'clone' in argv:
            now[0] = 3700.0
            raise TimeoutError('deadline expired during clone')
        return runtime.run_command(argv, **kwargs)

    app.services = replace(app.services, command_runner=command, clock=lambda: now[0])
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert transport.report_attempts == 0
    assert result.execution['status'] == 'TIMEOUT'
    assert result.execution['report_delivery']['status'] == 'NOT_ATTEMPTED'
    assert result.execution['label_update']['status'] == 'NOT_ATTEMPTED'
    assert result.execution['duration_seconds'] == 3600.0
    assert (result.diagnostics / 'execution-result.json').is_file()


@pytest.mark.parametrize('delivery,label_failure', [
    ('delivered-timeout', False), ('lost', False), ('deliver', True), ('lost-readback', True)])
def test_report_and_label_failures_are_independent_and_never_duplicate(handoff, delivery, label_failure):
    app, transport, source_repo = handoff
    transport.report_behavior = delivery
    transport.label_failure = label_failure
    original = transport.request
    posted = [False]

    def request(method, url, **kwargs):
        if method == 'POST' and url.endswith('/merge_requests/12/notes'):
            posted[0] = True
        if posted[0] and method == 'GET' and url.endswith('/merge_requests/12/notes'):
            if delivery == 'lost-readback':
                raise requests.ConnectionError('secret-gitlab secret-model')
            # Another marker must not confirm delivery for the current Run.
            if kwargs['params']['page'] == 1:
                transport.mr_notes.insert(0, {'id': 900, 'body': '<!-- coding-agent:execution-report run=another-run -->'})
        return original(method, url, **kwargs)

    transport.request = request
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    evidence = result.execution
    assert transport.report_attempts == 1
    assert evidence['report_delivery']['status'] == (
        'DELIVERED' if delivery in {'deliver', 'delivered-timeout'} else 'UNCERTAIN')
    assert evidence['label_update']['status'] == ('FAILED' if label_failure else 'UPDATED')
    assert evidence['commit']['status'] == evidence['push']['status'] == 'NOT_ATTEMPTED'
    assert evidence['product_verification']['status'] == 'UNVERIFIED'
    assert evidence['status'] == 'BLOCKED'
    assert transport.mr['labels'] == ['team', 'agent::in-progress' if label_failure else 'agent::needs-review']
    assert json.loads((result.diagnostics / 'execution-result.json').read_text(encoding='utf-8')) == evidence
    for path in result.diagnostics.rglob('*'):
        if path.is_file():
            content = path.read_bytes()
            assert b'secret-gitlab' not in content
            assert b'secret-model' not in content
    assert all(0 < timeout <= 30 for timeout in transport.final_timeouts)


def test_timeout_readback_and_each_label_request_use_remaining_budget(handoff):
    from dataclasses import replace

    app, transport, source_repo = handoff
    now = [100.0]
    runtime = app.services.role_runtime
    original = transport.request
    observed = []
    transport.report_behavior = 'delivered-timeout'

    def command(argv, **kwargs):
        output = runtime.run_command(argv, **kwargs)
        if 'symbolic-ref' in argv:
            now[0] = 3692.0
        return output

    def request(method, url, **kwargs):
        if now[0] > 100:
            observed.append((method, kwargs['timeout']))
        try:
            return original(method, url, **kwargs)
        finally:
            if method == 'POST':
                now[0] = 3695.0

    app.services = replace(app.services, command_runner=command, clock=lambda: now[0])
    transport.request = request
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    assert result.execution['label_update']['status'] == 'UPDATED'
    assert observed == [('POST', 8.0), ('GET', 5.0), ('GET', 5.0), ('GET', 5.0), ('PUT', 5.0)]
    assert result.execution['duration_seconds'] == 3595.0


def test_reported_mr_rejects_a_new_run_even_when_human_relabels_it(handoff):
    app, transport, source_repo = handoff
    first = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert first.execution['report_delivery']['status'] == 'DELIVERED'
    transport.mr['labels'] = ['team', 'agent::implement']
    before = set(app.settings.output_dir.iterdir())
    transport.calls.clear()
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert result.run_context is None
    assert 'already contains' in result.message
    assert transport.report_attempts == 1
    assert set(app.settings.output_dir.iterdir()) == before
    assert all(method == 'GET' for method, path, data in transport.calls)


def test_preparation_failure_retains_actionable_redacted_diagnostics_and_logs(handoff):
    from dataclasses import replace
    from pathlib import Path

    app, transport, source_repo = handoff
    app.settings = replace(app.settings, llm_api_key='secret-"model\\line')

    def command(argv, **kwargs):
        if 'clone' in argv:
            log = Path(kwargs['cwd']) / 'artifacts' / 'preparation.log'
            log.write_text(json.dumps({'key': app.settings.llm_api_key}), encoding='utf-8')
            log.with_suffix('.raw').write_bytes(b'\xff' + app.settings.gitlab_token.encode())
            raise RuntimeError('Clone toolchain unavailable: secret-gitlab ' + app.settings.llm_api_key)
        pytest.fail('Preparation failure must stop commands')

    app.services = replace(app.services, command_runner=command)
    result = app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    assert result.status == OutcomeStatus.FAILED
    assert result.execution['errors'] == [{'phase': 'preparation', 'error_type': 'RuntimeError',
        'message': 'Clone toolchain unavailable: [REDACTED] [REDACTED]'}]
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    assert result.execution['label_update']['status'] == 'UPDATED'
    assert 'Clone toolchain unavailable' in transport.mr_notes[0]['body']
    for path in result.diagnostics.rglob('*'):
        if path.is_file():
            assert b'secret-' not in path.read_bytes()
    assert '[REDACTED]' in (result.diagnostics / 'preparation.log').read_text(encoding='utf-8')
