import json
import re
import sqlite3
import uuid
import time
from contextlib import contextmanager
from dataclasses import dataclass


@dataclass(frozen=True)
class Binding:
    id: str
    tenant: str
    repo_id: int
    repo: str
    installation: int
    auth: str
    config: dict
    legacy_state: bool


class Registry:
    def __init__(self, path):
        self.path = path
        with self.db() as db:
            db.executescript('''
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS installations(id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS bindings(
                    id TEXT PRIMARY KEY, tenant TEXT NOT NULL, repo_id INTEGER NOT NULL,
                    repo TEXT NOT NULL, installation INTEGER NOT NULL, auth TEXT NOT NULL,
                    guild TEXT NOT NULL, config TEXT NOT NULL, legacy_state INTEGER NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1, UNIQUE(repo_id,guild));
                CREATE UNIQUE INDEX IF NOT EXISTS one_legacy ON bindings(auth) WHERE auth='legacy';
                CREATE TABLE IF NOT EXISTS inbox(id TEXT PRIMARY KEY, delivery TEXT NOT NULL,
                    event TEXT NOT NULL, payload TEXT, recipients TEXT NOT NULL, done INTEGER DEFAULT 0);
                CREATE TABLE IF NOT EXISTS connection_audit(id INTEGER PRIMARY KEY, binding TEXT NOT NULL,
                    action TEXT NOT NULL, discord_user TEXT NOT NULL, github_user TEXT NOT NULL,
                    happened TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS retired_bindings(id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admin_paused(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY, actor TEXT NOT NULL,
                    action TEXT NOT NULL, binding TEXT NOT NULL, happened TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE IF NOT EXISTS guild_profiles(guild TEXT PRIMARY KEY, name TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS github_profiles(user TEXT PRIMARY KEY, login TEXT NOT NULL,
                    checked REAL NOT NULL DEFAULT 0);
            ''')
            db.execute('CREATE TABLE IF NOT EXISTS connection_retention(id TEXT PRIMARY KEY, disconnected_at REAL NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS retention_scopes(scope TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS legal_acceptances(binding TEXT NOT NULL, discord_user TEXT NOT NULL, '
                       'github_user TEXT NOT NULL, version TEXT NOT NULL, accepted_at REAL NOT NULL, '
                       'PRIMARY KEY(binding,discord_user,github_user,version))')
            db.execute('INSERT OR IGNORE INTO connection_retention SELECT id,? FROM bindings WHERE enabled=0',(time.time(),))
            db.execute('INSERT OR IGNORE INTO connection_retention SELECT id,? FROM retired_bindings',(time.time(),))
            db.executescript("""
                CREATE TRIGGER IF NOT EXISTS binding_disconnected AFTER UPDATE OF enabled ON bindings
                WHEN NEW.enabled=0 AND OLD.enabled=1 BEGIN
                    INSERT OR REPLACE INTO connection_retention VALUES(NEW.id,(julianday('now')-2440587.5)*86400);
                END;
                CREATE TRIGGER IF NOT EXISTS binding_enabled AFTER UPDATE OF enabled ON bindings
                WHEN NEW.enabled=1 BEGIN DELETE FROM connection_retention WHERE id=NEW.id; END;
            """)

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate(repo_id, repo, installation, auth, config):
        from pr_routing import validate_groups
        from team_notifications import validate_options
        validate_groups(config.get('routing_groups', []))
        validate_options(config.get('team_notifications', {}))
        if config.get('language', 'ko') not in ('ko', 'en'):
            raise ValueError('Invalid notification language')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repo) or int(repo_id) <= 0:
            raise ValueError('Invalid repository identity')
        if auth not in ('legacy', 'app') or (auth == 'app' and installation <= 0):
            raise ValueError('Invalid installation authentication')
        if set(config['channels']) != {'issue', 'pr', 'discussion'}:
            raise ValueError('Issue, PR and Discussion channels are required')
        names = config.get('workflow_names', [])
        if not isinstance(names, list) or not all(isinstance(name, str) and name for name in names):
            raise ValueError('workflow_names must be a list of nonempty names')
        for value in [config['guild_id'], *config['channels'].values(), config.get('alert_channel', '1'), config.get('ci_channel', '1'),
                      *[g['channel'] for g in config.get('routing_groups', [])]]:
            if not re.fullmatch('[0-9]+', str(value)) or int(value) <= 0:
                raise ValueError('Discord IDs must be numeric')

    def configure(self, identity, config, discord_user, github_user, consent=None):
        binding = self.get(identity)
        if binding is None or binding.auth != 'app' or config['guild_id'] != binding.config['guild_id']:
            raise ValueError('연결 정보가 변경됐습니다. 다시 확인해 주세요.')
        self.validate(binding.repo_id, binding.repo, binding.installation, binding.auth, config)
        with self.db() as db:
            changed = db.execute('UPDATE bindings SET config=? WHERE id=? AND enabled=1 AND config=?',
                                 (json.dumps(config), identity, json.dumps(binding.config))).rowcount
            if changed != 1:
                raise ValueError('연결 정보가 변경됐습니다. 다시 확인해 주세요.')
            db.execute('INSERT INTO connection_audit(binding,action,discord_user,github_user) VALUES(?,?,?,?)',
                       (identity, 'configure', discord_user, github_user))
            self.acceptance(db, identity, discord_user, github_user, consent)

    def add(self, tenant, repo_id, repo, installation, auth, config, legacy_state=False):
        self.validate(repo_id, repo, installation, auth, config)
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO installations VALUES(?,1)', (installation,))
            db.execute('INSERT INTO bindings(id,tenant,repo_id,repo,installation,auth,guild,config,legacy_state,enabled) VALUES(?,?,?,?,?,?,?,?,?,1)',
                       (identity, tenant, repo_id, repo, installation, auth, str(config['guild_id']),
                        json.dumps(config), int(legacy_state)))
        return identity

    def connect(self, repo_id, repo, installation, config, discord_user, github_user, consent=None):
        self.validate(repo_id, repo, installation, 'app', config)
        guild = str(config['guild_id'])
        identity = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT * FROM bindings WHERE repo_id=? AND guild=?', (repo_id, guild)).fetchone()
            if row is not None:
                if db.execute('SELECT 1 FROM admin_paused WHERE id=?', (row['id'],)).fetchone():
                    raise ValueError('This connection is paused by the operator.')
                if row['auth'] == 'legacy' and row['enabled']:
                    raise ValueError('기존 운영 연결이 있습니다. 운영자에게 이전을 요청해 주세요.')
                if row['enabled']:
                    if row['installation'] == installation and json.loads(row['config']) == config:
                        self.acceptance(db, row['id'], discord_user, github_user, consent)
                        return row['id']
                    raise ValueError('이미 연결된 저장소입니다. 기존 연결을 해제한 뒤 다시 연결해 주세요.')
                db.execute('INSERT OR REPLACE INTO retired_bindings(id,data) VALUES(?,?)', (row['id'],json.dumps(dict(row))))
                db.execute('DELETE FROM bindings WHERE id=?', (row['id'],))
            db.execute('INSERT INTO installations VALUES(?,1) ON CONFLICT(id) DO UPDATE SET enabled=1', (installation,))
            db.execute('INSERT INTO bindings(id,tenant,repo_id,repo,installation,auth,guild,config,legacy_state,enabled) VALUES(?,?,?,?,?,?,?,?,?,1)',
                       (identity, 'discord:' + guild, repo_id, repo, installation, 'app', guild, json.dumps(config), 0))
            db.execute('INSERT INTO connection_audit(binding,action,discord_user,github_user) VALUES(?,?,?,?)',
                       (identity, 'connected', str(discord_user), str(github_user)))
            self.acceptance(db, identity, discord_user, github_user, consent)
        return identity

    @staticmethod
    def acceptance(db, identity, discord_user, github_user, consent):
        if consent is not None:
            from legal_consent import VERSION
            if consent.get('version') != VERSION or not isinstance(consent.get('accepted_at'), (int, float)):
                raise ValueError('Invalid terms acceptance')
            db.execute('INSERT OR IGNORE INTO legal_acceptances VALUES(?,?,?,?,?)',
                       (identity, str(discord_user), str(github_user), VERSION, consent['accepted_at']))

    def disconnect(self, identity, guild, discord_user):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT auth FROM bindings WHERE id=? AND guild=? AND enabled=1',
                             (identity, str(guild))).fetchone()
            if row is None or row[0] != 'app':
                raise ValueError('관리할 수 있는 연결이 아닙니다.')
            db.execute('UPDATE bindings SET enabled=0 WHERE id=?', (identity,))
            db.execute('INSERT INTO connection_audit(binding,action,discord_user,github_user) VALUES(?,?,?,?)',
                       (identity, 'disconnected', str(discord_user), ''))

    def get(self, identity, include_paused=False):
        with self.db() as db:
            if not include_paused and db.execute('SELECT 1 FROM admin_paused WHERE id=?', (identity,)).fetchone():
                return None
            row = db.execute('SELECT b.id,b.tenant,b.repo_id,b.repo,b.installation,b.auth,b.config,b.legacy_state '
                             'FROM bindings b JOIN installations i ON b.installation=i.id '
                             'WHERE b.id=? AND b.enabled=1 AND i.enabled=1', (identity,)).fetchone()
        return None if row is None else Binding(*row[:6], json.loads(row[6]), bool(row[7]))

    def active(self, include_paused=False):
        with self.db() as db:
            ids = [row[0] for row in db.execute('SELECT id FROM bindings')]
        return [binding for identity in ids if (binding := self.get(identity, include_paused)) is not None]

    def disable(self, identity):
        with self.db() as db:
            db.execute('UPDATE bindings SET enabled=0 WHERE id=?', (identity,))

    def guild_connections(self):
        with self.db() as db:
            result = {}
            for identity, guild in db.execute('SELECT id,guild FROM bindings WHERE enabled=1'):
                result.setdefault(guild, []).append(identity)
        return result

    def save_guild_name(self, guild, name):
        if not str(guild).isdigit() or not isinstance(name, str) or not name.strip():
            return
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO guild_profiles VALUES(?,?)', (str(guild), name[:100]))

    def save_github_login(self, user, login, checked):
        if not str(user).isdigit() or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(login)):
            return
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO github_profiles VALUES(?,?,?)', (str(user), login, checked))

    def remove_guild(self, guild, expected=None):
        guild = str(guild)
        if not re.fullmatch(r'[1-9][0-9]*', guild):
            raise ValueError('Invalid Discord server identity')
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT id FROM bindings WHERE guild=? AND enabled=1', (guild,)).fetchall()
            identities = [identity for (identity,) in rows if expected is None or identity in expected]
            for identity in identities:
                db.execute('UPDATE bindings SET enabled=0 WHERE id=?', (identity,))
                db.execute('DELETE FROM admin_paused WHERE id=?', (identity,))
                db.execute('INSERT INTO connection_audit(binding,action,discord_user,github_user) VALUES(?,?,?,?)',
                           (identity,'discord-guild-removed','system',''))
                db.execute('INSERT INTO admin_audit(actor,action,binding) VALUES(?,?,?)',
                           ('Discord','auto-disconnect',identity))
        return len(identities)

    def rename(self, identity, name):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', name):
            raise ValueError('Invalid canonical repository name')
        with self.db() as db:
            db.execute('UPDATE bindings SET repo=? WHERE id=?', (name, identity))

    def receive(self, mode, delivery, event, payload):
        installation = int(payload.get('installation', {}).get('id', 0))
        if mode == 'app' and installation <= 0:
            return 403
        if mode == 'app' and event in ('installation', 'installation_repositories'):
            with self.db() as db:
                action = payload.get('action')
                if event == 'installation' and action in ('deleted', 'suspend'):
                    db.execute('UPDATE installations SET enabled=0 WHERE id=?', (installation,))
                    if action == 'deleted':
                        db.execute('UPDATE bindings SET enabled=0 WHERE installation=?', (installation,))
                elif event == 'installation' and action == 'unsuspend':
                    db.execute('UPDATE installations SET enabled=1 WHERE id=?', (installation,))
                elif event == 'installation_repositories' and action == 'removed':
                    for repository in payload.get('repositories_removed', []):
                        db.execute('UPDATE bindings SET enabled=0 WHERE installation=? AND repo_id=?',
                                   (installation, int(repository['id'])))
            return 200
        repository = payload.get('repository', {})
        repo_id = int(repository.get('id', 0))
        name = repository.get('full_name', '')
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', name):
            return 403
        recipients = [b.id for b in self.active(include_paused=True) if b.auth == mode and b.repo_id == repo_id
                      and (mode == 'legacy' or b.installation == installation)]
        if not recipients:
            return 403
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM inbox WHERE id=?', (mode + ':' + delivery,)).fetchone():
                return 202
            if event in ('issues', 'pull_request') and payload.get('action') in ('milestoned', 'demilestoned'):
                obj = payload.get('pull_request') or payload.get('issue') or {}
                milestone = payload.get('milestone') or {}
                identity = obj.get('number')
                target = milestone.get('node_id') or milestone.get('id')
                updated = obj.get('updated_at')
                if identity and target and updated:
                    db.execute('CREATE TABLE IF NOT EXISTS milestone_receipts(topic TEXT PRIMARY KEY,fingerprint TEXT NOT NULL,received REAL NOT NULL)')
                    topic = mode + ':' + str(repo_id) + ':' + str(identity)
                    fingerprint = json.dumps([payload['action'],str(target),updated,payload.get('sender',{}).get('id')])
                    previous = db.execute('SELECT fingerprint,received FROM milestone_receipts WHERE topic=?',(topic,)).fetchone()
                    if previous and previous[0] == fingerprint and time.time() - previous[1] < 30:
                        db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?,?,NULL,?,1)',(mode+':'+delivery,delivery,event,'[]'))
                        return 202
                    db.execute('INSERT INTO milestone_receipts VALUES(?,?,?) ON CONFLICT(topic) DO UPDATE SET fingerprint=excluded.fingerprint,received=excluded.received',(topic,fingerprint,time.time()))
                    db.execute('DELETE FROM milestone_receipts WHERE received<?',(time.time()-86400,))
            if db.execute('SELECT count(*) FROM inbox WHERE done=0').fetchone()[0] >= 10000:
                raise RuntimeError('Ingress capacity reached')
            db.execute('INSERT OR IGNORE INTO inbox VALUES(?,?,?,?,?,0)',
                       (mode + ':' + delivery, delivery, event, json.dumps(payload), json.dumps(recipients)))
        return 202

    def pending(self):
        with self.db() as db:
            return db.execute('SELECT id,delivery,event,payload,recipients FROM inbox WHERE done=0 ORDER BY rowid LIMIT 100').fetchall()

    def finish(self, identity):
        with self.db() as db:
            db.execute('UPDATE inbox SET done=1,payload=NULL WHERE id=?', (identity,))
