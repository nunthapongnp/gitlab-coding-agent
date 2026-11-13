"""One best-effort final handoff for each accepted Execute Run."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import re


def _redact(text, settings):
    for value in (settings.gitlab_token, settings.llm_api_key):
        if value:
            variants = {value, json.dumps(value, ensure_ascii=False)[1:-1],
                        json.dumps(value, ensure_ascii=True)[1:-1]}
            for variant in sorted(variants, key=len, reverse=True):
                text = text.replace(variant, '[REDACTED]')
    return re.sub(r'(https?://)[^\s/@]+:[^\s/@]+@', r'\1[REDACTED]@', text)


def _safe(value, settings):
    # Redact values before serialization so quote/backslash-containing secrets
    # cannot survive JSON escaping or invalidate the aggregate's JSON syntax.
    def clean(item):
        if isinstance(item, str):
            return _redact(item, settings)
        if isinstance(item, dict):
            return {_redact(str(key), settings): clean(value) for key, value in item.items()}
        if isinstance(item, list):
            return [clean(value) for value in item]
        return item
    return clean(json.loads(json.dumps(value, default=str, ensure_ascii=False)))


def _render(result):
    provenance = result['provenance']
    lines = [result['marker'], '# Execution Report', '',
        f"Run `{result['run_id']}` — **{result['status']}**", '',
        f"Project {provenance['project_id']} · Issue #{provenance['issue_iid']} · "
        f"MR !{provenance['mr_iid']} · Plan comment {provenance['plan_comment_id']}",
        f"Source `{provenance['source_branch']}` → target `{provenance['target_branch']}`; "
        f"initial commit `{provenance['initial_source_commit'] or 'unavailable (preparation stopped)'}`.",
        f"Started {result['started_at']}; ended {result['ended_at']}; duration {result['duration_seconds']:.3f}s.",
        '', '## Changed behavior', result['summary'], '', 'Changed files:',
        *(['- ' + path for path in result['changed_files']] or ['- None recorded.'])]
    for heading, key in [('Acceptance coverage', 'acceptance_coverage'),
                         ('Verification commands, directories and results', 'checks'),
                         ('Product verification', 'product_verification'),
                         ('Skip and blocker reasons', 'blockers'), ('Review findings', 'review'),
                         ('Repairs', 'repairs'), ('Commit outcome', 'commit'), ('Push outcome', 'push'),
                         ('Diagnostic details', 'errors'),
                         ('Report delivery', 'report_delivery'), ('MR label update', 'label_update')]:
        lines.extend(['', '## ' + heading, '```json',
                      json.dumps(result[key], indent=2, ensure_ascii=False), '```'])
    lines.extend(['', 'Delivery and label results above describe the state at report submission. '
                  'The retained local result records their final independent outcomes.',
                  '', '## Diagnostics', result['diagnostics']['directory'],
                  'Final result: execution-result.json; report: execution-report.md; log: finalization.log.',
                  '', '## Next human action', result['next_human_action']])
    # GitLab interprets standalone slash commands before rendering Markdown.
    # Neutralize every possible command line, including model-supplied fence
    # escapes; the original structured evidence remains in the local result.
    # GitLab's note extractor removes raw CR and trims trailing whitespace.
    # Submit exactly that canonical body so strict delivery readback remains valid.
    body = '\n'.join(lines).replace('\r', '').rstrip()
    return '\n'.join(line.replace('/', '&#47;', 1) if line.lstrip().startswith('/') else line
                     for line in body.split('\n'))


def finalize_run(context, result, settings, services):
    """Aggregate orchestrator evidence; never promote model prose to a passing gate.

    The coordinator supplies checks/review/commit/push evidence. Missing evidence
    stays unverified/unattempted, including when an implementation is unavailable.
    Each GitLab operation uses the same absolute deadline, including paginated
    readback. There is one POST attempt and no background retry or resume state.
    """
    ended = services.clock()
    aggregate = dict(result)
    aggregate.update(schema='coding-agent.execution-result/1', run_id=context.run_id,
        marker=f'<!-- coding-agent:execution-report run={context.run_id} -->',
        provenance=context.metadata(), started_at=context.started_at,
        ended_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=max(0, ended - context.accepted_at))
    aggregate.setdefault('status', 'BLOCKED')
    aggregate.setdefault('summary', 'No Product Change was completed or pushed.')
    aggregate.setdefault('changed_files', [])
    aggregate.setdefault('blockers', [])
    aggregate.setdefault('checks', [])
    aggregate.setdefault('errors', [])
    aggregate.setdefault('acceptance_coverage', [dict(item, status='UNVERIFIED',
        reason='No completed verification/review evidence supplied.') for item in context.agreement['acceptance']])
    aggregate.setdefault('product_verification', {'status': 'UNVERIFIED', 'reason': 'Required checks have not passed.'})
    aggregate.setdefault('review', {'status': 'NOT_RUN', 'findings': []})
    aggregate.setdefault('repairs', [])
    aggregate.setdefault('commit', {'status': 'NOT_ATTEMPTED'})
    aggregate.setdefault('push', {'status': 'NOT_ATTEMPTED'})
    aggregate['report_delivery'] = {'status': 'PENDING'}
    aggregate['label_update'] = {'status': 'PENDING'}
    aggregate['diagnostics'] = {'directory': str(context.artifacts), 'status': 'PENDING'}
    # Independent delivery failures never rewrite confirmed commit/push evidence.
    if aggregate['status'] == 'SUCCESS' and not (
            aggregate['product_verification'].get('status') == 'PASS'
            and aggregate['review'].get('status') == 'PASS'
            and aggregate['commit'].get('status') == 'SUCCESS'
            and aggregate['push'].get('status') == 'SUCCESS'):
        aggregate['status'] = 'UNVERIFIED'
        aggregate['blockers'].append('Success requires confirmed verification, independent review, commit and push evidence.')
    aggregate.setdefault('next_human_action',
        'Inspect this report and local diagnostics. Complete remaining manual acceptance checks before review/merge.'
        if aggregate['status'] == 'SUCCESS' else
        'Inspect blockers and local diagnostics. To retry, create a new source branch and MR; this run cannot resume.')
    aggregate = _safe(aggregate, settings)
    events = []

    def persist(body):
        try:
            context.artifacts.mkdir(parents=True, exist_ok=True)
            # Runtime logs may contain repository/model text. Sanitize the retained
            # diagnostic area as well as new output; never follow links outside it.
            root = context.artifacts.resolve()
            for path in context.artifacts.rglob('*'):
                if path.is_file() and path.resolve().is_relative_to(root):
                    # Preserve undecodable bytes while still filtering secrets
                    # in process output that is not valid UTF-8.
                    original = path.read_bytes().decode('utf-8', errors='surrogateescape')
                    clean = _redact(original, settings)
                    if clean != original:
                        path.write_bytes(clean.encode('utf-8', errors='surrogateescape'))
            aggregate['diagnostics']['status'] = 'SAVED'
            (context.artifacts / 'execution-report.md').write_text(body, encoding='utf-8', newline='')
            (context.artifacts / 'finalization.log').write_text(_redact('\n'.join(events), settings), encoding='utf-8')
            (context.artifacts / 'execution-result.json').write_text(
                json.dumps(aggregate, indent=2, ensure_ascii=False), encoding='utf-8')
        except OSError as exc:
            aggregate['diagnostics'].update(status='FAILED', error_type=type(exc).__name__)

    body = _render(aggregate)
    persist(body)  # Preserve outcome before best-effort remote delivery.
    budget = {'deadline': context.deadline, 'clock': services.clock}
    project, mr = context.project['id'], context.mr['iid']
    report_attempted = False
    try:
        if services.clock() >= context.deadline:
            raise TimeoutError('No run budget remains for report submission')
        report_attempted = True
        note = context.gitlab.create_merge_request_note(project, mr, body, **budget)
        if (not isinstance(note, dict) or type(note.get('id')) is not int or note['id'] <= 0
                or note.get('body') != body):
            raise ValueError('Report submission response did not confirm the comment')
        aggregate['report_delivery'] = {'status': 'DELIVERED', 'comment_id': note['id']}
    except Exception as exc:
        events.append('Report submission unconfirmed: ' + type(exc).__name__ + ': ' + str(exc))
        aggregate['report_delivery'] = {'status': 'UNCERTAIN' if report_attempted else 'NOT_ATTEMPTED',
            'error_type': type(exc).__name__,
            'reason': 'Submission was not confirmed; no duplicate POST was attempted.'}
        try:
            if not report_attempted or services.clock() >= context.deadline:
                raise TimeoutError('No run budget remains for report readback')
            matches = [note for note in context.gitlab.get_merge_request_notes(project, mr, **budget)
                       if aggregate['marker'] in str(note.get('body', ''))]
            if (len(matches) == 1 and matches[0].get('body') == body
                    and type(matches[0].get('id')) is int and matches[0]['id'] > 0):
                aggregate['report_delivery'] = {'status': 'DELIVERED', 'comment_id': matches[0]['id']}
            elif not matches:
                aggregate['report_delivery']['reason'] = 'Run marker was not visible on readback; delivery remains uncertain.'
        except Exception as read_error:
            events.append('Report readback failed: ' + type(read_error).__name__ + ': ' + str(read_error))
    label_attempted = False
    try:
        if services.clock() >= context.deadline:
            raise TimeoutError('No run budget remains for MR label update')
        label_attempted = True
        context.gitlab.replace_merge_request_status(project, mr,
            'agent::in-progress', 'agent::needs-review', **budget)
        aggregate['label_update'] = {'status': 'UPDATED'}
    except Exception as exc:
        aggregate['label_update'] = {'status': 'FAILED' if label_attempted else 'NOT_ATTEMPTED',
                                    'error_type': type(exc).__name__,
                                    'reason': 'MR review-label replacement was not confirmed; inspect GitLab.'}
        events.append('MR label update failed: ' + type(exc).__name__ + ': ' + str(exc))
    events.append('Finalization completed; diagnostics are evidence only, never resume state.')
    # The remote body is necessarily an as-of-submission snapshot. The final
    # retained aggregate includes the time spent on all normal handoff APIs.
    aggregate['ended_at'] = datetime.now(timezone.utc).isoformat()
    aggregate['duration_seconds'] = max(0, services.clock() - context.accepted_at)
    persist(body)
    return aggregate
