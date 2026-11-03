"""Verified local SQLite snapshots; restoration checks never overwrite live state."""
import argparse
from contextlib import closing,contextmanager
import datetime as dt
import hashlib
import json
import logging
import math
from pathlib import Path,PurePosixPath
import re
import shutil
import sqlite3
import threading
import time
import uuid

LOG=logging.getLogger('state-backups')
ROOT_FILES=('connections.sqlite3','channel-layouts.sqlite3','queue.sqlite3','repository-files.sqlite3')
NAME=r'db-\d{8}T\d{6}Z-[a-f0-9]{8}'
LOCK=threading.Lock()


def linked(path):return path.is_symlink() or getattr(path,'is_junction',lambda:False)()


def safe(root,name):
    parts=PurePosixPath(name).parts
    allowed=name in ROOT_FILES or len(parts)==3 and parts[0]=='connections' and re.fullmatch('[a-f0-9]{32}',parts[1]) and parts[2]=='queue.sqlite3'
    if not allowed or '\\' in name or PurePosixPath(name).as_posix()!=name:raise ValueError('Unsupported state database path')
    path=root.joinpath(*parts)
    for item in (path,*path.parents):
        if item==root.parent:break
        if linked(item):raise ValueError('Linked state path rejected')
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('State path escapes root')
    return path


def checksum(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()


def integrity(path):
    if any(path.with_name(path.name+suffix).exists() for suffix in ('-wal','-shm','-journal')):raise ValueError('Snapshot must be a standalone database')
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        if db.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('State database integrity check failed')


def binding_ids(path):
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        identities={row[0] for row in db.execute('SELECT id FROM bindings UNION SELECT id FROM retired_bindings')}
    if any(not re.fullmatch('[a-f0-9]{32}',v) for v in identities):raise ValueError('Invalid backup binding scope')
    return identities


@contextmanager
def locked(root):
    root=Path(root).resolve();parent=root/'database-backups'
    if linked(parent):raise ValueError('Linked backup directory rejected')
    parent.mkdir(exist_ok=True)
    lock=parent/'backup.lock'
    if linked(lock):raise ValueError('Linked backup lock rejected')
    with LOCK,lock.open('a+b') as stream:
        stream.seek(0,2)
        if stream.tell()==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        if __import__('os').name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield parent
        finally:
            stream.seek(0)
            if __import__('os').name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)


def remove_managed(parent,folder):
    valid=re.fullmatch(NAME,folder.name) or re.fullmatch(r'\.partial-'+NAME,folder.name) or re.fullmatch(r'\.restore-check-[a-f0-9]{32}',folder.name)
    if not valid or linked(parent) or linked(folder) or folder.parent.resolve()!=parent.resolve():raise ValueError('Unsafe backup removal target')
    pending=[folder]
    while pending:
        current=pending.pop()
        for p in current.iterdir():
            if linked(p) or not p.resolve().is_relative_to(folder.resolve()):raise ValueError('Linked backup contents rejected')
            if p.is_dir():pending.append(p)
    # Both the absolute root and every descendant are checked before recursive deletion.
    shutil.rmtree(folder)


def manifest(folder):
    if linked(folder) or linked(folder/'manifest.json'):raise ValueError('Linked snapshot rejected')
    data=json.loads((folder/'manifest.json').read_text('utf-8'))
    if data.get('format')!=1 or data.get('complete') is not True or not isinstance(data.get('created'),(int,float)) or not math.isfinite(data['created']) or not isinstance(data.get('files'),list) or not data['files']:raise ValueError('Invalid snapshot manifest')
    names=set()
    for row in data['files']:
        path=safe(folder,row['path'])
        if row['path'] in names or not re.fullmatch('[a-f0-9]{64}',row['sha256']):raise ValueError('Invalid snapshot entry')
        names.add(row['path'])
    if 'connections.sqlite3' not in names:raise ValueError('Snapshot registry missing')
    if any(not re.fullmatch('[a-f0-9]{32}',v) for v in data.get('bindings',[])):raise ValueError('Invalid snapshot binding scope')
    return data


def verify(folder):
    folder=Path(folder)
    if linked(folder):raise ValueError('Linked snapshot rejected')
    folder=folder.resolve();data=manifest(folder)
    for row in data['files']:
        path=safe(folder,row['path'])
        if not path.is_file() or path.stat().st_size!=row['size'] or checksum(path)!=row['sha256']:raise ValueError('State snapshot checksum mismatch')
        integrity(path)
    return data


def restore_check(folder,target):
    folder=Path(folder)
    if linked(folder):raise ValueError('Linked snapshot rejected')
    folder=folder.resolve();target=Path(target)
    if target.exists() or linked(target) or any(linked(p) for p in target.parents):raise ValueError('Restore check requires a new, unlinked directory')
    data=verify(folder);target.mkdir(parents=True)
    for row in data['files']:
        source=safe(folder,row['path']);destination=safe(target,row['path']);destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,destination)
        if checksum(destination)!=row['sha256']:raise ValueError('Restored state checksum mismatch')
        integrity(destination.resolve())
    return len(data['files'])


def snapshots(parent):
    result=[]
    for folder in parent.iterdir():
        if re.fullmatch(NAME,folder.name) and folder.is_dir() and not linked(folder):
            try:result.append((manifest(folder)['created'],folder))
            except (ValueError,OSError,KeyError):LOG.warning('Unrecognized backup retained for inspection')
    return sorted(result,reverse=True)


