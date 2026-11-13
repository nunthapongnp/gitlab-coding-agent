"""Verification through the approved application and native command boundaries."""
from dataclasses import replace
import json
import os
from pathlib import Path
import sys

import pytest

from src.agreement import PLAN_SCHEMA, render_plan_comment
from src.gitlab_client import GitLabClient
from src.runtime import LocalRuntime, Workspace
from src.workflow import Application, ApplicationServices, OperationInput
from test_explore import plan, settings
from test_implement import Transport


@pytest.fixture
def pilot(tmp_path):
    transport = Transport(tmp_path / 'unused')
    transport.issue['labels'] = ['team', 'agent::approved']
    client = GitLabClient('https://gitlab.test')
    client.session = transport
    files = {'frontend/package.json': '{"scripts":{"build":"ng build","test":"ng test"}}'}
    agreement = {**plan(), 'schema': PLAN_SCHEMA, 'project_id': 42, 'issue_iid': 2,
                 'branch': 'main', 'commit': 'a' * 40, 'scope': ['frontend']}
    calls = []
    real_git = False

    class Runtime(LocalRuntime):
        def prepare_workspace(self, project, settings, *, root, branch, run_id, **kwargs):
            checkout = root / 'checkout'
            checkout.mkdir()
            for name, value in files.items():
                path = checkout / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
            commit = 'a' * 40
            if real_git:
                from test_explore import git
                git(checkout, 'init', '-b', branch)
                git(checkout, '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    'add', '.')
                git(checkout, '-c', 'user.name=Test', '-c', 'user.email=test@example.test',
                    'commit', '-m', 'Tiny verification baseline')
                commit = git(checkout, 'rev-parse', 'HEAD')
            return Workspace(run_id, checkout, root / 'artifacts', branch, commit)

        def run_command(self, argv, **kwargs):
            if argv[0] == 'git' and not real_git:
                if argv[1:] == ['symbolic-ref', '--short', 'HEAD']:
                    return 'human/task-2'
                if argv[1:] == ['rev-parse', 'HEAD']:
                    return 'a' * 40
                return ''
            return super().run_command(argv, **kwargs)

    def coder(**context):
        calls.append(context)
        return {'summary': 'No edit required', 'blockers': []}

    runtime = Runtime(model_runner=coder)
    def invoke(*, runner=None, model=None, git_history=False):
        nonlocal real_git
        real_git = git_history
        transport.notes = [{'id': 123, 'body': render_plan_comment(agreement)}]
        if model is not None:
            runtime.model_runner = model
        app = Application(settings=settings(tmp_path), services=ApplicationServices(
            gitlab=client, role_runtime=runtime, command_runner=runner))
        return app.invoke(OperationInput('implement', 'team/demo', 2, 12))
    return agreement, files, calls, invoke


def command(kind, code, *, module='frontend', cwd=None):
    return dict(argv=[sys.executable, '-c', code], cwd=cwd or module, module=module,
                evidence=f'Approved {kind} validation for {module}')


def test_failing_baseline_keeps_real_evidence_and_stops_before_coding(pilot):
    agreement, files, calls, invoke = pilot
    agreement['commands'] = [command('build', 'import sys; print("existing regression"); sys.exit(7)')]
    result = invoke()
    assert calls == [], 'A failing baseline must never authorize unrelated coder repair'
    baseline = result.execution['baseline']
    assert baseline['status'] == 'FAIL'
    check = next(row for row in baseline['checks'] if row['kind'] == 'build')
    assert check['status'] == 'FAIL' and check['exit_code'] == 7
    assert check['executed'] is True and check['duration_seconds'] >= 0
    assert check['cwd'] == str(result.workspace / 'frontend')
    assert 'existing regression' in Path(check['log_file']).read_text()
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_both_modules_execute_real_existing_tests_and_exclude_generated_outputs(pilot):
    agreement, files, calls, invoke = pilot
    agreement['scope'] = ['frontend', 'backend']
    files.update({'backend/pom.xml': '<project/>',
        'backend/TaskTest.java': 'class TaskTest { @Test void identifier() {} }',
        'frontend/task.spec.ts': 'it("displays IDs", () => expect(2).toBe(2));'})
    agreement['commands'] = [command(kind, code, module=module)
        for module in ('frontend', 'backend') for kind, code in [
            ('build', 'from pathlib import Path; Path("generated.log").write_text("build output"); print("build passed")'),
            ('test', 'print("1 test passed")')]]
    def coder(**context):
        if json.loads(context['prompt'])['role'] == 'reviewer':
            from test_review import approval
            return approval(json.loads(context['prompt']))
        for path in ('frontend/task.ts', 'backend/Task.java'):
            context['tools'].call('write', path, content='task ID 2',
                reason='Display task ID', acceptance_ids=['A1'])
        return {'summary': 'Both modules changed', 'blockers': []}
    result = invoke(model=coder, git_history=True)
    assert result.execution['product_verification']['status'] == 'PASS', result.execution
    assert result.execution['changed_files'] == ['backend/Task.java', 'frontend/task.ts']
    assert len(result.execution['checks']) == 4
    assert all(row['executed'] and row['status'] == 'PASS' for row in result.execution['checks'])
    assert result.execution['baseline']['generated_files'] == ['backend/generated.log', 'frontend/generated.log']
    assert result.execution['review']['status'] == 'PASS'
    assert not result.succeeded


def test_module_guidance_precedes_manifest_when_plan_commands_are_absent(pilot):
    agreement, files, calls, invoke = pilot
    files['frontend/README.md'] = f'Build:\n```\n"{sys.executable}" -c "print(\'guidance build\')"\n```\n'
    result = invoke()
    baseline = result.execution['baseline']
    build = next(row for row in baseline['checks'] if row['kind'] == 'build')
    assert baseline['status'] == 'PASS'
    assert build['source'] == 'repository guidance'
    assert build['evidence'] == 'frontend/README.md'
    assert build['cwd'] == str(result.workspace / 'frontend')
    tests = next(row for row in baseline['checks'] if row['kind'] == 'test')
    assert tests['status'] == 'SKIPPED' and not tests['executed']
    assert 'no test cases' in tests['reason'].lower()
    assert len(calls) == 1


def test_semantic_backend_scope_discovers_split_maven_module_and_native_wrapper(pilot):
    import os
    agreement, files, calls, invoke = pilot
    agreement['scope'] = ['backend']
    files.clear()
    files.update({'services/tasks/pom.xml': '<project/>',
        'services/tasks/src/test/java/TaskTest.java': 'class TaskTest { @Test void identifier() {} }',
        'services/tasks/mvnw.cmd': '@echo off', 'services/tasks/mvnw': '#!/bin/sh'})
    executions = []
    def runner(argv, **kwargs):
        if argv[0] == 'git':
            return 'human/task-2' if 'symbolic-ref' in argv else 'a' * 40 if 'rev-parse' in argv else ''
        executions.append((argv, kwargs))
        return '1 test passed'
    result = invoke(runner=runner)
    baseline = result.execution['baseline']
    assert baseline['status'] == 'PASS'
    build, test = baseline['checks']
    assert build['source'] == test['source'] == 'Maven descriptor'
    assert build['module'] == test['module'] == 'services/tasks'
    assert build['argv'][0].endswith('mvnw.cmd' if os.name == 'nt' else 'mvnw')
    assert '-DskipTests' in build['argv'] and 'test' in test['argv']
    assert len(executions) == 2 and len(calls) == 1


@pytest.mark.parametrize('stage, content, expected_source', [
    ('frontend/scripts/build.py', 'print("build")', 'scripts/Make/configuration'),
    ('frontend/Makefile', 'build:\n\t@echo build\n', 'scripts/Make/configuration'),
    ('frontend/angular.json', '{"projects":{"tasks":{}}}', 'repository analysis'),
])
def test_missing_earlier_evidence_uses_explained_repository_fallback(pilot, stage, content, expected_source):
    agreement, files, calls, invoke = pilot
    files.clear()
    files[stage] = content
    files['.gitlab-ci.yml'] = 'deploy:\n  script: deploy-production\n'
    seen = []
    def runner(argv, **kwargs):
        if argv[0] == 'git':
            return 'human/task-2' if 'symbolic-ref' in argv else 'a' * 40 if 'rev-parse' in argv else ''
        seen.append(argv)
        return 'build passed'
    result = invoke(runner=runner)
    baseline = result.execution['baseline']
    build = next(row for row in baseline['checks'] if row['kind'] == 'build')
    assert baseline['status'] == 'PASS'
    assert build['source'] == expected_source
    assert build['discovery_notes']
    assert len(seen) == 1 and 'deploy-production' not in seen[0]


def test_real_postchange_regression_retains_baseline_and_failed_candidate(pilot, monkeypatch):
    agreement, files, calls, invoke = pilot
    monkeypatch.setenv('GITLAB_TOKEN', 'secret-gitlab')
    monkeypatch.setenv('LLM_API_KEY', 'secret-model')
    agreement['commands'] = [command('build',
        'from pathlib import Path; import os,sys; '
        'print(Path.cwd()); print(sys.argv[1]); '
        'print("compiler warning", file=sys.stderr); '
        'assert "GITLAB_TOKEN" not in os.environ and "LLM_API_KEY" not in os.environ; '
        'raise SystemExit(9 if Path("task.ts").exists() else 0)')]
    agreement['commands'][0]['argv'].append('argument with spaces')
    def coder(**context):
        context['tools'].call('write', 'frontend/task.ts', content='broken task ID',
            reason='Display approved task IDs', acceptance_ids=['A1'])
        return {'summary': 'Task ID changed', 'blockers': []}
    result = invoke(model=coder, git_history=True)
    assert result.execution['baseline']['status'] == 'PASS'
    baseline_build = next(row for row in result.execution['baseline']['checks'] if row['kind'] == 'build')
    assert 'compiler warning' in Path(baseline_build['log_file']).read_text()
    check = next(row for row in result.execution['checks'] if row['kind'] == 'build')
    assert check['phase'] == 'repair-3'
    assert result.execution['coding_attempts'] == 4
    assert result.execution['status'] == 'EXHAUSTED'
    assert check['exit_code'] == 9 and check['status'] == 'FAIL'
    assert check['content_identity'] == result.execution['product_change']['identity']
    assert 'argument with spaces' in Path(check['log_file']).read_text()
    assert result.execution['product_verification']['status'] == 'FAIL'
    assert result.execution['changed_files'] == ['frontend/task.ts']
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_manifest_build_honors_production_expectation_without_plan_commands(pilot):
    agreement, files, calls, invoke = pilot
    agreement['verification'] = [{'description': 'Run the frontend production build'}]
    seen = []
    def runner(argv, **kwargs):
        if argv[0] == 'git':
            return 'human/task-2' if 'symbolic-ref' in argv else 'a' * 40 if 'rev-parse' in argv else ''
        seen.append(argv)
        return 'production build passed'
    result = invoke(runner=runner)
    assert seen == [['npm', 'run', 'build', '--', '--configuration', 'production']]
    assert result.execution['baseline']['status'] == 'PASS'


@pytest.mark.parametrize('validation', [True, False])
def test_scoped_container_change_requires_its_own_validation(pilot, validation):
    agreement, files, calls, invoke = pilot
    reference = 'Update frontend/Dockerfile configuration to use the approved image.'
    agreement['checklist'][0]['description'] = reference
    files['frontend/Dockerfile'] = 'FROM node:old\n'
    code = ('from pathlib import Path; assert Path("Dockerfile").read_text().startswith("FROM node:"); '
            'print("container configuration validation")' if validation else 'print("application build")')
    agreement['commands'] = [command('build', code)]
    def coder(**context):
        context['tools'].call('write', 'frontend/Dockerfile', content='FROM node:approved\n',
            reason='Approved image update', acceptance_ids=['A1'], approval_reference=reference)
        return {'summary': 'Container configuration updated', 'blockers': []}
    result = invoke(model=coder, git_history=True)
    assert result.execution['product_verification']['status'] == ('PASS' if validation else 'UNVERIFIED')
    assert result.execution['changed_files'] == ['frontend/Dockerfile']
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


@pytest.mark.skipif(os.name != 'nt', reason='Native Windows wrapper contract')
def test_native_windows_wrapper_records_interpreter_and_preserves_spaces(pilot):
    agreement, files, calls, invoke = pilot
    files['frontend/build wrapper.cmd'] = '@echo off\necho argument=%~1\necho native-warning 1>&2\n'
    agreement['commands'] = [dict(module='frontend', cwd='frontend',
        argv=['.\\build wrapper.cmd', 'argument with spaces'], evidence='Approved frontend build')]
    result = invoke()
    build = next(row for row in result.execution['baseline']['checks'] if row['kind'] == 'build')
    assert build['status'] == 'PASS' and build['exit_code'] == 0
    assert Path(build['actual_argv'][0]).name.lower() == 'cmd.exe'
    assert 'argument=argument with spaces' in Path(build['log_file']).read_text()
    assert 'native-warning' in Path(build['log_file']).read_text()


def test_zero_executed_cases_is_not_passing_test_coverage(pilot):
    agreement, files, calls, invoke = pilot
    files['frontend/task.spec.ts'] = 'it("shows an ID", () => expect(2).toBe(2));'
    agreement['commands'] = [command('build', 'print("build passed")'),
                             command('test', 'print("TOTAL: 0 SUCCESS")')]
    result = invoke()
    check = next(row for row in result.execution['baseline']['checks'] if row['kind'] == 'test')
    assert check['status'] == 'UNVERIFIED' and check['executed'] is True
    assert 'without executing test cases' in check['reason']
    assert result.execution['baseline']['status'] == 'UNVERIFIED'
    assert calls == []


@pytest.mark.parametrize('filename, declaration', [
    ('TestGreeting.java', 'class TestGreeting { @Test void greeting() {} }'),
    ('GreetingTestCase.java', 'class GreetingTestCase { @Test void greeting() {} }'),
    ('LegacyTestCase.java', 'class LegacyTestCase extends TestCase { public void testGreeting() {} }'),
])
def test_existing_maven_cases_execute_and_real_failure_withholds_publication(pilot, filename, declaration):
    agreement, files, calls, invoke = pilot
    agreement['scope'] = ['backend']
    files.clear()
    files.update({'backend/pom.xml': '<project/>', 'backend/src/test/java/' + filename: declaration})
    agreement['commands'] = [command('build', 'print("build passed")', module='backend'),
        command('test', 'import sys; print("Tests run: 1, Failures: 1"); sys.exit(7)', module='backend')]
    result = invoke()
    test = next(row for row in result.execution['baseline']['checks'] if row['kind'] == 'test')
    assert test['source'] == 'Approved Plan'
    assert test['test_cases'] == ['backend/src/test/java/' + filename]
    assert test['executed'] is True and test['status'] == 'FAIL' and test['exit_code'] == 7
    assert 'Failures: 1' in Path(test['log_file']).read_text()
    assert result.execution['baseline']['status'] == 'FAIL'
    assert calls == []
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'


def test_unrecognized_maven_test_sources_preserve_plan_evidence_and_withhold_publication(pilot):
    agreement, files, calls, invoke = pilot
    agreement['scope'] = ['backend']
    files.clear()
    files.update({'backend/pom.xml': '<project/>',
                  'backend/src/test/java/CustomCases.java': 'class CustomCases { @CustomCase void greeting() {} }'})
    explicit = command('test', 'print("custom runner")', module='backend')
    agreement['commands'] = [command('build', 'print("build passed")', module='backend'), explicit]
    result = invoke()
    test = next(row for row in result.execution['baseline']['checks'] if row['kind'] == 'test')
    assert test['source'] == 'Approved Plan' and test['argv'] == explicit['argv']
    assert test['status'] == 'UNVERIFIED'
    assert 'cannot establish absence' in test['reason']
    assert result.execution['baseline']['status'] == 'UNVERIFIED'
    assert calls == [] and result.execution['push']['status'] == 'NOT_ATTEMPTED'


@pytest.mark.parametrize('scenario, expected', [('missing-build', 'UNVERIFIED'),
    ('missing-executable', 'BLOCKED'), ('browser-failure', 'FAIL'), ('empty-spec', 'PASS')])
def test_missing_validation_environment_and_empty_cases_are_distinct(pilot, scenario, expected):
    agreement, files, calls, invoke = pilot
    files['frontend/task.spec.ts'] = 'export const helper = 1;'
    if scenario == 'missing-build':
        files.clear()
        files['frontend/task.ts'] = 'export const taskId = 2;'
    else:
        agreement['commands'] = [command('build', 'print("build passed")')]
    if scenario in {'browser-failure', 'missing-executable'}:
        files['frontend/task.spec.ts'] = 'it("shows a task ID", () => expect(2).toBe(2));'
        agreement['commands'].append(command('test',
            'import sys; print("Browser unavailable: cannot launch Chrome", file=sys.stderr); sys.exit(3)'))
        if scenario == 'missing-executable':
            agreement['commands'][-1]['argv'] = ['nonexistent-agent-verification-test', 'test']
    result = invoke()
    baseline = result.execution['baseline']
    assert baseline['status'] == expected
    test = next(row for row in baseline['checks'] if row['kind'] == 'test')
    if scenario == 'empty-spec':
        assert test['status'] == 'SKIPPED' and not test['executed']
    else:
        assert calls == []
    if scenario == 'browser-failure':
        assert test['status'] == 'FAIL' and test['exit_code'] == 3
        assert 'Browser unavailable' in Path(test['log_file']).read_text()
    assert result.execution['push']['status'] == 'NOT_ATTEMPTED'
