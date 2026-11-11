"""Explicit MR acceptance and fresh Execute Run preparation."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import uuid

from .agreement import PLAN_MARKER, parse_plan_comment
from .gitlab_client import GitLabClient, RECOGNIZED_LABELS
from .runtime import LocalRuntime, Workspace, clean_environment

RESTART_GUIDANCE = 'A new implementation attempt requires a new source branch/MR; prior execution state cannot be reused.'
REPORT_MARKER = '<!-- coding-agent:execution-report'


@dataclass
class RunContext:
    run_id: str
    accepted_at: float
    deadline: float
    started_at: str
    project: dict
    issue: dict
    mr: dict
    agreement: dict
    plan_comment_id: int
    source_branch: str
    target_branch: str
    artifacts: Path
    checkout: Path
    initial_source_commit: str | None = None
    workspace: Workspace | None = None
    runtime: object = field(default=None, repr=False)
    gitlab: object = field(default=None, repr=False)

    @property
    def work_deadline(self):
        # Keep normal report/label work inside the accepted run's deadline.
        return self.deadline - min(30.0, (self.deadline - self.accepted_at) / 10)

    def remaining(self, clock):
        remaining = self.work_deadline - clock()
        if remaining <= 0:
            self.runtime.cancel()
            raise TimeoutError('Execute Run work deadline expired; finalization budget reserved')
        return remaining

    def metadata(self):
        return dict(run_id=self.run_id, accepted_at=self.accepted_at, deadline=self.deadline,
                    work_deadline=self.work_deadline,
                    started_at=self.started_at, project_id=self.project['id'],
                    issue_iid=self.issue['iid'], mr_iid=self.mr['iid'],
                    plan_comment_id=self.plan_comment_id, source_branch=self.source_branch,
                    target_branch=self.target_branch, initial_source_commit=self.initial_source_commit,
                    checkout=str(self.checkout), artifacts=str(self.artifacts))


class RunPreparationError(RuntimeError):
    def __init__(self, context, message, *, cause=None):
        super().__init__(message)
        self.context = context
        self.status = 'TIMEOUT' if isinstance(cause, TimeoutError) else 'BLOCKED'
        # This stays in memory until the finalizer redacts it for every output.
        self.failure = {'phase': 'preparation', 'error_type': type(cause).__name__,
                        'message': str(cause)} if cause is not None else None


def _linked(mr, related, project, issue_iid):
    if any(item.get('iid') == mr['iid'] and item.get('project_id') == project['id'] for item in related):
        return True
    # Accept only an entire closing-reference line, with an exact same-project ref.
    # General prose/branch substrings and cross-project references grant no authority.
    path = project.get('path_with_namespace', '')
    short = rf'#{issue_iid}'
    full = re.escape(path) + short if path else r'(?!)'
    reference = rf'(?:{short}|{full})'
    return any(re.fullmatch(rf'\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+{reference}\s*[.]?\s*',
                            line, re.IGNORECASE)
               for line in str(mr.get('description') or '').splitlines())


def prepare_run(request, settings, services):
    gitlab = services.gitlab or GitLabClient(settings.gitlab_url, settings.gitlab_token)
    project = gitlab.get_project(request.project_id)
    issue = gitlab.get_issue(project['id'], request.issue_iid)
    if issue.get('iid') != request.issue_iid or issue.get('project_id') != project['id']:
        raise ValueError('GitLab Project/Issue identity mismatch')
    if set(issue.get('labels', [])) & RECOGNIZED_LABELS != {'agent::approved'}:
        raise ValueError('Implement requires only agent::approved among Issue workflow labels.')
    marked = [note for note in gitlab.get_issue_notes(project['id'], request.issue_iid)
              if PLAN_MARKER in str(note.get('body', ''))]
    if len(marked) != 1:
        raise ValueError('Implement requires exactly one marked executable Execution Plan.')
    note = marked[0]
    if type(note.get('id')) is not int or note['id'] < 1:
        raise ValueError('Execution Plan comment identity is missing.')
    agreement = parse_plan_comment(note['body'], project_id=project['id'], issue_iid=request.issue_iid)
    mr = gitlab.get_merge_request(project['id'], request.mr_iid)
    if (mr.get('iid') != request.mr_iid or mr.get('project_id') != project['id']
            or mr.get('source_project_id') != project['id'] or mr.get('target_project_id') != project['id']):
        raise ValueError('Implement requires the explicit same-Project MR and same-Project source.')
    if mr.get('state') != 'opened':
        raise ValueError('Implement requires an open MR.')
    if set(mr.get('labels', [])) & RECOGNIZED_LABELS != {'agent::implement'}:
        raise ValueError('Implement requires only agent::implement among MR workflow labels. ' + RESTART_GUIDANCE)
    source, target = mr.get('source_branch'), mr.get('target_branch')
    if (not isinstance(source, str) or not source or source.startswith('-')
            or not isinstance(target, str) or not target or target.startswith('-') or source == target):
        raise ValueError('MR source and target must be distinct valid existing branches.')
    if target != agreement['branch']:
        raise ValueError('MR target must match the Approved Plan baseline branch.')
    for branch in (source, target):
        if gitlab.get_branch(project['id'], branch).get('name') != branch:
            raise ValueError('MR source or target branch does not exist.')
    if not _linked(mr, gitlab.get_issue_related_merge_requests(project['id'], request.issue_iid),
                   project, request.issue_iid):
        raise ValueError('Explicit MR has no verified same-Project Issue relationship.')
    if any(REPORT_MARKER in str(item.get('body', '')) for item in
           gitlab.get_merge_request_notes(project['id'], request.mr_iid)):
        raise ValueError('MR already contains a final agent Execution Report. ' + RESTART_GUIDANCE)

    # Acceptance starts the shared deadline before any lifecycle writes or clone.
    accepted = services.clock()
    run_id = uuid.uuid4().hex
    root = settings.output_dir.resolve() / ('implement-' + run_id)
    if any((parent / '.git').exists() for parent in (root, *root.parents)):
        raise ValueError('Output directory must be outside Git checkouts')
    context = RunContext(run_id, accepted, accepted + settings.execution_max_runtime_seconds,
                         datetime.now(timezone.utc).isoformat(), project, issue, mr, agreement, note['id'],
                         source, target, root / 'artifacts', root / 'checkout',
                         runtime=services.role_runtime or LocalRuntime(), gitlab=gitlab)
    try:
        context.runtime.bind_deadline(context.work_deadline, services.clock)
        context.artifacts.mkdir(parents=True)
        (context.artifacts / 'run-context.json').write_text(json.dumps(context.metadata(), indent=2), encoding='utf-8')
        gitlab.replace_merge_request_status(project['id'], request.mr_iid,
                                            'agent::implement', 'agent::in-progress',
                                            deadline=context.work_deadline, clock=services.clock)
        context.workspace = context.runtime.prepare_workspace(project, settings,
            command_runner=services.command_runner, branch=source, run_id=run_id, root=root,
            deadline=context.work_deadline, clock=services.clock)
        context.initial_source_commit = context.workspace.commit
        command = services.command_runner or context.runtime.run_command
        remaining = context.remaining(services.clock)
        if remaining <= 0:
            raise RuntimeError('Execute Run deadline expired')
        clean = command(['git', 'status', '--porcelain', '--untracked-files=all'],
                        cwd=context.checkout, timeout=min(30, remaining), env=clean_environment())
        if clean.strip():
            raise RuntimeError('Fresh source checkout is not clean')
        remaining = context.remaining(services.clock)
        if remaining <= 0:
            raise RuntimeError('Execute Run deadline expired')
        branch = command(['git', 'symbolic-ref', '--short', 'HEAD'], cwd=context.checkout,
                         timeout=min(30, remaining), env=clean_environment()).strip()
        if branch != context.source_branch:
            raise RuntimeError('Fresh checkout does not match the selected source branch')
        (context.artifacts / 'run-context.json').write_text(json.dumps(context.metadata(), indent=2), encoding='utf-8')
    except Exception as exc:
        raise RunPreparationError(context,
            'Accepted Execute Run preparation failed; coding and publication did not start. Inspect local diagnostics and MR state.',
            cause=exc) from None
    return context
