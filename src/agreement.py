"""The durable, unversioned Execution Plan contract consumed from GitLab."""
from __future__ import annotations

from copy import deepcopy
from html import escape
import json
from pathlib import PurePosixPath, PureWindowsPath
import re


PLAN_MARKER = '<!-- coding-agent:execution-plan -->'
PLAN_SCHEMA = 'coding-agent.execution-plan/1'
RESET_GUIDANCE = ('Use a new Issue or manually reset this Issue: delete the old plan comments, '
                  'clear obsolete recognized status/approval labels, then request agent::explore again.')


class AgreementError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise AgreementError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def validate_agreement(value, *, project_id=None, issue_iid=None):
    """Return a detached, strict executable agreement or actionable diagnostics."""
    fields = {'schema', 'project_id', 'issue_iid', 'branch', 'commit', 'requirement',
              'scope', 'checklist', 'acceptance', 'verification', 'commands', 'unresolved_questions'}
    _require(isinstance(value, dict) and set(value) == fields, 'Agreement fields are missing or unsupported.')
    _require(value['schema'] == PLAN_SCHEMA, 'Unsupported Execution Plan schema.')
    for name, expected in [('project_id', project_id), ('issue_iid', issue_iid)]:
        _require(type(value[name]) is int and value[name] > 0, f'Invalid {name}.')
        _require(expected is None or value[name] == expected, f'Execution Plan {name} mismatch.')
    for name in ('branch', 'requirement'):
        _require(_text(value[name]), f'Execution Plan requires {name}.')
    _require(isinstance(value['commit'], str) and re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', value['commit']),
             'Execution Plan requires the inspected commit SHA.')
    _require(isinstance(value['scope'], list) and value['scope'] and all(map(_text, value['scope'])),
             'Scope must identify the affected modules.')
    _require(len(set(value['scope'])) == len(value['scope']), 'Duplicate scope module.')
    for name, keys in [('acceptance', {'id', 'description'}),
                       ('checklist', {'id', 'description', 'acceptance_ids'}),
                       ('verification', {'description'})]:
        rows = value[name]
        _require(isinstance(rows, list) and rows, f'Execution Plan requires {name}.')
        for row in rows:
            _require(isinstance(row, dict) and set(row) == keys and _text(row['description']),
                     f'Malformed {name} item.')
            if 'id' in keys:
                _require(isinstance(row['id'], str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]*', row['id']),
                         f'Invalid {name} identifier.')
        if 'id' in keys:
            _require(len({row['id'] for row in rows}) == len(rows), f'Duplicate {name} identifier.')
    acceptance = {row['id'] for row in value['acceptance']}
    mapped = set()
    for row in value['checklist']:
        ids = row['acceptance_ids']
        _require(isinstance(ids, list) and ids and all(isinstance(item, str) for item in ids),
                 'Checklist items require acceptance mappings.')
        _require(set(ids) <= acceptance and len(set(ids)) == len(ids), 'Unknown or duplicate acceptance mapping.')
        mapped.update(ids)
    _require(mapped == acceptance, 'Every acceptance criterion requires a checklist mapping.')
    _require(isinstance(value['commands'], list), 'Suggested commands must be a list.')
    for command in value['commands']:
        _require(isinstance(command, dict) and set(command) == {'argv', 'cwd', 'module', 'evidence'},
                 'Malformed suggested command.')
        _require(isinstance(command['argv'], list) and command['argv'] and all(map(_text, command['argv'])),
                 'Suggested commands require argv.')
        cwd = command['cwd']
        _require(_text(cwd) and not PurePosixPath(cwd).is_absolute() and not PureWindowsPath(cwd).drive
                 and not PureWindowsPath(cwd).root and '..' not in cwd.replace('\\', '/').split('/'),
                 'Command cwd must stay inside the checkout.')
        _require(command['module'] in value['scope'] and _text(command['evidence']),
                 'Suggested commands require a scoped module and evidence.')
    questions = value['unresolved_questions']
    _require(isinstance(questions, list) and all(map(_text, questions)), 'Malformed unresolved questions.')
    _require(not questions, 'Unresolved requirement questions prevent an executable plan: ' + '; '.join(questions))
    return deepcopy(value)


def create_agreement(inspection):
    _require(isinstance(inspection['plan'], dict), 'Planner must return an agreement object.')
    proposal = dict(inspection['plan'])
    proposal.setdefault('commands', [])
    return validate_agreement({**proposal, 'schema': PLAN_SCHEMA,
        **{name: inspection[name] for name in ('project_id', 'issue_iid', 'branch', 'commit')}})


def render_plan_comment(agreement):
    agreement = validate_agreement(agreement)
    def readable(value):
        return escape(str(value), quote=False).replace('\n', '<br>')
    lines = [PLAN_MARKER, '# Execution Plan', '',
        f"Schema: {agreement['schema']}",
        f"Project: {agreement['project_id']}; Issue: #{agreement['issue_iid']}",
        f"Inspected branch: {readable(agreement['branch'])}; commit: {agreement['commit']}",
        '', '## Requirement', readable(agreement['requirement']), '', '## Scope / modules']
    lines.extend('- ' + readable(module) for module in agreement['scope'])
    lines += ['', '## Implementation checklist']
    lines.extend(f"- [ ] {item['id']}: {readable(item['description'])} (acceptance: {', '.join(item['acceptance_ids'])})"
                 for item in agreement['checklist'])
    lines += ['', '## Acceptance criteria']
    lines.extend(f"- {item['id']}: {readable(item['description'])}" for item in agreement['acceptance'])
    lines += ['', '## Verification expectations']
    lines.extend('- ' + readable(item['description']) for item in agreement['verification'])
    lines += ['', '## Suggested commands (not executed)']
    lines.extend(f"- Module: {readable(item['module'])}; cwd: {readable(item['cwd'])}; "
                 f"argv: {readable(json.dumps(item['argv']))}; evidence: {readable(item['evidence'])}"
                 for item in agreement['commands'])
    if not agreement['commands']:
        lines.append('None proposed. Discover suitable commands during implement.')
    lines += ['', '## Unresolved questions', 'None.', '',
              'Human approval: replace agent::needs-review with agent::approved.', RESET_GUIDANCE,
              '', '## Machine-readable agreement', '```json',
              json.dumps(agreement, indent=2, ensure_ascii=True), '```']
    return '\n'.join(lines)


def parse_plan_comment(body, *, project_id=None, issue_iid=None):
    """Parse the actual comment, rejecting divergent readable/structured content."""
    _require(isinstance(body, str) and body.count(PLAN_MARKER) == 1, 'Missing or ambiguous Execution Plan marker.')
    match = re.search(r'\n```json\n(.*?)\n```\s*$', body, re.DOTALL)
    _require(match is not None, 'Missing machine-readable Execution Plan agreement.')
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                _require(key not in result, 'Duplicate agreement field.')
                result[key] = value
            return result
        value = json.loads(match[1], object_pairs_hook=unique_object)
    except (ValueError, TypeError) as exc:
        raise AgreementError('Invalid machine-readable Execution Plan agreement.') from exc
    agreement = validate_agreement(value, project_id=project_id, issue_iid=issue_iid)
    _require(body.strip() == render_plan_comment(agreement), 'Readable plan differs from its machine-readable agreement.')
    return agreement
