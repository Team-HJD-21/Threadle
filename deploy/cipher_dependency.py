"""Pin and verify the isolated Windows SQLCipher wheel before receiver downtime."""
import hashlib
import io
import json
import os
from pathlib import PurePosixPath
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from deploy.media_dependency import hashes

NAME = 'vendor-sqlcipher-0.6.4'


def unpack(raw, settings, target):
    if len(raw) != settings['size'] or hashlib.sha256(raw).hexdigest() != settings['sha256']:
        raise ValueError('SQLCipher wheel checksum mismatch')
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 1000 or sum(e.file_size for e in entries) > 64*1024*1024:
            raise ValueError('Unexpected SQLCipher wheel size')
        seen = set()
        for item in entries:
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or '\\' in item.filename or ':' in item.filename or '..' in path.parts or
                    not path.parts or path.parts[0] not in ('sqlcipher3', 'sqlcipher3.libs', 'sqlcipher3-0.6.4.dist-info') or
                    item.filename.casefold() in seen or item.flag_bits & 1 or (item.external_attr>>16)&0o170000 == 0o120000):
                raise ValueError('Unexpected SQLCipher wheel entry')
            seen.add(item.filename.casefold())
        for item in entries:
            path = target / item.filename
            if item.is_dir(): path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(archive.read(item))


def stage(root, source, job):
    settings = json.loads((source/'deploy/cipher.json').read_text('utf-8'))
    if settings['version'] != '0.6.4' or not settings['url'].startswith('https://files.pythonhosted.org/packages/'):
        raise ValueError('Unsupported database dependency')
    if sys.version_info[:2] != (3,12) or sys.platform != 'win32' or __import__('struct').calcsize('P') != 8:
        raise ValueError('Database wheel requires Windows Python 3.12 x64')
    with urllib.request.urlopen(settings['url'], timeout=30) as response:
        if not response.url.startswith('https://files.pythonhosted.org/packages/'):
            raise ValueError('Unexpected database dependency destination')
        raw = response.read(settings['size']+1)
    candidate = job/NAME
    unpack(raw, settings, candidate)
    script = ("import sys,os; sys.path[:0]=sys.argv[1:3]; import state_db,state_encryption; "
              "from pathlib import Path; p=Path(sys.argv[3])/'cipher-smoke.db'; k=os.urandom(32); "
              "assert state_encryption.unprotect(state_encryption.protect(k))==k; "
              "acl=Path(sys.argv[3])/'key-permission-smoke'; state_encryption.private_directory(acl); acl.rmdir(); "
              "db=state_db.raw_connect(p,k); db.execute('CREATE TABLE smoke(v TEXT)'); "
              "db.execute('INSERT INTO smoke VALUES(?)',('verified private data',)); db.commit(); db.close(); "
              "assert b'verified private data' not in p.read_bytes(); "
              "db=state_db.raw_connect(p,k); assert db.execute('SELECT v FROM smoke').fetchone()[0]=='verified private data'; "
              "assert db.execute('PRAGMA cipher_integrity_check').fetchall()==[]; db.close(); p.unlink(); "
              "print('SQLCipher encryption and Windows key protection verified')")
    subprocess.run([sys.executable,'-B','-I','-c',script,str(candidate),str(job/'candidate'),str(job)],check=True,timeout=30)
    target = root/NAME; expected = hashes(candidate)
    if target.exists() and hashes(target) != expected: raise ValueError('Existing database dependency differs; no files changed')
    (job/'cipher-state.json').write_text(json.dumps({'existing':target.exists(),'files':expected}),'utf-8')


def install(root, job):
    path = job/'cipher-state.json'
    if not path.exists(): return
    state = json.loads(path.read_text('utf-8')); source, target = job/NAME, root/NAME
    if state['existing']:
        if hashes(target) != state['files']: raise ValueError('Installed database dependency changed')
    else:
        if target.exists() or hashes(source) != state['files']: raise ValueError('Database dependency changed after preflight')
        os.rename(source, target)


def restore(root, job):
    path = job/'cipher-state.json'
    if not path.exists(): return
    state = json.loads(path.read_text('utf-8')); target = root/NAME
    if not state['existing'] and target.exists():
        if hashes(target) != state['files'] or target.resolve().parent != root.resolve():
            raise ValueError('Changed database dependency retained for inspection')
        shutil.rmtree(target)
