"""Independent review through the agreed whole-Issue application boundary."""
import base64
import copy
import json
from pathlib import Path
import shutil
import subprocess

import pytest

from src.agreement import PLAN_SCHEMA, render_plan_comment
from src.gitlab_client import GitLabClient
from src.runtime import LocalRuntime, Workspace
from src.workflow import Application, ApplicationServices, OperationInput
from test_explore import plan, settings
from test_implement import Transport


@pytest.fixture(scope='module')
def review_seed(tmp_path_factory):
    seed = tmp_path_factory.mktemp('review-seed')
    (seed / 'frontend').mkdir()
    (seed / 'frontend/package.json').write_text('{"scripts":{"build":"fixture-build"}}')
    subprocess.run(['git', 'init', '-b', 'human/task-2', str(seed)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(seed), 'add', '.'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(seed), '-c', 'user.name=Test', '-c',
                    'user.email=test@example.test', 'commit', '-m', 'review baseline'],
                   check=True, capture_output=True)
    head = subprocess.check_output(['git', '-C', str(seed), 'rev-parse', 'HEAD'], text=True).strip()
    return seed, head


def approval(payload):
    return dict(schema='coding-agent.review/1', decision='APPROVE',
        content_identity=payload['product_change']['identity'], plan_comment_id=123,
        acceptance_coverage=[dict(acceptance_id='A1', status='HUMAN_REQUIRED',
            reason='Static display implementation present; browser rendering remains unexecuted.',
            locations=['frontend/task.ts'])],
        change_coverage=[dict(path=row['path'], acceptance_ids=['A1'], reason='Approved task ID display')
                         for row in payload['product_change']['files']],
        findings=[], human_checks=[dict(acceptance_id='A1', description='Confirm task IDs in the browser.')])


@pytest.fixture
def reviewed_application(tmp_path, review_seed):
    seed, head = review_seed
    transport = Transport(tmp_path / 'unused')
    transport.issue['labels'] = ['team', 'agent::approved']
    agreement = {**plan(), 'schema': PLAN_SCHEMA, 'project_id': 42, 'issue_iid': 2,
        'branch': 'main', 'commit': head, 'scope': ['frontend'],
        'commands': [dict(module='frontend', cwd='frontend', argv=['fixture-build'],
                          evidence='Controlled frontend build validation')]}
    transport.notes = [{'id': 123, 'body': render_plan_comment(agreement)}]
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    roles = []
    class Runtime(LocalRuntime):
        def prepare_workspace(self, project, settings, *, root, branch, run_id, **kwargs):
            checkout = root / 'checkout'
            shutil.copytree(seed, checkout)
            return Workspace(run_id, checkout, root / 'artifacts', branch, head)
    runtime = Runtime()
    def commands(argv, **kwargs):
        if argv[0] != 'git':
            return 'Controlled build completed successfully'
        args = argv[1:]
        if args == ['rev-parse', 'HEAD']:
            return head
        if args == ['symbolic-ref', '--short', 'HEAD']:
            return 'human/task-2'
        if args == ['show-ref']:
            return head + ' refs/heads/human/task-2'
        if args == ['ls-files', '--others', '--exclude-standard', '-z']:
            return ''.join(p.relative_to(kwargs['cwd']).as_posix() + '\0'
                for p in Path(kwargs['cwd']).rglob('*') if p.is_file()
                and '.git' not in p.parts and p.name != 'package.json')
        return ''
    def invoke(reviewer=approval, *, build_failure=False, after_review=None, extra_file=False):
        def model(**context):
            payload = json.loads(context['prompt'])
            roles.append((payload['role'], context, payload))
            if payload['role'] == 'coder':
                context['state']['private_coder_reasoning'] = 'Never copy this into review'
                context['tools'].call('write', 'frontend/task.ts', content='export const taskId = "TASK-2";',
                    reason='Display approved task ID', acceptance_ids=['A1'])
                if extra_file:
                    context['tools'].call('write', 'frontend/extra.ts', content='unrelated change',
                        reason='Claimed relevance requires independent review', acceptance_ids=['A1'])
                return dict(summary='Coder claim is not review authority', blockers=[])
            result = reviewer(payload, context) if callable(reviewer) and reviewer is not approval else approval(payload)
            if after_review:
                after_review(context)
            return result
        def runner(argv, **kwargs):
            if build_failure and argv[0] != 'git' and (Path(kwargs['cwd']) / 'task.ts').exists():
                from src.runtime import CommandExecutionError, CommandResult
                raise CommandExecutionError(CommandResult(argv, '', 'candidate build failed', 2, 0.1))
            return commands(argv, **kwargs)
        runtime.model_runner = model
        app = Application(settings=settings(tmp_path), services=ApplicationServices(
            gitlab=client, role_runtime=runtime, command_runner=runner, clock=lambda: 100.0))
        return app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    return invoke, roles


