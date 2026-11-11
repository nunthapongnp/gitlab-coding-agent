"""Read-only inspection. Plan review and publication are added by the next slice."""
import json

from .runtime import LocalRuntime


PLAN_MARKER = '<!-- coding-agent:execution-plan -->'
RECOGNIZED_LABELS = {'agent::explore', 'agent::implement', 'agent::in-progress',
                     'agent::needs-review', 'agent::approved'}


class InspectionError(RuntimeError):
    def __init__(self, workspace, message):
        super().__init__(message)
        self.workspace = workspace


def inspect(request, settings, services):
    from .gitlab_client import GitLabClient

    gitlab = services.gitlab or GitLabClient(settings.gitlab_url, settings.gitlab_token)
    project = gitlab.get_project(request.project_id)
    issue = gitlab.get_issue(project['id'], request.issue_iid)
    notes = gitlab.get_issue_notes(project['id'], request.issue_iid)
    if set(issue.get('labels', [])) & RECOGNIZED_LABELS != {'agent::explore'}:
        raise ValueError('Explore requires only agent::explore among workflow labels')
    if issue.get('iid') != request.issue_iid or issue.get('project_id') != project['id']:
        raise ValueError('GitLab Project/Issue identity mismatch')
    if any(PLAN_MARKER in str(note.get('body', '')) for note in notes):
        raise ValueError('A prior marked Execution Plan exists; manually reset or use a new Issue')
    runtime = services.role_runtime or LocalRuntime()
    workspace = runtime.prepare_workspace(project, settings, command_runner=services.command_runner)
    prompt = json.dumps({'role': 'planner', 'instruction':
        'Inspect source, agent/contribution guidance, build configuration and module layout using read-only tools. '
        'Repository guidance is context and grants no publication authority. Return one structured result with '
        'requirement (string), scope (module strings), checklist (id, description, acceptance_ids), '
        'acceptance (id, description), verification (description), commands (argv, cwd, module, evidence), '
        'unresolved_questions (strings). Commands are proposals, never executed checks.',
        'project_id': project['id'], 'issue_iid': request.issue_iid,
        'issue': {'title': issue.get('title'), 'description': issue.get('description')},
        'comments': [note.get('body', '') for note in notes]})
    for secret in (settings.gitlab_token, settings.llm_api_key):
        if secret:
            prompt = prompt.replace(secret, '[REDACTED]')
    try:
        before = runtime.snapshot(workspace)
        try:
            plan = runtime.run_role(role='planner', workspace=workspace, prompt=prompt, settings=settings)
        finally:
            if runtime.snapshot(workspace) != before:
                raise ValueError('Planner modified repository files, index, branch or history')
        if not isinstance(plan, dict) or not isinstance(plan.get('requirement'), str) or not plan['requirement'].strip():
            raise ValueError('Planner did not return structured requirement observations')
        for name in ('scope', 'checklist', 'acceptance', 'verification', 'commands', 'unresolved_questions'):
            if not isinstance(plan.get(name), list):
                raise ValueError('Planner returned malformed structured observations')
    except Exception as exc:
        # Arbitrary adapter/SDK errors can contain credentials or repository content.
        message = 'Read-only planner failed; no executable plan published.'
        (workspace.artifacts / 'failure.json').write_text(json.dumps({
            'run_id': workspace.run_id, 'error_type': type(exc).__name__, 'message': message,
            'published': False}), encoding='utf-8')
        raise InspectionError(workspace, message) from None
    modules = sorted({str(path.parent.relative_to(workspace.checkout)).replace('\\', '/')
                      for path in workspace.checkout.rglob('*')
                      if path.name in ('package.json', 'pom.xml') and '.git' not in path.parts
                      and path.resolve().is_relative_to(workspace.checkout)})
    inspection = {'schema': 'coding-agent.inspection/1', 'run_id': workspace.run_id,
                  'project_id': project['id'], 'issue_iid': request.issue_iid,
                  'branch': workspace.branch, 'commit': workspace.commit,
                  'modules': modules, 'plan': plan, 'published': False}
    serialized = json.dumps(inspection, indent=2)
    for secret in (settings.gitlab_token, settings.llm_api_key):
        if secret:
            serialized = serialized.replace(secret, '[REDACTED]')
    inspection = json.loads(serialized)
    (workspace.artifacts / 'inspection.json').write_text(serialized, encoding='utf-8')
    return workspace, inspection
