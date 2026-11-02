import argparse
import ast
from contextlib import contextmanager
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import zipfile


PUBLIC_KEY = '129c6ce9db5362ecd60e05dc1f608fc25a367cf407ed2f97ea9a69ee0de05dd4'
BUSY = ('staged', 'scheduled', 'applying', 'restarting')
MAX_UPLOAD = 24 * 1024 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def allowed_path(name):
    path = PurePosixPath(name)
    if (not isinstance(name, str) or not name or '\\' in name or ':' in name or path.is_absolute()
            or any(part in ('', '.', '..') for part in name.split('/'))):
        return False
    if len(path.parts) == 1:
        return name == 'onboarding-manifest.json' or path.suffix in ('.py', '.ps1', '.md')
    return path.parts[0] in ('web_assets', 'vendor-presence-v1') and not any(p.startswith('.') for p in path.parts)


def verify_package(raw, public_key=PUBLIC_KEY):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    if not 0 < len(raw) <= MAX_UPLOAD:
        raise ValueError('The update ZIP must be smaller than 24 MB.')
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
        entries = archive.infolist()
        if len(entries) > 1200 or sum(item.file_size for item in entries) > 96 * 1024 * 1024:
            raise ValueError('The update package is too large.')
        names = [item.filename for item in entries]
        if len(names) != len(set(names)) or len(names) != len({n.casefold() for n in names}):
            raise ValueError('The update ZIP contains duplicate paths.')
        if any(item.is_dir() or item.flag_bits & 1 or (item.external_attr >> 16) & 0o170000 == 0o120000 for item in entries):
            raise ValueError('Directories, encrypted entries and links are not accepted.')
        if 'release.json' not in names or archive.getinfo('release.json').file_size > 512000:
            raise ValueError('A signed Threadle release is required.')
        signed = json.loads(archive.read('release.json'))
        release = signed['release']
        Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key)).verify(base64.b64decode(signed['signature'], validate=True), canonical(release))
        files = release['files']
        if (release.get('product') != 'Threadle' or release.get('format') != 1 or
                not re.fullmatch(r'[A-Za-z0-9._-]{1,64}', release.get('version', '')) or not isinstance(files, dict)):
            raise ValueError('This package is not a supported Threadle update.')
        if set(names) != set(files) | {'release.json'}:
            raise ValueError('The signed file list does not match this ZIP.')
        payload = {}
        for name, digest in files.items():
            if not allowed_path(name) or not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('This update attempts to change a protected or invalid path.')
            content = archive.read(name)
            if hashlib.sha256(content).hexdigest() != digest:
                raise ValueError('An update file failed checksum verification.')
            if name.endswith('.py') and not name.startswith('vendor-presence-v1/'):
                ast.parse(content, filename=name)
            payload[name] = content
        manifest = json.loads(payload['onboarding-manifest.json'])
        if not isinstance(manifest, dict) or any(name not in files or digest != files[name] for name, digest in manifest.items()):
            raise ValueError('The update manifest does not match the signed release.')
        if 'service.py' not in payload or 'admin_updates.py' not in payload:
            raise ValueError('This update is missing required service modules.')
        return release, payload
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError, SyntaxError, UnicodeError) as error:
        raise ValueError('The update ZIP is invalid or incomplete.') from error
    except ValueError:
        raise
    except Exception as error:
        raise ValueError('Release signature verification failed. Use a signed Threadle update ZIP.') from error


