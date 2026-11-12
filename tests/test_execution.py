"""Accepted Execute Runs through the application with controlled external services."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

import pytest

from src.agreement import PLAN_SCHEMA, render_plan_comment
from src.gitlab_client import GitLabClient
from src.runtime import CommandExecutionError, CommandResult, LocalRuntime, Workspace
from src.workflow import Application, ApplicationServices, OperationInput
from test_explore import plan, settings
from test_implement import Transport
from test_review import approval


@pytest.fixture
def execution_app(tmp_path):
    transport = Transport(tmp_path / 'unused')
    transport.issue['labels'] = ['team', 'agent::approved']
    agreement = {**plan(), 'schema': PLAN_SCHEMA, 'project_id': 42, 'issue_iid': 2,
        'branch': 'main', 'commit': 'a' * 40, 'scope': ['frontend'],
        'commands': [dict(module='frontend', cwd='frontend', argv=['fixture-build'],
                          evidence='Controlled build at command boundary')]}
    transport.notes = [{'id': 123, 'body': render_plan_comment(agreement)}]
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    clock = [100.0]
    roles, commands = [], []

    class Runtime(LocalRuntime):
        def prepare_workspace(self, project, settings, *, root, branch, run_id, **kwargs):
            checkout = root / 'checkout'
            (checkout / 'frontend').mkdir(parents=True)
            return Workspace(run_id, checkout, root / 'artifacts', branch, 'a' * 40)

    def invoke(*, failures=(), reviewer=None, configure=None, runtime_seconds=3600,
               check_runner=None, real_clock=False, baseline_failure=False):
        failures_iter = iter(failures)
        current_failure = [None]
        def model(**context):
            payload = json.loads(context['prompt'])
            roles.append((payload['role'], context, payload))
            if payload['role'] == 'coder':
                current_failure[0] = next(failures_iter, None)
                count = 1 + context['state'].get('attempts', 0)
                context['state']['attempts'] = count
                context['tools'].call('write', 'frontend/task.ts', content=f'export const attempt = {count};',
                    reason='Implement approved display', acceptance_ids=['A1'])
                return dict(summary=f'Implementation attempt {count}', blockers=[])
            response = approval(payload)
            if current_failure[0] == 'review':
                response.update(decision='REQUEST_CHANGES', findings=[dict(
                    explanation='Use actual task ID rather than hardcoding.', acceptance_ids=['A1'],
                    locations=['frontend/task.ts:1'])])
            return reviewer(payload, context) if reviewer else response

        def command(argv, **kwargs):
            commands.append((list(argv), kwargs))
            if argv[0] != 'git':
                if check_runner:
                    return check_runner(runtime, argv, kwargs)
                if baseline_failure:
                    raise CommandExecutionError(CommandResult(argv, '', 'existing baseline regression', 7, .1))
                if current_failure[0] == 'build':
                    raise CommandExecutionError(CommandResult(argv, '', 'compiler actual E42', 2, .1))
                return 'Controlled build PASS'
            args = argv[1:]
            if args == ['rev-parse', 'HEAD']:
                return 'a' * 40
            if args == ['symbolic-ref', '--short', 'HEAD']:
                return 'human/task-2'
            if args == ['show-ref']:
                return 'a' * 40 + ' refs/heads/human/task-2'
            if args == ['ls-files', '--others', '--exclude-standard', '-z']:
                return 'frontend/task.ts\0' if (Path(kwargs['cwd']) / 'frontend/task.ts').exists() else ''
            return b'' if kwargs.get('binary') else ''

        runtime = Runtime(model_runner=model)
        config = replace(settings(tmp_path), execution_max_runtime_seconds=runtime_seconds)
        if configure:
            configure(runtime, clock, command)
        app = Application(settings=config, services=ApplicationServices(
            gitlab=client, role_runtime=runtime, command_runner=command,
            clock=time.monotonic if real_clock else lambda: clock[0]))
        return app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    return invoke, roles, commands, clock


def test_build_failure_repairs_current_run_with_actual_failure_then_fresh_review(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke(failures=['build'])
    assert result.execution['review']['status'] == 'PASS', result.execution
    assert [role for role, _, _ in roles] == ['coder', 'coder', 'reviewer']
    first, repaired, reviewed = roles
    assert first[1]['context_id'] == repaired[1]['context_id']
    assert 'compiler actual E42' in json.dumps(repaired[2]['repair_evidence'])
    assert repaired[1]['state']['attempts'] == 2
    assert result.execution['coding_attempts'] == 2
    assert len(result.execution['repairs']) == 1
    assert result.execution['product_verification']['content_identity'] == result.execution['review']['content_identity']
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_alternating_build_and_review_failures_exhaust_one_shared_budget(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke(failures=['build', 'review', 'build', 'review'])
    assert result.execution['status'] == 'EXHAUSTED', result.execution
    assert [role for role, _, _ in roles] == ['coder', 'coder', 'reviewer', 'coder', 'coder', 'reviewer']
    assert result.execution['coding_attempts'] == 4
    assert len(result.execution['repairs']) == 3
    assert len({context['context_id'] for role, context, _ in roles if role == 'coder'}) == 1
    assert len({context['context_id'] for role, context, _ in roles if role == 'reviewer'}) == 2
    assert len(list(result.diagnostics.glob('coding-attempt-*.json'))) == 4
    assert result.execution['publication_eligible'] is False
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_review_repair_requires_checks_and_new_approval_for_new_content(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke(failures=['review'])
    assert result.execution['review']['status'] == 'PASS', result.execution
    assert [role for role, _, _ in roles] == ['coder', 'reviewer', 'coder', 'reviewer']
    assert 'Use actual task ID' in json.dumps(roles[2][2]['repair_evidence'])
    identities = [payload['product_change']['identity'] for role, _, payload in roles if role == 'reviewer']
    assert identities[0] != identities[1]
    assert result.execution['product_verification']['phase'] == 'repair-1'
    assert result.execution['product_verification']['content_identity'] == identities[1]
    # Synthetic Git evidence can pass orchestration checks but cannot authorize
    # delivery; the real publication boundary rejects its absent Git history.
    assert result.execution['publication_eligible'] is False


def test_deadline_denies_late_coder_mutations_and_reserves_finalization(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        original = runtime.model_runner
        def model(**context):
            clock[0] = 109.1  # Work stops at 109; finalization may run until 110.
            return original(**context)
        runtime.model_runner = model
    result = invoke(configure=configure, runtime_seconds=10)
    assert result.execution['status'] == 'TIMEOUT', result.execution
    assert not (result.workspace / 'frontend/task.ts').exists()
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'
    assert all(kwargs['timeout'] <= 9 for _, kwargs in commands)


def test_initial_success_needs_one_coder_and_one_fresh_review(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke()
    assert result.execution['publication_eligible'] is False, result.execution
    assert [role for role, _, _ in roles] == ['coder', 'reviewer']
    assert result.execution['coding_attempts'] == 1
    assert result.execution['repairs'] == []


def test_malformed_review_has_one_format_correction_without_spending_coding_repairs(execution_app):
    invoke, roles, commands, clock = execution_app
    replies = [None]
    def reviewer(payload, context):
        return replies.pop() if replies else approval(payload)
    result = invoke(reviewer=reviewer)
    assert result.execution['review']['status'] == 'PASS', result.execution
    assert result.execution['review']['format_corrections'] == 1
    assert [role for role, _, _ in roles] == ['coder', 'reviewer', 'reviewer']
    assert result.execution['coding_attempts'] == 1
    assert result.execution['repairs'] == []


def test_baseline_blocker_stops_without_spending_coding_repairs(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke(baseline_failure=True)
    assert result.execution['status'] == 'BLOCKED', result.execution
    assert result.execution['baseline']['checks'][0]['exit_code'] == 7
    assert result.execution['coding_attempts'] == 0
    assert roles == []
    assert result.execution['repairs'] == []


def test_malformed_review_correction_is_bounded_without_coder_repair(execution_app):
    invoke, roles, commands, clock = execution_app
    result = invoke(reviewer=lambda payload, context: {})
    assert result.execution['status'] == 'BLOCKED', result.execution
    assert [role for role, _, _ in roles] == ['coder', 'reviewer', 'reviewer']
    assert result.execution['coding_attempts'] == 1
    assert result.execution['review']['format_corrections'] == 1
    assert result.execution['repairs'] == []


def test_scope_blocker_stops_without_repeated_coding(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        runtime.model_runner = lambda **context: dict(summary='Stopped',
            blockers=['Requirement needs a new Approved Plan and human reset.'])
    result = invoke(configure=configure)
    assert result.execution['status'] == 'BLOCKED', result.execution
    assert result.execution['coding_attempts'] == 1
    assert result.execution['repairs'] == []
    assert 'new Approved Plan' in result.execution['blockers'][0]


def test_preparation_uses_accepted_deadline_and_stops_before_baseline_or_coder(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        prepare = runtime.prepare_workspace
        def expired(*args, **kwargs):
            workspace = prepare(*args, **kwargs)
            clock[0] = 109.1
            return workspace
        runtime.prepare_workspace = expired
    result = invoke(configure=configure, runtime_seconds=10)
    assert result.execution['status'] == 'TIMEOUT', result.execution
    assert commands == roles == []
    assert result.execution['report_delivery']['status'] == 'DELIVERED'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_clone_process_timeout_reports_expired_accepted_budget(execution_app):
    invoke, roles, commands, clock = execution_app
    def configure(runtime, clock, command):
        def expired(*args, **kwargs):
            clock[0] = 109.1
            raise CommandExecutionError(CommandResult(['git', 'clone'], '', '', None, 9,
                                                       'Local command timed out'))
        runtime.prepare_workspace = expired
    result = invoke(configure=configure, runtime_seconds=10)
    assert result.execution['status'] == 'TIMEOUT', result.execution
    assert result.execution['errors'][0]['error_type'] == 'CommandExecutionError'
    assert roles == []


def test_expired_workflow_kills_ready_child_tree_before_reporting_timeout(execution_app):
    invoke, roles, commands, clock = execution_app
    def check_runner(runtime, argv, kwargs):
        child = ('from pathlib import Path; import time\n'
                 'Path("child-ready").write_text("ready")\n'
                 'while True:\n Path("heartbeat").write_text(str(time.monotonic()))\n time.sleep(.05)')
        parent = ('import subprocess,sys,time; '
                  f'subprocess.Popen([sys.executable,"-c",{child!r}]); time.sleep(60)')
        return runtime.run_command_result([sys.executable, '-c', parent], **kwargs)
    result = invoke(check_runner=check_runner, real_clock=True, runtime_seconds=8)
    assert result.execution['status'] == 'TIMEOUT', result.execution
    assert (result.workspace / 'frontend/child-ready').read_text() == 'ready'
    heartbeat = result.workspace / 'frontend/heartbeat'
    stopped = heartbeat.read_bytes()
    time.sleep(.3)
    assert heartbeat.read_bytes() == stopped, 'Child continued editing after workflow returned expiry'
    assert roles == []
    assert result.execution['baseline']['status'] == 'BLOCKED'
    assert result.execution['commit']['status'] == result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_unfinished_sdk_review_cannot_approve_from_an_earlier_submit(execution_app, monkeypatch):
    """Controlled SDK boundary exercises the actual worker completion gate."""
    from types import SimpleNamespace, ModuleType
    from src.planner_worker import run_sdk
    registered = {}
    tool_api = ModuleType('openhands.sdk.tool')
    class Action:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    class Observation:
        @staticmethod
        def from_text(text, **kwargs):
            return SimpleNamespace(text=text)
    class Definition:
        name = 'inspection'
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
    tool_api.Action, tool_api.Observation = Action, Observation
    tool_api.ToolDefinition, tool_api.ToolExecutor = Definition, object
    tool_api.register_tool = lambda name, cls: registered.update({name: cls})
    monkeypatch.setitem(sys.modules, 'openhands.sdk.tool', tool_api)
    class Conversation:
        def __init__(self, *, agent, **kwargs):
            self.state = SimpleNamespace(execution_status='paused', agent=agent)
        def send_message(self, prompt):
            self.payload = json.loads(prompt)
        def run(self):
            definition = registered['inspection'].create()[0]
            definition.executor(Action(operation='submit', observations=approval(self.payload)))
        def close(self):
            pass
    sdk = SimpleNamespace(LLM=lambda **kwargs: SimpleNamespace(**kwargs),
        Agent=lambda **kwargs: SimpleNamespace(**kwargs), Tool=lambda **kwargs: SimpleNamespace(**kwargs),
        Conversation=Conversation)
    def configure(runtime, clock, command):
        original = runtime.model_runner
        runtime.model_runner = lambda **context: run_sdk(context, sdk=sdk) if (
            json.loads(context['prompt'])['role'] == 'reviewer') else original(**context)
    invoke, roles, commands, clock = execution_app
    result = invoke(configure=configure)
    assert result.execution['review']['status'] == 'BLOCKED', result.execution
    assert result.execution['publication_eligible'] is False
