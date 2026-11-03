import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))


def digest(data):
    return hashlib.sha256(data.replace(b'\r\n', b'\n').rstrip() + b'\n').hexdigest()


def read_manifest(source):
    manifest = json.loads((source / 'deploy/changes.json').read_text('utf-8'))
    for name, rule in manifest['files'].items():
        path = Path(name)
        if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
            raise ValueError('Invalid patch path')
        if not (len(path.parts) == 1 and path.suffix == '.py' or
                len(path.parts) == 2 and path.parts[0] == 'web_assets' and path.suffix in ('.css', '.js', '.png')):
            raise ValueError('Patch targets application Python and web assets only')
        data = (source / name).read_bytes()
        if digest(data) != rule['after']:
            raise ValueError('Source checksum mismatch: ' + name)
        if name.endswith('.py'):
            ast.parse(data, filename=name)
    return manifest


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.threadle-new')
    temporary.write_bytes(data)
    os.replace(temporary, path)


def prepare(root, source, job):
    manifest = read_manifest(source)
    if job.exists():
        raise ValueError('Deployment job already exists')
    for name, rule in manifest['files'].items():
        target = root / name
        if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
            raise ValueError('Unexpected target location: ' + name)
        actual = digest(target.read_bytes()) if target.exists() else None
        if actual not in [rule['before'], rule['after'], *rule.get('previous', [])]:
            raise ValueError('Server differs from supported baseline; no files changed: ' + name)
    job.mkdir(parents=True)
    candidate = job / 'candidate'
    candidate.mkdir()
    for path in root.glob('*.py'):
        shutil.copy2(path, candidate / path.name)
    records = {}
    for name, rule in manifest['files'].items():
        target = root / name
        data = target.read_bytes() if target.exists() else None
        records[name] = hashlib.sha256(data).hexdigest() if data is not None else None
        if data is not None:
            backup = job / 'backup' / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(data)
        staged = candidate / name
        staged.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, staged)
    (job / 'before.json').write_text(json.dumps(records), 'utf-8')
    (job / 'manifest.json').write_text(json.dumps(manifest), 'utf-8')
    for path in candidate.glob('*.py'):
        ast.parse(path.read_bytes(), filename=path.name)
    check = ('import sys; sys.path.insert(0,sys.argv[1]); '
             'import settings_audit, guild_plans, operator_settings, state_backups, api_cooldowns, delivery_metrics, metadata_notifications, merge_conflicts, deferred_threads, notification_text, root_status, digest_settings, dnd_notifications, message_edits, commit_notifications, notification_markdown, service, auth, discord_threads, notification_media, demo_web, channel_topics, connection_welcome, notification_language; '
             'assert callable(auth.RepositoryApi.fetch_media); '
             'assert callable(notification_media.installation_image); '
             'assert demo_web.demonstration(lambda ko,en:en).count("preview-case ")==5; '
             'print("Candidate imports and media dependency verified")')
    subprocess.run([os.sys.executable, '-B', '-I', '-c', check, str(candidate)], check=True)
    from deploy.media_dependency import stage
    stage(root,source,job)
    print('Preflight passed: ' + manifest['version'])


def apply(root, job):
    records = json.loads((job / 'before.json').read_text('utf-8'))
    manifest = json.loads((job / 'manifest.json').read_text('utf-8'))
    # Check every target again before the first write.
    for name, expected in records.items():
        target = root / name
        actual = hashlib.sha256(target.read_bytes()).hexdigest() if target.exists() else None
        if actual != expected:
            raise ValueError('Server changed after preflight: ' + name)
    for name, rule in manifest['files'].items():
        data = (job / 'candidate' / name).read_bytes()
        if digest(data) != rule['after']:
            raise ValueError('Staged file changed: ' + name)
    from deploy.media_dependency import install
    install(root,job)
    for name, rule in manifest['files'].items():
        target = root / name
        if not target.exists() or digest(target.read_bytes()) != rule['after']:
            atomic_write(target, (job / 'candidate' / name).read_bytes())
    print('Changed files applied: ' + str(len(records)))


def restore(root, job):
    records = json.loads((job / 'before.json').read_text('utf-8'))
    for name, expected in records.items():
        if expected is not None and hashlib.sha256((job / 'backup' / name).read_bytes()).hexdigest() != expected:
            raise ValueError('Backup checksum mismatch: ' + name)
    for name, expected in records.items():
        target = root / name
        if expected is None:
            target.unlink(missing_ok=True)
        else:
            atomic_write(target, (job / 'backup' / name).read_bytes())
    from deploy.media_dependency import restore as restore_media
    restore_media(root,job)
    print('Previous files restored and verified')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'apply', 'restore'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    parser.add_argument('--job', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.root, args.source, args.job)
    else:
        globals()[args.action](args.root, args.job)
