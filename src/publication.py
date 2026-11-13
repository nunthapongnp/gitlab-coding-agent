"""Coordinator delivery of one exact independently approved whole-Issue change."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from .coding import artifact_path, collect_candidate, content
from .review import assert_review_current
from .protected_history import protected_state, fingerprint


def publication_environment():
    # Git has many environment overrides (including tracing and executable helpers).
    # Inherit normal host prerequisites, never inherited Git execution/configuration.
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(('GIT_', 'GCM_', 'SSH_'))
           and key.upper() not in {'GITLAB_TOKEN', 'LLM_API_KEY'} }
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_SYSTEM=os.devnull,
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0',
               GCM_INTERACTIVE='never')
    return env


def publish_issue(context, candidate, settings, services):
    """Never retry a commit/push, adopt a changed source, or publish partial work."""
    candidate.setdefault('commit', {'status': 'NOT_ATTEMPTED'})
    candidate.setdefault('push', {'status': 'NOT_ATTEMPTED'})
    if not candidate.get('publication_eligible'):
        return candidate
    phase = 'gate'
    command = services.command_runner or context.runtime.run_command
    try:
        context.remaining(services.clock)
        assert_review_current(context, candidate, services)
        change = candidate['product_change']
        verification = candidate.get('product_verification', {})
        checks = verification.get('checks', [])
        if (verification.get('status') != 'PASS' or verification.get('content_identity') != change['identity']
                or not checks or any(row.get('status') not in {'PASS', 'SKIPPED'} for row in checks)
                or any(row.get('status') == 'SKIPPED' and not row.get('reason') for row in checks)
                or not any(row.get('kind') == 'build' and row.get('status') == 'PASS'
                           and row.get('executed') for row in checks)):
            raise ValueError('Publication requires current passing mandatory verification and justified test outcomes')
        if (not change['files'] or change.get('head') != context.initial_source_commit
                or change.get('initial_head') != context.initial_source_commit
                or change.get('source_branch') != context.source_branch or change.get('staged_paths')):
            raise ValueError('Empty Product Change or unexpected initial history/index')
        with tempfile.TemporaryDirectory(prefix='coding-agent-publication-', dir=context.artifacts) as trusted:
            trusted = Path(trusted)
            hooks = trusted / 'empty-hooks'
            hooks.mkdir()
            env = publication_environment()
            options = ['-c', f'core.hooksPath={hooks}', '-c', 'core.fsmonitor=false',
                       '-c', 'commit.gpgsign=false', '-c', 'credential.helper=']
            def git(*args, binary=False, cwd=None, network=False):
                operation_env = dict(env)
                if network:
                    operation_env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = str(context.checkout / '.git/objects')
                    if settings.gitlab_token and urlsplit(context.project['http_url_to_repo']).scheme in {'http', 'https'}:
                        auth = base64.b64encode(f'{settings.gitlab_git_username}:{settings.gitlab_token}'.encode()).decode()
                        operation_env.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='http.extraHeader',
                                             GIT_CONFIG_VALUE_0='Authorization: Basic ' + auth)
                return command(['git', *options, *args], cwd=cwd or context.checkout,
                    timeout=min(30, context.remaining(services.clock)), env=operation_env,
                    **({'binary': True} if binary else {}))

            if (fingerprint(protected_state(context.checkout, git)) != change.get('initial_history_identity')
                    or change.get('initial_history_identity') != change.get('observed_history_identity')):
                raise ValueError('Unexpected history, Git metadata or staged content after coding')
            current = collect_candidate(context, git, excluded_paths=candidate.get('generated_files', []),
                                        secrets=(settings.gitlab_token, settings.llm_api_key))
            ignored = {'identity', 'initial_history_identity', 'observed_history_identity'}
            if {k: v for k, v in current.items() if k not in ignored} != {
                    k: v for k, v in change.items() if k not in ignored}:
                raise ValueError('Actual Product Change differs from independently reviewed content')
            paths = [row['path'] for row in change['files']]
            for row in change['files']:
                path = row['path']
                target = context.checkout / path
                if (artifact_path(path) or Path(path).is_absolute() or '..' in Path(path).parts
                        or '.git' in Path(path).parts or not target.resolve().is_relative_to(context.checkout.resolve())
                        or row.get('credentials_redacted') or Path(path).name.lower().startswith('.env')
                        or Path(path).suffix.lower() in {'.log', '.db', '.sqlite', '.sqlite3', '.pem', '.key'}):
                    raise ValueError('Product Change contains artifacts, local data or credentials')
                for value in (row['before'], row['after']):
                    if value and value['mode'] not in {'100644', '100755'}:
                        raise ValueError('Symlinks and gitlinks are unsupported publication content')
                attributes = git('--literal-pathspecs', 'check-attr', '-z', 'filter', 'working-tree-encoding', '--', path).split('\0')
                if any(value not in {'unspecified', 'unset', ''} for value in attributes[2::3]):
                    raise ValueError('Configured content filters/encodings require canonical review evidence')
            phase = 'staging'
            git('--literal-pathspecs', 'add', '--', *paths)
            staged = git('diff', '--cached', '--name-only', '--no-renames', '-z').split('\0')
            if set(filter(None, staged)) != set(paths):
                raise ValueError('Unexpected or empty staged Product Change inventory')
            for row in change['files']:
                entry = git('--literal-pathspecs', 'ls-files', '--stage', '-z', '--', row['path'])
                if row['after'] is None:
                    if entry:
                        raise ValueError('Reviewed deletion was not staged')
                else:
                    header = entry.split('\t', 1)[0].split()
                    if len(header) != 3 or header[2] != '0':
                        raise ValueError('Missing or conflicted staged entry')
                    actual = content(git('cat-file', 'blob', header[1], binary=True), header[0])
                    if actual != row['after']:
                        raise ValueError('Staged bytes or mode differ from review; fresh canonical verification required')
            tree = git('write-tree').strip()
            if tree == git('rev-parse', context.initial_source_commit + '^{tree}').strip():
                raise ValueError('Refusing an empty effective Product Change')
            if (git('rev-parse', 'HEAD').strip() != context.initial_source_commit
                    or git('symbolic-ref', '--short', 'HEAD').strip() != context.source_branch):
                raise ValueError('Unexpected source branch/history before aggregate commit')
            phase = 'commit'
            env.update(GIT_AUTHOR_NAME=settings.git_author_name, GIT_AUTHOR_EMAIL=settings.git_author_email,
                       GIT_COMMITTER_NAME=settings.git_author_name, GIT_COMMITTER_EMAIL=settings.git_author_email)
            git('commit', '-m', f'Implement Issue #{context.issue["iid"]}\n\nExecute Run: {context.run_id}')
            sha = git('rev-parse', 'HEAD').strip()
            candidate['commit'] = dict(status='SUCCESS', sha=sha, tree=tree)
            if (git('rev-list', '--parents', '-n', '1', sha).strip().split() != [sha, context.initial_source_commit]
                    or git('rev-parse', sha + '^{tree}').strip() != tree):
                raise ValueError('Aggregate commit does not preserve the accepted source parent/tree')
            # A tiny coordinator-owned repository prevents product Git configuration,
            # URL rewrites, hooks and transport helpers from receiving credentials.
            transport = trusted / 'transport.git'
            git('init', '--bare', str(transport), cwd=trusted)
            destination = context.project['http_url_to_repo']
            parsed = urlsplit(destination)
            if parsed.scheme and parsed.scheme not in {'http', 'https', 'file'} and not Path(destination).is_absolute():
                raise ValueError('Unsupported accepted repository transport')
            if parsed.username or parsed.password:
                raise ValueError('Credentials are forbidden in the accepted repository URL')
            ref = 'refs/heads/' + context.source_branch
            def remote_head():
                rows = git('ls-remote', '--refs', '--', destination, ref, cwd=transport, network=True).splitlines()
                return rows[0].split()[0] if len(rows) == 1 and rows[0].split()[1] == ref else None
            phase = 'remote-precheck'
            # Observation plus ordinary push rejects observed changes and NFF
            # advances. It is not atomic compare-and-swap for a concurrent
            # deletion or rewind; no force/lease/history repair is attempted.
            observed = remote_head()
            if observed != context.initial_source_commit:
                candidate['push'] = dict(status='BLOCKED', ref=ref, observed_sha=observed,
                                         reason='Remote source changed or disappeared since acceptance')
                raise ValueError('Remote source no longer matches the accepted initial source')
            phase = 'push'
            try:
                git('push', '--porcelain', '--', destination, sha + ':' + ref, cwd=transport, network=True)
                candidate['push'] = dict(status='SUCCESS', sha=sha, ref=ref, confirmation='ordinary push completed')
            except Exception as exc:
                result = getattr(exc, 'result', None)
                # A transport exit (even 128) may happen after the server updated
                # the ref. Only an explicit rejected porcelain row proves failure.
                streams = [] if result is None else [result.stdout, result.stderr]
                lines = '\n'.join(value.decode('utf-8', 'replace') if isinstance(value, bytes) else value
                                  for value in streams).splitlines()
                definite = any(line.startswith('!\t') and any(marker in line for marker in
                               ('[rejected]', '[remote rejected]')) for line in lines)
                candidate['push'] = dict(status='FAILED' if definite else 'UNCERTAIN', sha=sha, ref=ref,
                                         error_type=type(exc).__name__)
                if not definite:
                    try:
                        if remote_head() == sha:
                            candidate['push'].update(status='SUCCESS', confirmation='single remote readback')
                    except Exception:
                        pass
            if candidate['push']['status'] != 'SUCCESS':
                raise ValueError('Remote delivery was not confirmed; retain the local aggregate commit')
            candidate.update(status='SUCCESS')
    except Exception as exc:
        expired = isinstance(exc, TimeoutError) or services.clock() >= context.work_deadline
        if expired:
            context.runtime.cancel()
        candidate.update(status='TIMEOUT' if expired else 'BLOCKED', publication_eligible=False)
        candidate.setdefault('blockers', []).append(str(exc) if isinstance(exc, ValueError) else
                                                   'Publication stopped; inspect retained local evidence')
        candidate.setdefault('errors', []).append(dict(phase=phase, error_type=type(exc).__name__))
        if phase == 'commit' and candidate['commit']['status'] != 'SUCCESS':
            candidate['commit'] = dict(status='UNCERTAIN', error_type=type(exc).__name__)
    return candidate