def cleanup_partials(parent):
    for folder in parent.iterdir():
        if re.fullmatch(r'\.partial-'+NAME,folder.name) or re.fullmatch(r'\.restore-check-[a-f0-9]{32}',folder.name):remove_managed(parent,folder)


def expire(root,identities):
    if not (Path(root)/'database-backups').exists():return
    with locked(root) as parent:
        cleanup_partials(parent)
        for _,folder in snapshots(parent):
            if set(manifest(folder)['bindings'])&set(identities):remove_managed(parent,folder)


def purge_expired(root):
    root=Path(root).resolve();registry=safe(root,'connections.sqlite3')
    with closing(sqlite3.connect(registry.as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='backup_expiry'").fetchone():return
        identities={r[0] for r in db.execute('SELECT binding FROM backup_expiry')}
    if not identities:return
    expire(root,identities)
    with closing(sqlite3.connect(registry,timeout=5)) as db:
        with db:db.executemany('DELETE FROM backup_expiry WHERE binding=?',[(identity,) for identity in identities])


def latest(root):
    parent=Path(root).resolve()/'database-backups'
    if not parent.is_dir() or linked(parent):return None
    rows=snapshots(parent)
    if not rows:return None
    data=manifest(rows[0][1])
    return {'created':data['created'],'files':len(data['files'])}


def create(root,reason='daily',stop=None):
    root=Path(root).resolve();started=time.monotonic();now=time.time()
    with locked(root) as parent:
        cleanup_partials(parent)
        name='db-'+dt.datetime.fromtimestamp(now,dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
        pending=parent/('.partial-'+name);pending.mkdir();scratch=parent/('.restore-check-'+uuid.uuid4().hex)
        try:
            files=[]
            def copy_db(relative):
                source=safe(root,relative);destination=safe(pending,relative);destination.parent.mkdir(parents=True,exist_ok=True)
                if any(linked(source.with_name(source.name+suffix)) for suffix in ('-wal','-shm','-journal')):raise ValueError('Linked live database sidecar rejected')
                def progress(*_):
                    if time.monotonic()-started>120 or stop is not None and stop.is_set():raise TimeoutError('State backup interrupted')
                with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True,timeout=5)) as src,closing(sqlite3.connect(destination)) as dst:
                    src.backup(dst,pages=256,progress=progress,sleep=.05)
                    dst.execute('PRAGMA journal_mode=DELETE')
                integrity(destination.resolve())
                files.append({'path':relative,'sha256':checksum(destination),'size':destination.stat().st_size})
            # Registry first: later queue snapshots cannot precede the inbox's fan-out acknowledgement.
            registry=safe(root,'connections.sqlite3')
            candidates=[safe(root,n) for n in ROOT_FILES]+[safe(root,f'connections/{identity}/queue.sqlite3') for identity in binding_ids(registry)]
            for candidate in list(candidates):
                wal=candidate.with_name(candidate.name+'-wal')
                if linked(wal):raise ValueError('Linked live database sidecar rejected')
                if wal.is_file():candidates.append(wal)
            estimate=sum(p.stat().st_size for p in candidates if p.is_file())
            if shutil.disk_usage(root).free<max(256*1024*1024,estimate*3):raise OSError('Insufficient space for verified state backup')
            copy_db('connections.sqlite3');identities=binding_ids(pending/'connections.sqlite3')
            for relative in ROOT_FILES[1:]:
                if safe(root,relative).is_file():copy_db(relative)
            for identity in sorted(identities):
                relative=f'connections/{identity}/queue.sqlite3'
                if safe(root,relative).is_file():copy_db(relative)
            if not identities<=binding_ids(registry):raise RuntimeError('Connections changed during backup; retry later')
            data={'format':1,'complete':True,'created':now,'reason':reason,'bindings':sorted(identities),'files':files}
            (pending/'manifest.json').write_text(json.dumps(data,indent=2)+'\n','utf-8')
            restore_check(pending,scratch);remove_managed(parent,scratch)
            destination=parent/name;pending.rename(destination)
            existing=[entry for entry in snapshots(parent) if entry[1]!=destination]
            for index,(created,folder) in enumerate(existing,1):
                if index>=14 or created<now-7*86400:remove_managed(parent,folder)
            LOG.info('Verified state backup files=%s reason=%s elapsed=%.2fs',len(files),reason,time.monotonic()-started)
            return destination
        finally:
            for folder in (scratch,pending):
                if folder.exists():remove_managed(parent,folder)


class StateBackups:
    def __init__(self,root):self.root=Path(root)
    def tick(self,stop):
        purge_expired(self.root)
        with locked(self.root) as parent:
            cleanup_partials(parent);existing=snapshots(parent)
            if existing and time.time()-existing[0][0]<86400:return
        create(self.root,stop=stop)
    def run(self,stop):
        if stop.wait(300):return
        while not stop.is_set():
            try:self.tick(stop)
            except Exception as error:LOG.warning('State backup will retry error=%s',type(error).__name__)
            stop.wait(3600)
    def start(self,stop):
        worker=threading.Thread(target=self.run,args=(stop,),daemon=True,name='state-backups');worker.start();return worker


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['create','verify','restore-check']);parser.add_argument('--root',type=Path);parser.add_argument('--snapshot',type=Path);parser.add_argument('--target',type=Path);parser.add_argument('--reason',default='manual');args=parser.parse_args()
    if args.action=='create':print('STATE_BACKUP_VERIFIED:',create(args.root,args.reason))
    elif args.action=='verify':print('STATE_BACKUP_VERIFIED files=',len(verify(args.snapshot)['files']))
    else:print('STATE_RESTORE_CHECK_PASSED files=',restore_check(args.snapshot,args.target))
