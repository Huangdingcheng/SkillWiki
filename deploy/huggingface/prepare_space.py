"""Export a reviewable Docker Space folder, without credentials or local data."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]


def selected(path: str) -> bool:
    parts = Path(path).parts
    if any(part in {'.git', '__pycache__', 'node_modules', 'dist', 'logs', 'SkillStorage'} for part in parts):
        return False
    if any(part.startswith('.env') or part == 'config.local.json' for part in parts):
        return False
    if path.endswith(('.pyc', '.pem', '.key')):
        return False
    return (path.startswith('skillwiki/skillwiki/')
            or path in {'skillwiki/pyproject.toml', 'skillwiki/README.md', 'LICENSE', 'DEPLOYMENT.md', 'TESTING.md', 'USER_GUIDE.md'}
            or path.startswith('user-guide-examples/')
            or path.startswith('skillwiki-frontend/src/')
            or path.startswith('skillwiki-frontend/public/')
            or path in {'skillwiki-frontend/package.json', 'skillwiki-frontend/package-lock.json', 'skillwiki-frontend/index.html', 'skillwiki-frontend/vite.config.ts'}
            or (path.startswith('skillwiki-frontend/tsconfig') and path.endswith('.json')))


def export(destination: Path) -> dict:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError('Destination must not exist; exports never overwrite a previous package.')
    if destination == ROOT or ROOT in destination.parents:
        raise ValueError('Choose a destination outside the source checkout.')
    tracked = subprocess.check_output(['git', '-C', str(ROOT), 'ls-files', '-z']).decode().split(chr(0))
    paths = {path for path in tracked if path and selected(path)}
    paths.update({'Dockerfile', 'Dockerfile.dockerignore'})
    paths.update(path.relative_to(ROOT).as_posix() for path in (ROOT / 'deploy/huggingface').glob('*') if path.is_file() and path.suffix in {'.py', '.md', '.conf'})
    required = {'Dockerfile', 'Dockerfile.dockerignore', 'deploy/huggingface/start.py', 'deploy/huggingface/nginx.conf', 'deploy/huggingface/README.md', 'skillwiki/pyproject.toml', 'skillwiki-frontend/package-lock.json'}
    for name in paths | required:
        source = ROOT / name
        if not source.is_file() or source.is_symlink():
            raise ValueError('Missing or symbolic-link package input: ' + name)
    destination.mkdir(parents=True)
    files = {}
    for name in sorted(paths):
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
        files[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    shutil.copyfile(ROOT / 'deploy/huggingface/README.md', destination / 'README.md')
    files['README.md'] = hashlib.sha256((destination / 'README.md').read_bytes()).hexdigest()
    changes = subprocess.check_output(['git', '-C', str(ROOT), 'diff', 'HEAD', '--name-only']).decode().splitlines()
    manifest = {
        'schema': 'skillwiki-space-package-v1',
        'source_commit': subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).decode().strip(),
        'source_identity': 'working-tree file hashes; commit alone is not the complete identity',
        'included_tracked_modifications': sorted(set(changes) & paths),
        'files_sha256': files,
        'seeded_data': False,
        'remote_deployment_performed': False,
    }
    (destination / 'space-manifest.json').write_text(json.dumps(manifest, indent=2) + chr(10), encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    result = export(args.destination)
    print(json.dumps({'files': len(result['files_sha256']), 'source_commit': result['source_commit'], 'seeded_data': False}))
