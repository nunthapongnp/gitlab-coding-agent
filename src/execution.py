"""Whole-Issue execution sequencing; later review/repair/publication consume its evidence."""
from pathlib import Path
import json
from .checks import discover, verify
from .coding import code_issue


def execute_issue(context, settings, services):
    selection = discover(context)
    baseline = verify(context, selection, settings, services, phase='baseline',
                      identity=context.initial_source_commit)
    if baseline['status'] != 'PASS':
        return dict(status='BLOCKED', summary='Baseline verification stopped this run before coding.',
                    blockers=['Existing baseline failed or required verification is unavailable.'],
                    baseline=baseline, checks=baseline['checks'],
                    product_verification={'status': baseline['status']})
    candidate = code_issue(context, settings, services, excluded_paths=baseline['generated_files'])
    candidate['baseline'] = baseline
    if candidate['status'] == 'BLOCKED':
        return candidate
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
    verification = verify(context, post_selection, settings, services, phase='post-change',
                          identity=candidate['product_change']['identity'])
    candidate.update(checks=verification['checks'], product_verification=verification,
                     generated_files=sorted(set(baseline['generated_files'] + verification['generated_files'])),
                     verification=verification,
                     status='UNVERIFIED' if verification['status'] == 'PASS' else verification['status'])
    if verification['status'] != 'PASS':
        candidate['blockers'].append('Post-change verification did not pass; publication is withheld.')
    return candidate
