"""Prepare only tracked deployment inputs for the verified workflow artifact."""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from deploy.patch_runtime import read_manifest


def build(root, destination, commit):
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if not re.fullmatch(r'[a-f0-9]{40}', commit):
        raise ValueError('Expected an exact commit')
    if destination.exists():
        raise ValueError('Package destination must be new')
    names = subprocess.check_output(['git', 'ls-files', '-z'], cwd=root).decode().split('\0')
    selected = []
    for name in filter(None, names):
        path = Path(name)
        if path.is_absolute() or '..' in path.parts:
            raise ValueError('Invalid source path')
        if not (len(path.parts) == 1 and (path.suffix == '.py' or name == 'requirements-app.txt')
                or path.parts[0] in ('deploy', 'web_assets')):
            continue
        source = root / path
        if source.is_symlink() or not source.resolve().is_relative_to(root) or not source.is_file():
            raise ValueError('Source must be a regular file inside the checkout')
        selected.append((name, source))
    required = {'deploy/deploy.ps1', 'deploy/changes.json', 'service.py', 'requirements-app.txt'}
    required.update(read_manifest(root)['files'])
    if not required.issubset({name for name, _ in selected}):
        raise ValueError('Deployment inputs missing')
    destination.mkdir(parents=True)
    for name, source in selected:
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    (destination / 'source-commit.txt').write_text(commit + '\n', encoding='ascii')
    return len(selected)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination', required=True)
    parser.add_argument('--commit', required=True)
    args = parser.parse_args()
    print('Deployment inputs packaged:', build(Path(__file__).resolve().parent.parent, args.destination, args.commit))
