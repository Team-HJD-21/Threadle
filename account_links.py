"""Opt-in personal identities. Secrets and queued comment bodies are encrypted at rest.

Use the already backed-up registry database; never create a plaintext token sidecar.
"""
import json
import re
import threading
import time
from urllib.parse import urlencode

from connect_auth import ConnectError, UserApis


def snowflake(value):
    return bool(re.fullmatch(r'[1-9][0-9]{0,24}', str(value)))


def command_definition():
    return {'name': 'account', 'type': 1, 'description': 'Connect your own GitHub identity and comment preferences.',
            'default_member_permissions': None, 'contexts': [0], 'integration_types': [0],
            'name_localizations': None, 'description_localizations': None,
            'options': [{'type': 1, 'name': name, 'description': description} for name, description in (
                ('connect', 'Privately link your GitHub account for this server.'),
                ('status', 'Privately check your account connection for this server.'),
                ('disconnect', 'Remove your account connection and pending comments in every server.'))]}


class Accounts:
    def __init__(self, service, settings, cipher, clock=time.time):
        self.service, self.settings, self.cipher, self.clock = service, settings, cipher, clock
        self.lock = threading.RLock()
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS personal_accounts(discord TEXT PRIMARY KEY, github TEXT UNIQUE NOT NULL,
                    login TEXT NOT NULL, credential BLOB, generation TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS personal_guilds(discord TEXT NOT NULL, guild TEXT NOT NULL,
                    mentions INTEGER NOT NULL, comments INTEGER NOT NULL, consent REAL NOT NULL,
                    PRIMARY KEY(discord,guild));
                CREATE TABLE IF NOT EXISTS comment_outbox(id TEXT PRIMARY KEY, binding TEXT NOT NULL,
                    discord TEXT NOT NULL, github TEXT NOT NULL, generation TEXT NOT NULL, guild TEXT NOT NULL,
                    thread TEXT NOT NULL, topic TEXT NOT NULL, operation TEXT UNIQUE NOT NULL, payload BLOB,
                    state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
                    next REAL NOT NULL DEFAULT 0, created REAL NOT NULL, remote TEXT, url TEXT,
                    error TEXT, notified INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS comment_outbox_ready ON comment_outbox(state,next,created);
                CREATE TABLE IF NOT EXISTS mention_receipts(event TEXT NOT NULL, channel TEXT NOT NULL,
                    discord TEXT NOT NULL, created REAL NOT NULL, PRIMARY KEY(event,channel,discord));
            ''')

    def db(self):
        return self.service.registry.db()

    def encode(self, value):
        return self.cipher.encrypt(json.dumps(value, ensure_ascii=False).encode())

    def decode(self, value):
        return json.loads(self.cipher.decrypt(bytes(value)))

    def get(self, discord, guild=None):
        with self.db() as db:
            row = db.execute('SELECT github,login,generation,credential FROM personal_accounts WHERE discord=?', (str(discord),)).fetchone()
            options = db.execute('SELECT mentions,comments FROM personal_guilds WHERE discord=? AND guild=?', (str(discord), str(guild))).fetchone() if guild else None
        if row is None:
            return None
        return {'discord': str(discord), 'github': row[0], 'login': row[1], 'generation': row[2],
                'authorized': row[3] is not None, 'mentions': bool(options and options[0]),
                'comments': bool(options and options[1])}

    def link(self, discord, guild, profile, credential, mentions, comments):
        import secrets
        identity, login = str(profile.get('id', '')), profile.get('login', '')
        if not all(snowflake(v) for v in (discord, guild, identity)) or not re.fullmatch(r'[A-Za-z0-9-]{1,39}', login):
            raise ConnectError('Invalid account identity.', 400)
        token = credential.get('access_token')
        if not isinstance(token, str) or not token or credential.get('token_type', '').lower() != 'bearer':
            raise ConnectError('GitHub authorization could not be verified.', 403)
        saved = {'access_token': token, 'expires': self.clock() + int(credential.get('expires_in', 28800)),
                 'refresh_token': credential.get('refresh_token'),
                 'refresh_expires': self.clock() + int(credential.get('refresh_token_expires_in', 0))}
        # Tokens are not needed for identity-only mentions.
        encrypted = self.encode(saved) if comments else None
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            owner = db.execute('SELECT discord FROM personal_accounts WHERE github=?', (identity,)).fetchone()
            if owner and owner[0] != str(discord):
                raise ConnectError('This GitHub account is already connected to another Discord account. Disconnect it there first.', 409)
            old = db.execute('SELECT github,credential,generation FROM personal_accounts WHERE discord=?', (str(discord),)).fetchone()
            if old and old[0] != identity:
                raise ConnectError('Disconnect your current account before choosing another GitHub account.', 409)
            generation = old[2] if old else secrets.token_hex(16)
            # Connecting for mentions in another guild must not remove an existing write grant.
            if old and not comments:
                encrypted = old[1]
            db.execute('INSERT INTO personal_accounts VALUES(?,?,?,?,?,?) ON CONFLICT(discord) DO UPDATE SET login=excluded.login,credential=excluded.credential,updated=excluded.updated',
                       (str(discord), identity, login, encrypted, generation, self.clock()))
            db.execute('INSERT INTO personal_guilds VALUES(?,?,?,?,?) ON CONFLICT(discord,guild) DO UPDATE SET mentions=excluded.mentions,comments=excluded.comments,consent=excluded.consent',
                       (str(discord), str(guild), int(mentions), int(comments), self.clock()))
            if not comments:
                db.execute("UPDATE comment_outbox SET state='failed',payload=NULL,error='disconnected',notified=1 WHERE discord=? AND guild=? AND state IN ('queued','sending','uncertain')", (str(discord), str(guild)))

    def disconnect(self, discord=None, github=None):
        if discord is None and github is None:
            return
        # The worker holds this same lock over credential use and a single write.
        with self.lock, self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT discord FROM personal_accounts WHERE ' + ('discord=?' if discord is not None else 'github=?'), (str(discord if discord is not None else github),)).fetchall()
            for (user,) in rows:
                db.execute("UPDATE comment_outbox SET state='failed',payload=NULL,error='disconnected',notified=1 WHERE discord=? AND state IN ('queued','sending','uncertain')", (user,))
                db.execute('DELETE FROM personal_guilds WHERE discord=?', (user,))
                db.execute('DELETE FROM personal_accounts WHERE discord=?', (user,))

    def token(self, discord, generation):
        """Call while holding lock; rotating refresh tokens must never race."""
        with self.db() as db:
            row = db.execute('SELECT credential FROM personal_accounts WHERE discord=? AND generation=?', (discord, generation)).fetchone()
        if row is None or row[0] is None:
            raise ConnectError('Connect your GitHub account again.', 401)
        credential = self.decode(row[0])
        if credential['expires'] > self.clock() + 60:
            return credential['access_token']
        if not credential.get('refresh_token') or credential['refresh_expires'] <= self.clock():
            self.disconnect(discord=discord)
            raise ConnectError('GitHub authorization expired. Connect again.', 401)
        # Persist consumption before exchange: a crash must require reconnect, never reuse a rotated token.
        with self.db() as db:
            db.execute('UPDATE personal_accounts SET credential=NULL WHERE discord=?', (discord,))
        result = UserApis(self.settings).request('https://github.com/login/oauth/access_token', form={
            'client_id': self.settings.github_client, 'client_secret': self.settings.github_secret,
            'grant_type': 'refresh_token', 'refresh_token': credential['refresh_token']})
        if not result.get('access_token') or not result.get('refresh_token') or result.get('token_type', '').lower() != 'bearer':
            raise ConnectError('GitHub authorization expired. Connect again.', 401)
        credential = {'access_token': result['access_token'], 'refresh_token': result['refresh_token'],
                      'expires': self.clock() + int(result['expires_in']),
                      'refresh_expires': self.clock() + int(result['refresh_token_expires_in'])}
        with self.db() as db:
            db.execute('UPDATE personal_accounts SET credential=? WHERE discord=? AND generation=?', (self.encode(credential), discord, generation))
        return credential['access_token']

    def interaction(self, payload, sessions, respond):
        def say(ko, en):
            return ko if str(payload.get('locale', '')).startswith('ko') else en
        guild, user = str(payload.get('guild_id', '')), str(payload.get('member', {}).get('user', {}).get('id', ''))
        channel, identity = str(payload.get('channel_id', '')), str(payload.get('id', ''))
        if not all(snowflake(v) for v in (guild, user, channel, identity)) or payload.get('member', {}).get('user', {}).get('bot'):
            return respond(say('참여 중인 Discord 서버에서 /account를 실행해 주세요.', 'Run /account in a Discord server you belong to.'))
        options = payload.get('data', {}).get('options', [])
        action = options[0].get('name') if len(options) == 1 else None
        if action == 'disconnect':
            self.disconnect(discord=user)
            return respond(say('모든 서버에서 내 GitHub 계정 연결을 해제하고 대기 중인 댓글을 취소했어요.', 'Your GitHub account is disconnected in every server. Pending comments were canceled.'))
        if action == 'status':
            account = self.get(user, guild)
            if not account:
                return respond(say('연결된 GitHub 계정이 없어요. /account connect로 연결해 주세요.', 'No GitHub account connected. Use /account connect.'))
            status = f"GitHub: {account['login']}\n" + say('이 서버 — 멘션: ', 'This server — mentions: ') + ('on' if account['mentions'] else 'off')
            status += say('; 댓글 전송: ', '; comments: ') + ('on' if account['comments'] and account['authorized'] else say('off / 재연결 필요', 'off / reconnect required'))
            status += '\n' + say('연결 해제: /account disconnect', 'Disconnect with /account disconnect.')
            bridge = getattr(self.service, 'comment_bridge', None)
            if account['comments'] and (bridge is None or not bridge.content_enabled):
                status += '\n' + say('Discord 메시지 접근이 아직 활성화되지 않았어요. 운영자가 Message Content Intent를 켜고 Threadle을 재시작해야 해요.', 'Discord message access is not active. The operator must enable Message Content Intent and restart Threadle.')
            return respond(status)
        if action != 'connect':
            return respond(say('/account connect, /account status, /account disconnect 중 하나를 실행해 주세요.', 'Use /account connect, /account status, or /account disconnect.'))
        code = sessions.ticket('account:' + identity, user, guild, channel)
        if code is None:
            return respond(say('이미 사용했거나 만료된 링크예요. /account connect를 다시 실행해 주세요.', 'This private link expired or was used. Run /account connect again.'))
        return respond(say('본인의 GitHub 계정을 연결해 주세요. 승인 전에 멘션과 댓글 전송을 선택할 수 있어요. 이 비공개 링크는 5분 후 만료돼요.', 'Link your own GitHub identity. You choose mentions and comment forwarding before authorizing. This private link expires in 5 minutes.'),
                       self.settings.base + '/connect/account?' + urlencode({'ticket': code}), say('내 GitHub 계정 연결', 'Connect my GitHub account'))
