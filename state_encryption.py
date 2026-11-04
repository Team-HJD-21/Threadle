"""Offline, verified SQLCipher conversion with a separately protected machine key."""
import argparse
import base64
from contextlib import closing
import ctypes
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid

import state_db

LIVE = ('connections.sqlite3', 'channel-layouts.sqlite3', 'repository-files.sqlite3',
        'queue.sqlite3', 'connect.sqlite3', 'admin-updates/updates.sqlite3')


def crypt(data, decrypt=False):
    if os.name != 'nt':
        raise state_db.StorageError('State key protection requires Windows DPAPI')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    operation = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    flags = 1 if decrypt else 5  # No UI; new keys are bound to this Windows machine.
    if not operation(ctypes.byref(source), None, None, None, None, flags, ctypes.byref(output)):
        raise state_db.StorageError('Windows could not protect or unlock the state key')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        free = ctypes.windll.kernel32.LocalFree
        free.argtypes = [ctypes.c_void_p]
        free.restype = ctypes.c_void_p
        free(output.data)


def protect(data): return crypt(data)
def unprotect(data): return crypt(data, True)


def private_directory(path):
    if state_db.linked(path) or any(state_db.linked(p) for p in path.parents):
        raise state_db.StorageError('Linked key directory rejected')
    path.mkdir(parents=True, exist_ok=True)
    if os.name == 'nt':
        # Preserve the deploying operator as well as the SYSTEM receiver and administrators.
        who = subprocess.run(['whoami.exe', '/user', '/fo', 'csv', '/nh'], capture_output=True, check=True, text=True)
        sid = re.search(r'S-1-5-[0-9-]+', who.stdout)
        if sid is None: raise state_db.StorageError('Could not identify the key owner')
        principals = {'S-1-5-18', 'S-1-5-32-544', sid[0]}
        sddl = 'D:P' + ''.join('(A;OICI;FA;;;' + value + ')' for value in sorted(principals))
        encoded_path = base64.b64encode(str(path).encode('utf-8')).decode('ascii')
        script = ("$ErrorActionPreference='Stop'; $p=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + encoded_path + "')); "
                  "$a=New-Object Security.AccessControl.DirectorySecurity; $a.SetSecurityDescriptorSddlForm('" + sddl + "',[Security.AccessControl.AccessControlSections]::Access); [IO.Directory]::SetAccessControl($p,$a)")
        subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand',
                        base64.b64encode(script.encode('utf-16le')).decode('ascii')], check=True, capture_output=True)
    else:
        path.chmod(0o700)


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.new')
    if state_db.linked(path) or state_db.linked(temporary):
        raise state_db.StorageError('Linked encryption state rejected')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, sort_keys=True, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, path)


def get_key(root):
    state_db.use_vendor(root)
    folder = root / '.state-keys'
    private_directory(folder)
    path = root / state_db.KEY_FILE
    if state_db.linked(path): raise state_db.StorageError('Linked key rejected')
    if path.exists():
        try: key = unprotect(path.read_bytes())
        except Exception: raise state_db.StorageError('Existing state key is unavailable; no replacement key was created') from None
        if len(key) != 32: raise state_db.StorageError('Invalid state key; no replacement key was created')
    else:
        if (root/state_db.POLICY).exists() or any((root/name).is_file() and (root/name).stat().st_size and not is_plain(root/name) for name in LIVE):
            raise state_db.StorageError('Existing encrypted state has no key; no replacement key was created')
        key = os.urandom(32)
        encrypted = protect(key)
        if unprotect(encrypted) != key: raise state_db.StorageError('State key round-trip failed')
        temporary = path.with_name('master.dpapi.new')
        if temporary.exists() or state_db.linked(temporary): raise state_db.StorageError('Unfinished state key requires inspection')
        with temporary.open('xb') as stream:
            stream.write(encrypted); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    return key


def safe_path(root, name):
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(root.resolve()):
        raise state_db.StorageError('State conversion path rejected')
    if any(state_db.linked(p) for p in (path, *path.parents)):
        raise state_db.StorageError('Linked state conversion path rejected')
    return path


