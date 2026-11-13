"""The exact protected Git state shared by coding and the publication gate."""
import hashlib
import json


def protected_state(checkout, git):
    root = checkout / '.git'
    paths = [root / name for name in ('config', 'HEAD', 'packed-refs', 'info/exclude')]
    if (root / 'hooks').is_dir():
        paths.extend((root / 'hooks').rglob('*'))
    metadata = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in paths if path.is_file()}
    return (git('rev-parse', 'HEAD').strip(), git('symbolic-ref', '--short', 'HEAD').strip(),
            git('show-ref'), git('reflog', '--all', '--format=%H%x00%gD%x00%gs'),
            git('ls-files', '--stage', '-z'), metadata)


def fingerprint(state):
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()
