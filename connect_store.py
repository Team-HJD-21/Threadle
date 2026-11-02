import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass
class Session:
    key: str
    expires: float
    data: dict


class Sessions:
    def __init__(self, path, encryption_key, clock=time.time):
        from cryptography.fernet import Fernet
        self.path, self.clock = path, clock
        self.cipher = Fernet(encryption_key.encode())
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, expires REAL NOT NULL, data BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS states(id TEXT PRIMARY KEY, session TEXT NOT NULL,
                    provider TEXT NOT NULL, expires REAL NOT NULL, verifier BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY, interaction TEXT NOT NULL UNIQUE,
                    expires REAL NOT NULL, data BLOB);
            ''')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    def encode(self, data):
        return self.cipher.encrypt(json.dumps(data, ensure_ascii=False).encode())

    def get(self, key):
        if not key or len(key) > 128:
            return None
        with self.db() as db:
            row = db.execute('SELECT expires,data FROM sessions WHERE id=? AND expires>?',
                             (self.digest(key), self.clock())).fetchone()
        return Session(key, row[0], json.loads(self.cipher.decrypt(row[1]))) if row else None

    def create(self, data=None):
        payload = dict(data or {})
        payload['csrf'] = secrets.token_urlsafe(32)
        session = Session(secrets.token_urlsafe(32), self.clock() + 1800, payload)
        with self.db() as db:
            db.execute('DELETE FROM sessions WHERE expires<=?', (self.clock(),))
            db.execute('DELETE FROM states WHERE expires<=?', (self.clock(),))
            if db.execute('SELECT count(*) FROM sessions').fetchone()[0] >= 10000:
                raise RuntimeError('Session capacity reached')
            db.execute('INSERT INTO sessions VALUES(?,?,?)',
                       (self.digest(session.key), session.expires, self.encode(payload)))
        return session

    def cleanup(self):
        with self.db() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM states WHERE expires<=?', (self.clock(),))
            db.execute('DELETE FROM sessions WHERE expires<=?', (self.clock(),))
            db.execute('UPDATE tickets SET data=NULL WHERE expires<=?', (self.clock(),))
            db.execute('DELETE FROM tickets WHERE expires<=?', (self.clock() - 86400,))
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='command_jobs'").fetchone():
                db.execute('UPDATE command_jobs SET payload=NULL,result=NULL,done=1 WHERE expires<?',(self.clock(),))
                db.execute('DELETE FROM command_jobs WHERE expires<?',(self.clock()-86400,))

    def ticket(self, interaction, user, guild, channel):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT expires,data FROM tickets WHERE interaction=?', (interaction,)).fetchone()
            if row:
                return json.loads(self.cipher.decrypt(row[1]))['code'] if row[0] > self.clock() and row[1] else None
            if db.execute('SELECT count(*) FROM tickets WHERE expires>?', (self.clock(),)).fetchone()[0] >= 10000:
                raise RuntimeError('Connection ticket capacity reached')
            code = secrets.token_urlsafe(32)
            data = {'code': code, 'user': user, 'guild': guild, 'channel': channel, 'expires': self.clock() + 300}
            db.execute('INSERT INTO tickets VALUES(?,?,?,?)',
                       (self.digest(code), interaction, data['expires'], self.encode(data)))
        return code

    def consume_ticket(self, code):
        if not isinstance(code, str) or len(code) > 128:
            raise ValueError('Invalid connection ticket')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM tickets WHERE id=? AND expires>?',
                             (self.digest(code), self.clock())).fetchone()
            if row is None or row[0] is None:
                raise ValueError('Connection ticket expired or already used')
            db.execute('UPDATE tickets SET data=NULL WHERE id=?', (self.digest(code),))
        result = json.loads(self.cipher.decrypt(row[0]))
        result.pop('code')
        return result

    def save(self, session):
        with self.db() as db:
            changed = db.execute('UPDATE sessions SET data=? WHERE id=? AND expires>?',
                                 (self.encode(session.data), self.digest(session.key), self.clock())).rowcount
        if changed != 1:
            raise ValueError('Session expired')

    def remove(self, session):
        with self.db() as db:
            db.execute('DELETE FROM states WHERE session=?', (self.digest(session.key),))
            db.execute('DELETE FROM sessions WHERE id=?', (self.digest(session.key),))

    def rotate(self, session):
        fresh = self.create(session.data)
        self.remove(session)
        return fresh

    def state(self, session, provider):
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        with self.db() as db:
            db.execute('DELETE FROM states WHERE session=? AND provider=?', (self.digest(session.key), provider))
            db.execute('INSERT INTO states VALUES(?,?,?,?,?)',
                       (self.digest(state), self.digest(session.key), provider, self.clock() + 600,
                        self.cipher.encrypt(verifier.encode())))
        return state, verifier

    def consume(self, session, provider, state):
        if not state or len(state) > 128:
            raise ValueError('Invalid OAuth state')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT verifier FROM states WHERE id=? AND session=? AND provider=? AND expires>?',
                             (self.digest(state), self.digest(session.key), provider, self.clock())).fetchone()
            if row is None:
                raise ValueError('Invalid or expired OAuth state')
            db.execute('DELETE FROM states WHERE id=?', (self.digest(state),))
        return self.cipher.decrypt(row[0]).decode()

    @staticmethod
    def valid_csrf(session, value):
        return isinstance(value, str) and hmac.compare_digest(session.data.get('csrf', ''), value)
