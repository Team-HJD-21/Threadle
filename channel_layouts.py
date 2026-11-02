from contextlib import contextmanager
import json
import math
import sqlite3
import time
import uuid

from connect_auth import ConnectError, channel_permissions
from discord_threads import ApiError

CHANNELS = [('issue', '‼️github-issue'), ('pr', '🙏🏻github-pr'),
            ('discussion', '🙋🏻github-discussions'), ('ci', '🔄github-ci'), ('alert', '🚨github-alert')]


def category_name(repository, qualified=False):
    return ('🔔 ' + (repository if qualified else repository.rsplit('/', 1)[-1]))[:100]


class ChannelLayouts:
    def __init__(self, path, bot_id, clock=time.time):
        self.path, self.bot_id, self.clock = path, str(bot_id), clock
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS layouts(guild TEXT PRIMARY KEY, data TEXT NOT NULL, owner TEXT, lease REAL NOT NULL DEFAULT 0)')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db:
                yield db
        finally:
            db.close()

    @contextmanager
    def claim(self, guild):
        owner = uuid.uuid4().hex
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data,owner,lease FROM layouts WHERE guild=?', (guild,)).fetchone()
            if row and row[1] and row[2] > self.clock():
                raise ConnectError('이 서버의 알림 채널을 생성하고 있습니다. 잠시 후 현재 단계에서 다시 확인해 주세요.', 409)
            data = json.loads(row[0]) if row else {'token': uuid.uuid4().hex, 'category': None, 'channels': {}, 'pending': None}
            db.execute('INSERT INTO layouts VALUES(?,?,?,?) ON CONFLICT(guild) DO UPDATE SET owner=excluded.owner,lease=excluded.lease',
                       (guild, json.dumps(data), owner, self.clock() + 300))
        try:
            yield owner, data
        finally:
            with self.db() as db:
                db.execute('UPDATE layouts SET owner=NULL,lease=0 WHERE guild=? AND owner=?', (guild, owner))

    def save(self, guild, owner, data):
        with self.db() as db:
            changed = db.execute('UPDATE layouts SET data=?,lease=? WHERE guild=? AND owner=?',
                                 (json.dumps(data), self.clock() + 300, data.get('scope', guild), owner)).rowcount
        if changed != 1:
            raise ConnectError('다른 연결 요청이 진행 중입니다. 잠시 후 다시 확인해 주세요.', 409)

    def name(self, guild, repo_id, repository):
        with self.db() as db:
            rows = db.execute('SELECT data FROM layouts WHERE guild LIKE ?', (str(guild) + ':%',)).fetchall()
        other = [json.loads(row[0]) for row in rows]
        collision = any(row.get('repo_id') != int(repo_id) and row.get('repository', '').rsplit('/', 1)[-1]
                        == repository.rsplit('/', 1)[-1] for row in other)
        return category_name(repository, qualified=collision)

    def can_manage(self, api, guild):
        bot = api.call('GET', '/users/@me')
        if not bot.get('bot') or str(bot.get('id')) != self.bot_id:
            raise ConnectError('봇 설정을 확인할 수 없습니다. 운영자에게 문의해 주세요.', 503)
        try:
            member = api.call('GET', f'/guilds/{guild}/members/{self.bot_id}')
            roles = api.call('GET', f'/guilds/{guild}/roles')
        except ApiError as error:
            if error.status in (403, 404):
                return False
            raise
        return bool(channel_permissions(guild, member, roles, {}) & (1 << 4))

    @staticmethod
    def valid(row, guild, kind, parent=None):
        return (str(row.get('guild_id')) == guild and row.get('type') == kind
                and str(row.get('id', '')).isdigit() and int(row['id']) > 0
                and (kind == 4 or str(row.get('parent_id')) == parent))

    def snapshot(self, api, guild):
        return api.call('GET', f'/guilds/{guild}/channels')

    def reconcile(self, api, guild, owner, data):
        pending = data['pending']
        if not pending:
            return
        rows = self.snapshot(api, guild)
        matches = [row for row in rows if str(row['id']) not in pending['before']
                   and self.valid(row, guild, pending['type'], pending.get('parent'))
                   and (row.get('name') == pending['name'] if pending['type'] == 4 else row.get('topic') == pending['topic'])]
        if len(matches) != 1:
            raise ConnectError('Discord 채널 생성 결과를 아직 확인할 수 없습니다. 중복 생성을 막기 위해 다시 만들지 않고 있습니다. 잠시 후 다시 확인해 주세요.', 502)
        if pending['key'] == 'category':
            data['category'] = str(matches[0]['id'])
        else:
            data['channels'][pending['key']] = str(matches[0]['id'])
        data['pending'] = None
        self.save(guild, owner, data)

    def create(self, api, guild, owner, data, key, payload):
        rows = self.snapshot(api, guild)
        data['pending'] = {'key': key, 'type': payload['type'], 'name': payload['name'],
                           'topic': payload.get('topic'), 'parent': payload.get('parent_id'),
                           'before': [str(row['id']) for row in rows]}
        self.save(guild, owner, data)
        try:
            row = api.create_channel(guild, payload)
            if not self.valid(row, guild, payload['type'], payload.get('parent_id')):
                raise ApiError('Discord', 'invalid-response')
        except ApiError as error:
            if error.status in (400, 401, 403, 404, 429, 'not-sent'):
                data['pending'] = None
                if error.status == 429:
                    try:
                        delay = float(error.retry_after)
                        delay = delay if math.isfinite(delay) and delay > 0 else 5
                    except (TypeError, ValueError):
                        delay = 5
                    data['not_before'] = self.clock() + delay
                self.save(guild, owner, data)
                if error.status == 'not-sent':
                    raise ConnectError('Discord에 채널 생성 요청을 보내지 못했습니다. 현재 단계에서 다시 시도해 주세요.', 502) from None
                raise ConnectError('Discord에서 채널을 만들지 못했습니다. 봇의 채널 관리 권한과 서버 채널 수를 확인한 뒤 다시 시도해 주세요.', 429 if error.status == 429 else 403) from None
            self.reconcile(api, guild, owner, data)
            return
        if key == 'category':
            data['category'] = str(row['id'])
        else:
            data['channels'][key] = str(row['id'])
        data['pending'] = None
        self.save(guild, owner, data)

    def ensure(self, api, guild, repo_id, repository, groups=None):
        from pr_routing import validate_groups, group_channel_name
        groups = validate_groups(groups or [], provisioned=False)
        guild = str(guild)
        if not str(repo_id).isdigit() or int(repo_id) <= 0:
            raise ValueError('Repository ID must be positive')
        if not self.can_manage(api, guild):
            raise ConnectError('알림 채널 자동 생성을 위해 봇의 채널 관리 권한을 승인해 주세요.', 403)
        with self.claim(guild + ':' + str(repo_id)) as (owner, data):
            data.update({'scope': guild + ':' + str(repo_id), 'repo_id': int(repo_id), 'repository': repository})
            self.save(guild, owner, data)
            if data.get('not_before', 0) > self.clock():
                raise ConnectError('Discord의 채널 생성 대기 시간이 남아 있습니다. 잠시 후 다시 시도해 주세요.', 429)
            self.reconcile(api, guild, owner, data)
            if not data['category']:
                self.create(api, guild, owner, data, 'category',
                            {'name': 'GitHub Notify · setup-' + data['token'], 'type': 4})
            category = api.call('GET', '/channels/' + data['category'])
            if not self.valid(category, guild, 4):
                raise ConnectError('기존 GitHub 카테고리의 서버나 종류가 변경됐습니다. 채널 설정을 확인해 주세요.', 409)
            title = self.name(guild, repo_id, repository)
            if (category.get('name') == 'GitHub Notify · setup-' + data['token']
                    or (category.get('name') == data.get('title') and category.get('name') != title)):
                api.call('PATCH', '/channels/' + data['category'], {'name': title})
                data['title'] = title
                self.save(guild, owner, data)
            planned = CHANNELS + [('role:' + group['id'], group_channel_name(group)) for group in groups]
            for position, (key, name) in enumerate(planned):
                if key in data['channels']:
                    try:
                        channel = api.call('GET', '/channels/' + data['channels'][key])
                    except ApiError as error:
                        if error.status != 404:
                            raise
                        channel = None
                    if channel is not None:
                        if not self.valid(channel, guild, 0, data['category']):
                            raise ConnectError('기존 알림 채널이 다른 카테고리로 이동됐습니다. 채널 설정을 확인해 주세요.', 409)
                        continue
                self.create(api, guild, owner, data, key,
                            {'name': name, 'type': 0, 'parent_id': data['category'], 'position': position,
                             'topic': 'GitHub Notify managed channel | layout=' + data['token'] + '; kind=' + key})
            config = {'guild_id': guild, 'channels': {key: data['channels'][key] for key in ('issue', 'pr', 'discussion')},
                      'alert_channel': data['channels']['alert'], 'ci_channel': data['channels']['ci']}
            if groups:
                config['routing_groups'] = [dict(group, channel=data['channels']['role:' + group['id']]) for group in groups]
            return config