def managed_database(name):
    if name in LIVE: return True
    if re.fullmatch(r'connections/[a-f0-9]{32}/queue\.sqlite3',name): return True
    parts = name.split('/')
    if len(parts) >= 3 and parts[0] == 'database-backups' and re.fullmatch(r'db-\d{8}T\d{6}Z-[a-f0-9]{8}',parts[1]):
        from state_backups import ROOT_FILES
        relative = '/'.join(parts[2:])
        return relative in ROOT_FILES or bool(re.fullmatch(r'connections/[a-f0-9]{32}/queue\.sqlite3',relative))
    return False


def is_plain(path):
    with path.open('rb') as stream: return stream.read(16) == b'SQLite format 3\0'


def logical_hash(db):
    digest = hashlib.sha256()
    schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name').fetchall()
    digest.update(repr(schema).encode('utf-8'))
    for kind, name, _, sql in schema:
        if kind != 'table': continue
        quoted = '"' + name.replace('"', '""') + '"'
        order = '' if 'WITHOUT ROWID' in sql.upper() else ' ORDER BY _rowid_'
        digest.update(name.encode('utf-8'))
        for row in db.execute('SELECT * FROM ' + quoted + order):
            digest.update(repr(row).encode('utf-8')); digest.update(b'\n')
    for pragma in ('user_version', 'application_id'):
        digest.update(str(db.execute('PRAGMA ' + pragma).fetchone()).encode())
    return digest.hexdigest()


def export(source, destination, source_key, destination_key):
    if destination.exists() or state_db.linked(destination): raise state_db.StorageError('Conversion destination already exists')
    with closing(state_db.raw_connect(source, source_key, timeout=15)) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]: raise state_db.StorageError('Source state integrity check failed')
        if db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0]: raise state_db.StorageError('State is busy; stop the receiver before conversion')
        db.execute('PRAGMA journal_mode=DELETE')
        before = logical_hash(db)
        version = db.execute('PRAGMA user_version').fetchone()[0]
        app_id = db.execute('PRAGMA application_id').fetchone()[0]
        key = "x'" + destination_key.hex() + "'" if destination_key is not None else ''
        db.execute('ATTACH DATABASE ? AS converted KEY ?', (str(destination), key))
        db.execute("SELECT sqlcipher_export('converted')")
        db.execute('PRAGMA converted.user_version=' + str(version))
        db.execute('PRAGMA converted.application_id=' + str(app_id))
        db.execute('DETACH DATABASE converted')
    with closing(state_db.raw_connect(destination, destination_key, timeout=15)) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)] or logical_hash(db) != before:
            raise state_db.StorageError('Converted state does not match the original')
        if destination_key is not None and db.execute('PRAGMA cipher_integrity_check').fetchall():
            raise state_db.StorageError('Converted state authentication check failed')
    with destination.open('r+b') as stream: os.fsync(stream.fileno())


def databases(root):
    from state_backups import manifest, snapshots, integrity
    result = [name for name in LIVE if safe_path(root, name).is_file()]
    folder = root / 'connections'
    if folder.exists():
        for item in folder.iterdir():
            if re.fullmatch('[a-f0-9]{32}', item.name):
                name = 'connections/' + item.name + '/queue.sqlite3'
                if safe_path(root, name).is_file(): result.append(name)
    manifests = []
    parent = root / 'database-backups'
    if parent.exists():
        safe_path(root, 'database-backups')
        for _, backup in snapshots(parent):
            data = manifest(backup)
            manifests.append(backup.relative_to(root).as_posix() + '/manifest.json')
            for row in data['files']:
                name = (backup.relative_to(root) / row['path']).as_posix()
                path = safe_path(root, name)
                from state_backups import checksum
                if path.stat().st_size != row['size'] or checksum(path) != row['sha256']:
                    raise state_db.StorageError('Existing state backup failed verification; no state converted')
                integrity(path)
                result.append(name)
    return sorted(set(result)), manifests


