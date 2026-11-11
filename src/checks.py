"""Evidence-backed module verification for accepted Execute Runs."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import sys

from .coding import redact
from .runtime import CommandExecutionError, CommandResult, clean_environment


IGNORED = {'.git', 'node_modules', 'target', 'dist', 'build', '.venv', 'venv', '__pycache__'}


def source_files(root):
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in IGNORED
                                and not (Path(current) / name).is_symlink())
        for name in sorted(names):
            path = Path(current) / name
            if not path.is_symlink():
                yield path


def command_kind(argv, evidence=''):
    text = ' '.join(Path(arg).name if ('/' in arg or '\\' in arg) else arg for arg in argv) + ' ' + evidence
    if re.search(r'\b(?:test|tests|pytest)\b|\.spec\.', text, re.I):
        return 'test'
    if re.search(r'\b(?:install|ci)\b', ' '.join(argv[1:]), re.I):
        return 'setup'
    if re.search(r'\b(?:build|compile|package|assemble|verify|validate|validation|check)\b', text, re.I):
        return 'build'
    return None


def discover(context):
    """Small pilot discovery: precedence is applied independently per module/check."""
    root = context.checkout
    files = list(source_files(root))
    manifests = [p for p in files if p.name in {'package.json', 'pom.xml'}]
    all_modules = {p.parent.relative_to(root).as_posix(): p.name for p in manifests}
    modules = {}
    for scope in context.agreement['scope']:
        matches = [name for name, manifest in all_modules.items() if name == scope
            or (manifest == 'package.json' and re.search(r'frontend|angular|client|web', scope, re.I))
            or (manifest == 'pom.xml' and re.search(r'backend|spring|server|api', scope, re.I))]
        if not matches:
            matches = [scope if (root / scope).is_dir() else '.']
        for name in matches:
            modules.setdefault(name, scope)
    checks = []
    for module, scope in modules.items():
        directory = root / module
        selected = {}
        considered = []
        def offer(argv, cwd, evidence, source):
            kind = command_kind(argv, evidence if source == 'Approved Plan' else '')
            if kind and kind not in selected:
                selected[kind] = dict(argv=argv, cwd=cwd, module=module, scope=scope,
                    kind=kind, evidence=evidence, source=source, executed=False)
        for command in context.agreement['commands']:
            if command['module'] == scope:
                offer(command['argv'], command['cwd'], command['evidence'], 'Approved Plan')
        considered.append('Approved Plan: no workable command for any remaining check kind.')
        for guidance in [p for p in files if p.name.lower().startswith(('readme', 'agents', 'contributing', 'claude'))
                         and p.parent in {root, directory}]:
            text = guidance.read_text(encoding='utf-8', errors='replace')
            cwd = guidance.parent.relative_to(root).as_posix()
            for line in text.splitlines():
                line = line.strip().strip('`').removeprefix('$ ')
                if line.startswith('cd '):
                    cwd = line[3:].strip().strip('"\'')
                    continue
                if any(token in line for token in ('&&', '||', ';', '|', '>')):
                    continue
                try:
                    argv = shlex.split(line, posix=True)
                except ValueError:
                    continue
                if argv and (Path(argv[0]).stem.lower() in {'npm', 'pnpm', 'yarn', 'mvn', 'mvnw',
                            'python', 'python3', 'make'} or argv[0] == sys.executable):
                    # Root guidance must identify the module through cd or an explicit path.
                    if cwd == module:
                        offer(argv, cwd, guidance.relative_to(root).as_posix(), 'repository guidance')
        considered.append('Repository guidance: no workable command for any remaining check kind.')
        package = directory / 'package.json'
        if package.is_file():
            try:
                scripts = json.loads(package.read_text(encoding='utf-8')).get('scripts', {})
                manager = 'pnpm' if (directory / 'pnpm-lock.yaml').is_file() else (
                    'yarn' if (directory / 'yarn.lock').is_file() else 'npm')
                for name in ('build', 'test'):
                    if name in scripts:
                        argv = [manager, 'run', name]
                        expected = json.dumps(context.agreement['verification'])
                        if name == 'build' and re.search(r'\bproduction\b', expected, re.I) and 'ng build' in scripts[name] and not re.search(r'--prod|--configuration[= ]+production', scripts[name]):
                            argv += ['--', '--configuration', 'production']
                        offer(argv, module, package.relative_to(root).as_posix(), 'Node package manifest')
            except (ValueError, AttributeError):
                considered.append('Node package manifest is malformed.')
        considered.append('Node package manifest: no workable command for any remaining check kind.')
        pom = directory / 'pom.xml'
        if pom.is_file():
            wrapper = directory / ('mvnw.cmd' if os.name == 'nt' else 'mvnw')
            executable = str(wrapper.resolve()) if wrapper.is_file() else 'mvn'
            offer([executable, '-B', '-DskipTests', 'package'], module,
                  pom.relative_to(root).as_posix(), 'Maven descriptor')
            offer([executable, '-B', 'test'], module,
                  pom.relative_to(root).as_posix(), 'Maven descriptor')
        considered.append('Maven descriptor: no workable command for any remaining check kind.')
        for script in sorted((directory / 'scripts').glob('*')) if (directory / 'scripts').is_dir() else []:
            if script.suffix == '.py' and script.stem in {'build', 'test', 'validate', 'check'}:
                offer([sys.executable, str(script.resolve())], module,
                      script.relative_to(root).as_posix(), 'scripts/Make/configuration')
        makefile = directory / 'Makefile'
        if makefile.is_file():
            for target in re.findall(r'^(build|test|validate|check):', makefile.read_text(), re.M):
                offer(['make', target], module, makefile.relative_to(root).as_posix(), 'scripts/Make/configuration')
        considered.append('Scripts/Make/configuration: no workable command for any remaining check kind.')
        ci = root / '.gitlab-ci.yml'
        considered.append('GitLab CI: inspected as corroboration only; never execute pipeline/deployment jobs.'
                          + f' {len(ci.read_text(encoding="utf-8", errors="replace").splitlines())} lines inspected.'
                          if ci.is_file() else 'GitLab CI: absent.')
        if (directory / 'angular.json').is_file():
            offer(['ng', 'build'], module, 'angular.json identifies an Angular build target; earlier sources absent.',
                  'repository analysis')
        module_files = [p for p in files if p.is_relative_to(directory)]
        case_candidates = [p for p in module_files if re.search(
            r'(?:\.(?:spec|test)\.[cm]?[jt]sx?$|(?:Test|Tests)\.java$|(?:^|[/\\])test_[^/\\]+\.py$|_test\.py$)', str(p))
            and p.stat().st_size > 0]
        cases = [p.relative_to(root).as_posix() for p in case_candidates if re.search(
            r'\b(?:it|test|specify)\s*\(|@(?:Test|ParameterizedTest|RepeatedTest)\b|\bdef\s+test_\w+\s*\(',
            p.read_text(encoding='utf-8', errors='replace'))]
        if not cases:
            selected['test'] = dict(kind='test', module=module, scope=scope, argv=[], cwd=module,
                source='test source inspection', evidence='No executable test declarations found in inspected module sources.',
                reason='Configured runner has no test cases.' if 'test' in selected else 'No test cases or applicable suite found.',
                status='SKIPPED', executed=False)
        elif 'test' not in selected:
            selected['test'] = dict(kind='test', module=module, scope=scope, argv=[], cwd=module,
                source='test source inspection', evidence=cases, status='UNVERIFIED', executed=False,
                reason='Existing test cases have no workable test command.')
        if 'build' not in selected:
            selected['build'] = dict(kind='build', module=module, scope=scope, argv=[], cwd=module,
                source='repository analysis', evidence=considered, status='UNVERIFIED', executed=False,
                reason='No meaningful mandatory build/validation could be established.')
        for kind in ('setup', 'build', 'test'):
            if kind in selected:
                checks.append(dict(selected[kind], test_cases=cases, discovery_notes=considered))
    return dict(checks=checks, modules=list(modules), exclusions=[dict(module=name,
        reason='Outside approved affected modules; no dependency evidence selects it.')
        for name in all_modules if name not in modules])


def verify(context, selection, settings, services, *, phase, identity=None):
    def output_inventory():
        return {p.relative_to(context.checkout).as_posix() for current, dirs, names in
            os.walk(context.checkout, followlinks=False)
            for p in inventory_directory(current, dirs, names)}
    before_files = output_inventory()
    checks = []
    for number, proposed in enumerate(selection['checks']):
        check = dict(proposed, phase=phase, content_identity=identity,
                     actual_argv=proposed['argv'],
                     status='UNVERIFIED', exit_code=None, duration_seconds=0.0)
        cwd = (context.checkout / proposed['cwd']).resolve()
        check['cwd'] = str(cwd)
        if proposed.get('status') in {'SKIPPED', 'UNVERIFIED'}:
            check.update(status=proposed['status'], reason=proposed['reason'], log_file=None)
            checks.append(check)
            continue
        log = context.artifacts / 'checks' / phase / '1' / proposed['module'] / f'{number}-{proposed["kind"]}.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        check['log_file'] = str(log)
        started = services.clock()
        output = ''
        try:
            if not cwd.is_relative_to(context.checkout.resolve()):
                raise ValueError('Verification cwd escapes checkout')
            remaining = context.remaining(services.clock)
            if remaining <= 0:
                raise TimeoutError('Execute Run deadline expired')
            runner = services.command_runner or context.runtime.run_command_result
            result = runner(proposed['argv'], cwd=cwd, timeout=min(settings.check_timeout_seconds, remaining),
                            env=clean_environment())
            if isinstance(result, CommandResult):
                output = result.stdout + '\n' + result.stderr
                check.update(actual_argv=result.argv, duration_seconds=result.duration_seconds)
            else:
                output = result
            check.update(executed=True, status='PASS', exit_code=0, reason='Command executed successfully.')
            if proposed['kind'] == 'test' and re.search(
                    r'\b(?:TOTAL\s*:\s*0|Tests run\s*:\s*0|Ran\s+0\s+tests|0\s+passed|No tests (?:found|executed))\b',
                    output, re.I):
                check.update(status='UNVERIFIED',
                    reason='Runner completed without executing test cases despite discovered declarations.')
        except CommandExecutionError as exc:
            output = exc.result.stdout + '\n' + exc.result.stderr
            check.update(executed=True, status='BLOCKED' if exc.result.exit_code is None else 'FAIL',
                         actual_argv=exc.result.argv, exit_code=exc.result.exit_code,
                         duration_seconds=exc.result.duration_seconds,
                         reason=exc.result.reason or 'Command exited unsuccessfully.')
        except Exception as exc:
            output = str(exc)
            check.update(status='BLOCKED', reason='Required command could not execute: ' + type(exc).__name__)
        check['duration_seconds'] = max(check['duration_seconds'], services.clock() - started, 0)
        log.write_text(redact(output, settings), encoding='utf-8')
        checks.append(check)
    status = ('FAIL' if any(c['status'] == 'FAIL' for c in checks) else
              'BLOCKED' if any(c['status'] == 'BLOCKED' for c in checks) else
              'UNVERIFIED' if not checks or any(c['status'] == 'UNVERIFIED' for c in checks) else 'PASS')
    report = redact(dict(status=status, checks=checks, phase=phase, content_identity=identity,
                         generated_files=sorted(output_inventory() - before_files),
                         exclusions=selection['exclusions'], modules=selection['modules']), settings)
    location = context.artifacts / 'checks' / phase / 'report.json'
    location.parent.mkdir(parents=True, exist_ok=True)
    location.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


def inventory_directory(current, directories, names):
    directories[:] = [name for name in directories if name not in {'.git', 'node_modules'}
                      and not (Path(current) / name).is_symlink()]
    return [Path(current) / name for name in names if not (Path(current) / name).is_symlink()]
