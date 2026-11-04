"""SQLite-compatible access to an explicitly provisioned SQLCipher state store."""
from contextlib import closing
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3 as plain
import sys
from urllib.parse import unquote, urlsplit

POLICY = 'state-encryption.json'
KEY_FILE = '.state-keys/master.dpapi'
VENDOR = 'vendor-sqlcipher-0.6.4'
Error = plain.Error
OperationalError = plain.OperationalError
IntegrityError = plain.IntegrityError
DatabaseError = plain.DatabaseError
Row = plain.Row


class StorageError(RuntimeError):
    pass


def linked(path):
    return path.is_symlink() or getattr(path, 'is_junction', lambda: False)()


def driver():
    vendor = Path(__file__).resolve().parent / VENDOR
    if vendor.is_dir() and str(vendor) not in sys.path:
        if linked(vendor):
            raise StorageError('Linked database dependency rejected')
        sys.path.insert(0, str(vendor))
    try:
        from sqlcipher3 import dbapi2
    except ImportError:
        raise StorageError('Encrypted state requires the verified SQLCipher dependency') from None
    return dbapi2


def use_vendor(root):
    vendor = Path(root) / VENDOR
    if vendor.is_dir() and str(vendor) not in sys.path:
        if linked(vendor): raise StorageError('Linked database dependency rejected')
        sys.path.insert(0, str(vendor))


def database_path(value, uri=False):
    if str(value) == ':memory:':
        return None
    if uri:
        parsed = urlsplit(str(value))
        if parsed.scheme != 'file' or parsed.netloc:
            raise StorageError('Unsupported database URI')
        name = unquote(parsed.path)
        if sys.platform == 'win32' and len(name) > 2 and name[0] == '/' and name[2] == ':':
            name = name[1:]
        return Path(name).absolute()
    return Path(value).absolute()


def key_root(path):
    if path is None:
        return None
    for parent in path.parents:
        active = parent / 'state-conversion-active.json'
        # Finalize removes this guard while the healthy receiver is opening DBs.
        # Read once instead of checking existence before a second filesystem call.
        if linked(active):
            raise StorageError('Linked conversion guard rejected')
        try:
            transition = active.read_text('utf-8')
        except FileNotFoundError:
            transition = None
        if transition is not None:
            if linked(active) or json.loads(transition).get('state') != 'awaiting-health':
                raise StorageError('Offline state conversion requires recovery before starting the receiver')
        if (parent / POLICY).exists():
            for part in (path, *path.parents):
                if linked(part):
                    raise StorageError('Linked encrypted state path rejected')
                if part == parent:
                    break
            return parent
    return None


def load_key(root):
    from state_encryption import unprotect
    root = Path(root).absolute()
    use_vendor(root)
    policy_path, key_path = root / POLICY, root / KEY_FILE
    if any(linked(p) for p in (root, policy_path, key_path.parent, key_path)):
        raise StorageError('Linked encryption key rejected')
    try:
        policy = json.loads(policy_path.read_text('utf-8'))
        if policy.get('format') != 1 or policy.get('cipher') != 'sqlcipher4' or policy.get('state') != 'ready':
            raise ValueError()
        key = unprotect(key_path.read_bytes())
        if len(key) != 32 or not hmac.compare_digest(hashlib.sha256(key).hexdigest(), policy['key_id']):
            raise ValueError()
        return key
    except Exception:
        raise StorageError('State encryption key is missing, unavailable, or invalid; state was not replaced') from None


def raw_connect(database, key=None, **kwargs):
    module = driver()
    db = module.connect(str(database), **kwargs)
    try:
        if not db.execute('PRAGMA cipher_version').fetchone():
            raise StorageError('Database driver has no encryption support')
        if key is not None:
            db.execute('PRAGMA key = "x\'' + key.hex() + '\'"')
            db.execute('PRAGMA cipher_memory_security=ON')
        db.execute('PRAGMA temp_store=MEMORY')
        db.execute('PRAGMA secure_delete=ON')
        db.execute('SELECT count(*) FROM sqlite_master').fetchone()
        return db
    except Exception:
        db.close()
        raise StorageError('Encrypted database could not be opened; state was not replaced') from None


def translated(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except driver().Error as error:
        kind = getattr(plain, type(error).__name__, plain.DatabaseError)
        raise kind(str(error)) from None


class Cursor:
    def __init__(self, value): self.value = value
    def __getattr__(self, name): return getattr(self.value, name)
    def __iter__(self): return self
    def __next__(self): return translated(next, self.value)
    def execute(self, *args, **kwargs): translated(self.value.execute, *args, **kwargs); return self
    def executemany(self, *args, **kwargs): translated(self.value.executemany, *args, **kwargs); return self
    def executescript(self, *args, **kwargs): translated(self.value.executescript, *args, **kwargs); return self
    def fetchone(self): return translated(self.value.fetchone)
    def fetchall(self): return translated(self.value.fetchall)
    def fetchmany(self, *args): return translated(self.value.fetchmany, *args)


class Connection:
    def __init__(self, value): object.__setattr__(self, 'value', value)
    def __getattr__(self, name): return getattr(self.value, name)
    def __setattr__(self, name, value):
        if name == 'row_factory' and value is plain.Row: value = driver().Row
        setattr(self.value, name, value)
    def __enter__(self): translated(self.value.__enter__); return self
    def __exit__(self, *args): return translated(self.value.__exit__, *args)
    def execute(self, *args, **kwargs): return Cursor(translated(self.value.execute, *args, **kwargs))
    def executemany(self, *args, **kwargs): return Cursor(translated(self.value.executemany, *args, **kwargs))
    def executescript(self, *args, **kwargs): return Cursor(translated(self.value.executescript, *args, **kwargs))
    def cursor(self, *args, **kwargs): return Cursor(translated(self.value.cursor, *args, **kwargs))
    def commit(self): return translated(self.value.commit)
    def rollback(self): return translated(self.value.rollback)
    def backup(self, target, **kwargs): return translated(self.value.backup, getattr(target, 'value', target), **kwargs)


def connect(database, *args, key_from=None, **kwargs):
    path = database_path(database, kwargs.get('uri', False))
    root = Path(key_from) if key_from is not None else key_root(path)
    if root is None:
        return plain.connect(database, *args, **kwargs)
    if args:
        raise StorageError('Use keyword database connection options')
    if path.exists() and path.stat().st_size:
        with path.open('rb') as stream:
            if stream.read(16) == b'SQLite format 3\0':
                raise StorageError('Plain database found in encrypted state; complete offline migration first')
    return Connection(raw_connect(database, load_key(root), **kwargs))