def refresh_manifests(root, names, encrypted, key):
    from state_backups import checksum, manifest
    for name in names:
        path = safe_path(root, name)
        data = manifest(path.parent)
        for row in data['files']:
            database = path.parent / row['path']
            row.update(size=database.stat().st_size, sha256=checksum(database))
        data['encrypted'] = encrypted
        if encrypted: data['key_id'] = hashlib.sha256(key).hexdigest()
        else: data.pop('key_id', None)
        atomic_json(path, data)


def migrate(root, job):
    from state_backups import locked, cleanup_partials
    with locked(root) as parent:
        cleanup_partials(parent)
        return _migrate(root,job)


def _migrate(root, job):
    root, job = Path(root).resolve(), Path(job).resolve()
    safe_path(root, job.relative_to(root).as_posix())
    if len(job.relative_to(root).parts) != 2 or job.parent != root/'cd-deployments':
        raise state_db.StorageError('Conversion requires an isolated CD job directory')
    journal = job / 'state-conversion.json'
    if journal.exists(): raise state_db.StorageError('Conversion journal already exists; recover it before retrying')
    active = root / 'state-conversion-active.json'
    if active.exists(): raise state_db.StorageError('A previous conversion requires recovery')
    names, manifests = databases(root)
    key = get_key(root)
    previous = json.loads((root / state_db.POLICY).read_text('utf-8')) if (root / state_db.POLICY).exists() else None
    if previous is not None and state_db.load_key(root) != key: raise state_db.StorageError('State key changed')
    private = root / '.state-keys' / ('conversion-' + uuid.uuid4().hex)
    private_directory(private)
    record = {'format': 1, 'root': str(root), 'private': private.relative_to(root).as_posix(),
              'previous': previous, 'manifests': manifests, 'files': [], 'state': 'preparing'}
    job.mkdir(parents=True, exist_ok=True)
    atomic_json(journal, record)
    atomic_json(active, {'state': 'preparing', 'job': job.relative_to(root).as_posix()})
    try:
        required = sum(safe_path(root, name).stat().st_size for name in names)
        if shutil.disk_usage(root).free < max(256*1024*1024, required*3): raise OSError('Insufficient space for verified state conversion')
        for index, name in enumerate(names):
            path = safe_path(root, name)
            if not path.stat().st_size: continue
            if not is_plain(path):
                with closing(state_db.raw_connect(path, key)) as db:
                    if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]: raise state_db.StorageError('Existing encrypted state failed verification')
                continue
            staged, original = private / (str(index) + '.encrypted'), private / (str(index) + '.original')
            export(path, staged, None, key)
            record['files'].append({'path': name, 'staged': staged.name, 'original': original.name})
            atomic_json(journal, record)
        record['state'] = 'replacing'; atomic_json(journal, record)
        for row in record['files']:
            path = safe_path(root, row['path'])
            os.replace(path, private / row['original'])
            os.replace(private / row['staged'], path)
        refresh_manifests(root, manifests, True, key)
        atomic_json(root / state_db.POLICY, {'format': 1, 'cipher': 'sqlcipher4', 'state': 'ready', 'key_id': hashlib.sha256(key).hexdigest()})
        record['state'] = 'awaiting-health'; atomic_json(journal, record)
        atomic_json(active, {'state': 'awaiting-health', 'job': job.relative_to(root).as_posix()})
        return len(record['files'])
    except Exception:
        abort(root, journal)
        raise


def read_journal(root, journal):
    safe_path(root,journal.relative_to(root).as_posix())
    record = json.loads(journal.read_text('utf-8'))
    if record.get('format') != 1 or record.get('root') != str(root): raise state_db.StorageError('Invalid state conversion journal')
    private = safe_path(root, record['private'])
    if private.parent != root / '.state-keys' or not re.fullmatch('conversion-[a-f0-9]{32}', private.name):
        raise state_db.StorageError('Invalid conversion recovery directory')
    for row in record['files']:
        safe_path(root, row['path'])
        if not managed_database(row['path']) or not re.fullmatch(r'\d+\.original', row['original']) or not re.fullmatch(r'\d+\.encrypted', row['staged']):
            raise state_db.StorageError('Invalid conversion recovery file')
    for name in record['manifests']:
        path = safe_path(root, name)
        if path.name != 'manifest.json' or path.parent.parent != root / 'database-backups':
            raise state_db.StorageError('Invalid conversion backup manifest')
    for name in record.get('rollback_files',[]):
        if not managed_database(name): raise state_db.StorageError('Invalid rollback database path')
    return record, private