def test_review_receives_full_new_files_exact_agreement_and_real_check_evidence_in_fresh_context(reviewed_application):
    invoke, roles = reviewed_application
    result = invoke()
    assert result.execution['review']['status'] == 'PASS', result.execution
    coder, reviewer = roles
    assert reviewer[0] == 'reviewer' and coder[1]['context_id'] != reviewer[1]['context_id']
    payload, context = reviewer[2], reviewer[1]
    assert payload['plan_comment_id'] == 123
    assert payload['approved_plan'] == result.run_context.agreement
    assert payload['issue']['description'] == 'Show task IDs'
    assert 'private_coder_reasoning' not in json.dumps(payload)
    assert 'Coder claim' not in json.dumps(payload)
    assert 'state' not in context
    row = payload['product_change']['files'][0]
    assert row['before'] is None
    assert base64.b64decode(row['after']['content_base64']) == b'export const taskId = "TASK-2";'
    assert payload['verification']['status'] == 'PASS'
    assert any(check['executed'] and check['exit_code'] == 0 for check in payload['verification']['checks'])
    assert 'Controlled build completed' in json.dumps(payload['verification_logs'])
    assert result.execution['review']['content_identity'] == result.execution['product_change']['identity']
    assert result.execution['review']['human_checks'][0]['acceptance_id'] == 'A1'
    assert json.loads((result.diagnostics / 'review-result.json').read_text())['status'] == 'PASS'
    assert result.execution['push']['status'] == result.execution['commit']['status'] == 'NOT_ATTEMPTED'
    assert not result.succeeded

def test_review_retains_exact_redacted_request_and_response_outside_product_checkout(reviewed_application):
    invoke, roles = reviewed_application
    result = invoke()
    request = json.loads((result.diagnostics / 'review-input.json').read_text())
    response = json.loads((result.diagnostics / 'review-response.json').read_text())
    assert request['plan_comment_id'] == 123
    assert request['product_change']['identity'] == result.execution['review']['content_identity']
    assert response['decision'] == 'APPROVE'
    assert not list(result.workspace.rglob('review-*.json'))

@pytest.mark.parametrize('invalid', ['empty', 'prose', 'missing-criterion', 'wrong-identity',
    'contradictory-finding', 'unfinished'])
def test_ambiguous_or_incomplete_reviewer_output_never_grants_eligibility(reviewed_application, invalid):
    invoke, roles = reviewed_application
    def reviewer(payload, context):
        response = approval(payload)
        if invalid == 'empty':
            return {}
        if invalid == 'prose':
            return 'Everything looks good; approved'
        if invalid == 'missing-criterion':
            response['acceptance_coverage'] = []
        if invalid == 'wrong-identity':
            response['content_identity'] = 'earlier-content'
        if invalid == 'contradictory-finding':
            response['findings'] = [dict(explanation='TASK-2 is hardcoded and fails other IDs.',
                acceptance_ids=['A1'], locations=['frontend/task.ts:1'])]
        if invalid == 'unfinished':
            response.pop('decision')
        return response
    result = invoke(reviewer)
    assert result.execution['review']['status'] == 'BLOCKED', result.execution
    assert result.execution['status'] == 'BLOCKED'
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_findings_reference_unrelated_change_and_return_actionable_repair_input(reviewed_application):
    invoke, roles = reviewed_application
    def reviewer(payload, context):
        response = approval(payload)
        response['decision'] = 'REQUEST_CHANGES'
        response['change_coverage'][0]['acceptance_ids'] = []
        response['findings'] = [dict(explanation='Remove unrelated extra file; no approved criterion needs it.',
            acceptance_ids=[], locations=['frontend/extra.ts:1'])]
        return response
    result = invoke(reviewer, extra_file=True)
    assert result.execution['review']['status'] == 'FAIL'
    assert result.execution['review']['findings'][0]['locations'] == ['frontend/extra.ts:1']
    assert 'Remove unrelated' in result.execution['review']['findings'][0]['explanation']
    assert (result.workspace / 'frontend/extra.ts').read_text() == 'unrelated change'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_failed_required_validation_cannot_be_overridden_by_reviewer_prose(reviewed_application):
    invoke, roles = reviewed_application
    result = invoke(build_failure=True)
    assert [role[0] for role in roles] == ['coder'] * 4
    assert result.execution['status'] == 'EXHAUSTED'
    assert result.execution['product_verification']['status'] == 'FAIL'
    assert result.execution['review']['status'] == 'NOT_RUN'
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_reviewer_only_exposes_read_capabilities_and_cannot_edit_or_publish(reviewed_application):
    invoke, roles = reviewed_application
    def reviewer(payload, context):
        tools = context['tools']
        assert tools.names == ('list', 'read', 'search')
        assert 'taskId' in tools.call('read', 'frontend/task.ts')
        for operation in ['write', 'delete', 'command', 'commit', 'push', 'gitlab_write', 'approve_mr']:
            with pytest.raises(ValueError):
                tools.call(operation, 'frontend/task.ts')
        with pytest.raises(ValueError):
            tools.call('read', '.git/index')
        with pytest.raises(ValueError):
            tools.call('read', '../artifacts/run-context.json')
        return approval(payload)
    result = invoke(reviewer)
    assert result.execution['review']['status'] == 'PASS'
    assert result.execution['review']['before_workspace_identity'] == result.execution['review']['after_workspace_identity']


@pytest.mark.parametrize('path', ['frontend/task.ts', '.git/index', '.git/refs/heads/human/task-2'])
def test_out_of_band_review_mutations_to_source_index_or_history_reject_approval(reviewed_application, path):
    invoke, roles = reviewed_application
    def mutation(context):
        (context['workspace'] / path).write_text('Unexpected mutation')
    result = invoke(after_review=mutation)
    assert result.execution['review']['status'] == 'BLOCKED'
    assert 'mutated source, index or history' in result.execution['review']['reason']
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'
