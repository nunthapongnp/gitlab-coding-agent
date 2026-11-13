"""Bounded whole-Issue coding, verification and independent review evidence."""
from pathlib import Path
import json
from .checks import discover, verify
from .coding import code_issue
from .review import review_issue, assert_review_current


def execute_issue(context, settings, services):
    """One accepted-run deadline and one repair budget for every failure category."""
    candidate = dict(status='BLOCKED', blockers=[], checks=[], coding_attempts=0, repairs=[], attempts=[])
    try:
        return _execute(context, settings, services, candidate)
    except Exception as exc:
        expired = services.clock() >= context.work_deadline or isinstance(exc, TimeoutError)
        if expired:
            context.runtime.cancel()
        candidate.update(status='TIMEOUT' if expired else 'BLOCKED',
            summary='Execute Run deadline expired.' if expired else 'Execution stopped; inspect retained diagnostics.')
        candidate.setdefault('blockers', []).append(candidate['summary'])
        candidate.setdefault('errors', []).append(dict(phase='execution', error_type=type(exc).__name__))
        return candidate


def _execute(context, settings, services, state):
    context.remaining(services.clock)
    selection = discover(context)
    baseline = verify(context, selection, settings, services, phase='baseline',
                      identity=context.initial_source_commit)
    state.update(baseline=baseline, checks=baseline['checks'], product_verification=baseline)
    context.remaining(services.clock)
    if baseline['status'] != 'PASS':
        return dict(status='BLOCKED', summary='Baseline verification stopped this run before coding.',
                    blockers=['Existing baseline failed or required verification is unavailable.'],
                    baseline=baseline, checks=baseline['checks'],
                    product_verification={'status': baseline['status']}, coding_attempts=0, repairs=[], attempts=[])
    evidence = None
    excluded = set(baseline['generated_files'])
    for attempt in range(1, settings.max_fix_attempts + 2):
        context.remaining(services.clock)
        if attempt > 1:
            state['repairs'].append(dict(repair=attempt - 1, after_coding_attempt=attempt - 1, evidence=evidence))
        state['publication_eligible'] = False
        state['checks'] = []
        state.pop('acceptance_coverage', None)
        state.pop('review', None)
        state.pop('verification', None)
        state.pop('product_verification', None)
        candidate = code_issue(context, settings, services, excluded_paths=excluded,
                               repair_evidence=evidence, attempt=attempt)
        state.update(candidate, baseline=baseline, coding_attempts=attempt)
        context.remaining(services.clock)
        if candidate['status'] == 'BLOCKED':
            return state
        # Earlier gates authorize no later edit, even within the same conversation.
        candidate = _verify_candidate(context, state, selection, settings, services,
                                      phase='post-change' if attempt == 1 else f'repair-{attempt - 1}')
        context.remaining(services.clock)
        excluded.update(candidate.get('generated_files', []))
        candidate['generated_files'] = sorted(excluded)
        state['attempts'].append(dict(coding_attempt=attempt,
            content_identity=candidate['product_change']['identity'],
            verification=candidate.get('product_verification'), review=candidate.get('review')))
        if candidate['status'] == 'BLOCKED':
            return candidate
        if candidate['product_verification']['status'] == 'PASS' and candidate.get('review', {}).get('status') == 'PASS':
            assert_review_current(context, candidate, services)
            candidate['publication_eligible'] = True
            return candidate
        if candidate['status'] in {'BLOCKED', 'UNVERIFIED'}:
            return candidate
        if attempt > settings.max_fix_attempts:
            candidate.update(status='EXHAUSTED', publication_eligible=False,
                summary='Shared coding repair budget exhausted; no Product Change published.')
            return candidate
        evidence = dict(verification=candidate['product_verification'], review=candidate.get('review'), logs=[])
        for check in candidate.get('checks', []):
            if check.get('log_file'):
                evidence['logs'].append(dict(log_file=check['log_file'],
                    output=Path(check['log_file']).read_text(encoding='utf-8')))
    return state


def _verify_candidate(context, candidate, selection, settings, services, *, phase):
    changed = candidate['changed_files']
    affected = [module for module in selection['modules'] if any(
        module == '.' or path == module or path.startswith(module + '/') for path in changed)]
    if any(not any(module == '.' or path == module or path.startswith(module + '/')
                   for module in affected) for path in changed):
        candidate.update(status='BLOCKED', blockers=['Changed deliverable has no established module baseline.'])
        return candidate
    post_selection = discover(context)
    post_selection['checks'] = [check for check in post_selection['checks'] if check['module'] in affected]
    for path in changed:
        if Path(path).name.lower().startswith('dockerfile'):
            validation = [check for check in post_selection['checks'] if check['kind'] == 'build'
                and any(term in json.dumps([check['argv'], check['evidence']]).lower()
                        for term in (path.lower(), 'dockerfile', 'container configuration', 'container validation'))]
            if not validation:
                post_selection['checks'].append(dict(kind='build', module=affected[0], scope=affected[0],
                    argv=[], cwd=affected[0], source='changed deliverable inspection', evidence=path,
                    status='UNVERIFIED', executed=False,
                    reason='Container configuration requires relevant validation; application compilation is insufficient.'))
    post_selection['exclusions'].extend(dict(module=module,
        reason='No changed files or demonstrated dependency impact in this approved module.')
        for module in post_selection['modules'] if module not in affected)
    post_selection['modules'] = affected
    verification = verify(context, post_selection, settings, services, phase=phase,
                          identity=candidate['product_change']['identity'])
    candidate.update(checks=verification['checks'], product_verification=verification,
                     generated_files=sorted(set(candidate['baseline']['generated_files'] + verification['generated_files'])),
                     verification=verification,
                     status='UNVERIFIED' if verification['status'] == 'PASS' else verification['status'])
    if verification['status'] != 'PASS':
        candidate['blockers'].append('Post-change verification did not pass; publication is withheld.')
    else:
        candidate['review'] = review_issue(context, candidate, settings, services)
        candidate['acceptance_coverage'] = candidate['review'].get('acceptance_coverage', [])
        if candidate['review']['status'] != 'PASS':
            candidate['status'] = 'BLOCKED' if candidate['review']['status'] == 'BLOCKED' else 'FAIL'
            candidate['blockers'].append(candidate['review'].get('reason', 'Independent review found changes requiring repair.'))
    return candidate