def remove_private(root, private):
    safe_path(root, private.relative_to(root).as_posix())
    if any(state_db.linked(p) for p in private.rglob('*')): raise state_db.StorageError('Linked conversion material rejected')
    shutil.rmtree(private)


def abort(root, journal):
    record, private = read_journal(root, journal)
    for row in record['files']:
        original = private / row['original']
        if original.exists(): os.replace(original, safe_path(root, row['path']))
    refresh_manifests(root, record['manifests'], record['previous'] is not None, get_key(root))
    if record['previous'] is None: (root / state_db.POLICY).unlink(missing_ok=True)
    else: atomic_json(root / state_db.POLICY, record['previous'])
    remove_private(root, private)
    record['state'] = 'aborted'; atomic_json(journal, record)
    (root / 'state-conversion-active.json').unlink(missing_ok=True)


def rollback(root, job):
    from state_backups import locked
    with locked(root): return _rollback(root,job)


def _rollback(root, job):
    root, job = Path(root).resolve(), Path(job).resolve()
    journal = job / 'state-conversion.json'
    if not journal.exists(): return
    record, private = read_journal(root, journal)
    if record['state'] in ('aborted', 'rolled-back'): return
    if record['state'] in ('preparing', 'replacing'): return abort(root, journal)
    if record['previous'] is None:
        if not private.exists(): private_directory(private)
        key = get_key(root)
        if record['state'] != 'rolling-back':
            names, manifests = databases(root)
            record.update(state='rolling-back', rollback_files=names, rollback_manifests=manifests)
            atomic_json(journal, record)
        names = record['rollback_files']
        atomic_json(root / 'state-conversion-active.json', {'state': 'rolling-back', 'job': job.relative_to(root).as_posix()})
        for index, name in enumerate(names):
            path = safe_path(root, name)
            if not path.stat().st_size or is_plain(path): continue
            destination = private / (str(index) + '.rollback')
            if destination.exists(): destination.unlink()
            export(path, destination, key, None)
            os.replace(destination, path)
        refresh_manifests(root, record['rollback_manifests'], False, key)
        (root / state_db.POLICY).unlink(missing_ok=True)
    # A completed deployment has already removed its private staging directory.
    # Existing encrypted state needs no export; preserve its live data when a
    # post-finalize health check asks CD to restore the previous application.
    if record['state'] != 'complete' or private.exists() or state_db.linked(private):
        remove_private(root, private)
    record['state'] = 'rolled-back'; atomic_json(journal, record)
    (root / 'state-conversion-active.json').unlink(missing_ok=True)


def finalize(root, job):
    root, job = Path(root).resolve(), Path(job).resolve()
    record, private = read_journal(root, job / 'state-conversion.json')
    if record['state'] == 'complete': return
    if record['state'] != 'awaiting-health': raise state_db.StorageError('State conversion is not ready to finalize')
    if private.exists(): remove_private(root, private)
    record['state'] = 'complete'; atomic_json(job / 'state-conversion.json', record)
    (root / 'state-conversion-active.json').unlink(missing_ok=True)