class Updates:
    def __init__(self, root):
        self.root = Path(root)
        self.folder = self.root / 'admin-updates'
        self.folder.mkdir(exist_ok=True)
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS updates(id TEXT PRIMARY KEY, version TEXT NOT NULL, '
                       'digest TEXT NOT NULL, actor TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL, '
                       'created REAL NOT NULL, changed REAL NOT NULL)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.folder / 'updates.sqlite3', timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def job(self, identity):
        if not re.fullmatch('[a-f0-9]{32}', identity):
            raise ValueError('Invalid update identity.')
        return self.folder / identity

    def target(self, name):
        if not allowed_path(name):
            raise ValueError('Invalid update path.')
        target = self.root / name
        if not target.resolve().is_relative_to(self.root.resolve()):
            raise ValueError('An update path points outside the service folder.')
        return target

    def latest(self):
        with self.db() as db:
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM updates ORDER BY created DESC LIMIT 1').fetchone()
        return dict(row) if row else None

    def status(self, identity, state, detail=''):
        with self.db() as db:
            db.execute('UPDATE updates SET state=?,detail=?,changed=? WHERE id=?', (state, detail[:500], time.time(), identity))

    def stage(self, raw, actor):
        release, payload = verify_package(raw)
        identity = uuid.uuid4().hex
        digest = hashlib.sha256(raw).hexdigest()
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM updates WHERE state IN ('staged','scheduled','applying','restarting')").fetchone():
                raise ValueError('Another update is already pending or running.')
            if db.execute("SELECT 1 FROM updates WHERE digest=? AND state='success'", (digest,)).fetchone():
                raise ValueError('This release has already been installed.')
            db.execute('INSERT INTO updates VALUES(?,?,?,?,?,?,?,?)',
                       (identity, release['version'], digest, actor, 'staged', '', time.time(), time.time()))
        try:
            job = self.job(identity)
            job.mkdir()
            (job / 'package.zip').write_bytes(raw)
            shutil.copyfile(Path(__file__), job / 'driver.py')
        except Exception:
            self.status(identity, 'failed', 'Staging failed; the running service was not changed.')
            raise
        return identity, release

    def launch(self, identity):
        if os.name != 'nt':
            raise ValueError('Web updates require the Windows service installation.')
        with self.db() as db:
            changed = db.execute("UPDATE updates SET state='scheduled',changed=? WHERE id=? AND state='staged'",
                                 (time.time(),identity)).rowcount
            if changed != 1:
                raise ValueError('This update is no longer waiting for confirmation.')
        runner = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        try:
            result = subprocess.run([str(runner), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                     '-File', str(self.root/'start-admin-update.ps1'), '-JobId', identity],
                                    capture_output=True, timeout=30, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except (OSError, subprocess.TimeoutExpired):
            self.status(identity,'failed','Update task startup could not be confirmed. Check the Windows update task before retrying.')
            raise ValueError('Update task startup could not be confirmed.') from None
        if result.returncode:
            self.status(identity,'failed','The independent update task could not be started. The running service was not changed.')
            raise ValueError('The independent update task could not be started.')

    def cancel(self, identity):
        with self.db() as db:
            if db.execute("UPDATE updates SET state='cancelled',changed=? WHERE id=? AND state='staged'", (time.time(),identity)).rowcount != 1:
                raise ValueError('Only an unstarted update can be cancelled.')

    def apply(self, identity):
        job = self.job(identity)
        release, payload = verify_package((job/'package.zip').read_bytes())
        changed = {name:content for name,content in payload.items()
                   if not self.target(name).is_file() or hashlib.sha256(self.target(name).read_bytes()).hexdigest()!=hashlib.sha256(content).hexdigest()}
        if shutil.disk_usage(self.root).free < sum(len(value) for value in changed.values()) * 3 + 64*1024*1024:
            raise ValueError('There is not enough free disk space for an update and backup.')
        previous = {name:self.target(name).is_file() for name in changed}
        hashes = {}
        for name, existed in previous.items():
            if existed:
                target = job/'backup'/name
                target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(self.target(name),target)
                hashes[name]=hashlib.sha256(target.read_bytes()).hexdigest()
        (job/'previous-hashes.json').write_text(json.dumps(hashes),encoding='utf-8')
        temporary = job/'previous.json.new'
        temporary.write_text(json.dumps(previous),encoding='utf-8')
        temporary.replace(job/'previous.json')
        self.status(identity,'applying','Replacing changed files: '+str(len(changed)))
        for index,(name,content) in enumerate(changed.items(),1):
            self.status(identity,'applying',str(index)+'/'+str(len(changed))+' '+name)
            target = self.target(name)
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary = target.with_name(target.name+'.update-'+identity)
            temporary.write_bytes(content)
            temporary.replace(target)
        for name,content in changed.items():
            if hashlib.sha256(self.target(name).read_bytes()).hexdigest()!=hashlib.sha256(content).hexdigest():
                raise ValueError('Installed file verification failed: '+name)
        self.status(identity,'restarting','Application files verified; checking Receiver startup.')

    def restore(self, identity):
        job = self.job(identity)
        path = job/'previous.json'
        if not path.exists():
            raise ValueError('No completed application backup is available.')
        previous = json.loads(path.read_text(encoding='utf-8'))
        hashes_path = job/'previous-hashes.json'
        hashes = json.loads(hashes_path.read_text(encoding='utf-8')) if hashes_path.is_file() else {}
        expected={}
        for name,existed in previous.items():
            target=self.target(name)
            backup=job/'backup'/name
            if existed:
                if not backup.is_file():
                    raise ValueError('Rollback backup is missing: '+name)
                digest=hashlib.sha256(backup.read_bytes()).hexdigest()
                if name in hashes and digest!=hashes[name]:
                    raise ValueError('Rollback backup checksum failed: '+name)
                expected[name]=digest
        for name,existed in previous.items():
            target=self.target(name)
            backup=job/'backup'/name
            if existed:
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected[name]:
                    target.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(backup,target)
            else:
                target.unlink(missing_ok=True)
        for name,existed in previous.items():
            target=self.target(name)
            if existed:
                if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest()!=expected[name]:
                    raise ValueError('Rollback verification failed: '+name)
            elif target.exists():
                raise ValueError('Rollback added file remains: '+name)
        self.status(identity,'rolled-back','Previous application file hashes verified; checking Receiver startup.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--job',required=True)
    parser.add_argument('--detail',default='')
    parser.add_argument('action',choices=['apply','restore','scheduled','success','failed','needs-run'])
    args = parser.parse_args()
    for folder in ('vendor-public','vendor','vendor-presence-v1'):
        sys.path.insert(0,str(args.root/folder))
    updates = Updates(args.root)
    if args.action == 'needs-run':
        with updates.db() as db:
            row = db.execute('SELECT state FROM updates WHERE id=?',(args.job,)).fetchone()
        raise SystemExit(0 if row and row[0] in BUSY else 3)
    if args.action in ('apply','restore'):
        getattr(updates,args.action)(args.job)
    else:
        updates.status(args.job,args.action,args.detail)


if __name__ == '__main__':
    main()