def export_recovery(root, target, password):
    """Create a password-encrypted recovery key outside the runtime/DB backup tree."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    root, target = Path(root).resolve(), Path(target).absolute()
    if target.resolve().is_relative_to(root) or any(state_db.linked(p) for p in (target,*target.parents)):
        raise state_db.StorageError('Keep the recovery key outside the runtime and database backups')
    if len(password) < 16: raise ValueError('Use a recovery passphrase of at least 16 characters')
    key = state_db.load_key(root)
    salt, nonce = os.urandom(16), os.urandom(12)
    wrapping = Scrypt(salt=salt,length=32,n=2**17,r=8,p=1).derive(password.encode('utf-8'))
    identity = hashlib.sha256(key).hexdigest()
    encrypted = AESGCM(wrapping).encrypt(nonce,key,('Threadle recovery v1:'+identity).encode('ascii'))
    data = {'format':1,'key_id':identity,'salt':salt.hex(),'nonce':nonce.hex(),'encrypted':encrypted.hex()}
    with target.open('x', encoding='utf-8') as stream:
        json.dump(data,stream); stream.write('\n'); stream.flush(); os.fsync(stream.fileno())


def import_recovery(root, source, password):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    root, source = Path(root).resolve(), Path(source)
    if source.stat().st_size > 4096 or state_db.linked(source): raise state_db.StorageError('Invalid recovery key file')
    try:
        data = json.loads(source.read_text('utf-8'))
        if data['format'] != 1 or not re.fullmatch('[a-f0-9]{64}',data['key_id']): raise ValueError()
        salt, nonce = bytes.fromhex(data['salt']), bytes.fromhex(data['nonce'])
        if len(salt) != 16 or len(nonce) != 12: raise ValueError()
        wrapping = Scrypt(salt=salt,length=32,n=2**17,r=8,p=1).derive(password.encode('utf-8'))
        key = AESGCM(wrapping).decrypt(nonce,bytes.fromhex(data['encrypted']),('Threadle recovery v1:'+data['key_id']).encode('ascii'))
        if len(key) != 32 or hashlib.sha256(key).hexdigest() != data['key_id']: raise ValueError()
    except Exception:
        raise state_db.StorageError('Recovery key or passphrase could not be verified') from None
    path = root/state_db.KEY_FILE
    if path.exists(): raise state_db.StorageError('An existing protected key will not be replaced')
    state_db.use_vendor(root)
    registry = safe_path(root,'connections.sqlite3')
    if not registry.is_file() or is_plain(registry): raise state_db.StorageError('Restore the encrypted state databases before importing their key')
    with closing(state_db.raw_connect(registry,key)) as db:
        if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]: raise state_db.StorageError('Recovered key does not match the state database')
    private_directory(path.parent)
    protected = protect(key)
    if unprotect(protected) != key: raise state_db.StorageError('Recovered key protection failed')
    with path.open('xb') as stream:
        stream.write(protected);stream.flush();os.fsync(stream.fileno())
    atomic_json(root/state_db.POLICY, {'format':1,'cipher':'sqlcipher4','state':'ready','key_id':data['key_id']})


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['migrate', 'rollback', 'finalize', 'export-recovery', 'import-recovery'])
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--job', type=Path)
    parser.add_argument('--recovery-file', type=Path)
    parser.add_argument('--offline', action='store_true', help='Receiver intake/delivery has been stopped')
    args = parser.parse_args()
    try:
        if args.action in ('migrate','rollback','import-recovery'):
            if not args.offline: parser.error('Stop the receiver and pass --offline before changing state storage')
            import socket
            try:
                with socket.create_connection(('127.0.0.1',8787),timeout=.5): pass
            except OSError: pass
            else: raise state_db.StorageError('Receiver port is still listening; no state converted')
        if args.action in ('export-recovery','import-recovery'):
            import getpass
            if not args.recovery_file: parser.error('--recovery-file is required')
            password = getpass.getpass('Recovery passphrase (not stored): ')
            if args.action == 'export-recovery' and password != getpass.getpass('Repeat passphrase: '):
                raise ValueError('Passphrases do not match')
            operation = export_recovery if args.action == 'export-recovery' else import_recovery
            result = operation(args.root,args.recovery_file,password)
        else:
            if not args.job: parser.error('--job is required')
            result = globals()[args.action](args.root, args.job)
        print('STATE_ENCRYPTION_' + args.action.upper() + '_VERIFIED', result if result is not None else '')
    except Exception as error:
        print('STATE_ENCRYPTION_FAILED type=' + type(error).__name__ + (' detail=' + str(error) if isinstance(error,state_db.StorageError) else ''))
        raise SystemExit(1)
