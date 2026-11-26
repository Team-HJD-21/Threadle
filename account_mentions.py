"""Explicit user-only mentions, scoped to opted-in members who can see the channel."""
import copy
import re
import time

from connect_auth import channel_permissions
from discord_threads import ApiError

# Ignore code, Markdown links/URLs, email addresses, and GitHub team mentions.
TOKENS = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|<!--[\s\S]*?-->|<[^>\n]*>|!?\[[^\]]*\]\([^)]*\)|https?://\S+|(?<![\w/@\\])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))(?![\w/-])')


def names(text):
    return {match[1].lower() for match in TOKENS.finditer(text or '') if match[1]}


def replace(text, mapping):
    if not isinstance(text, str):
        return text
    return TOKENS.sub(lambda m: '<@' + mapping[m[1].lower()] + '>' if m[1] and m[1].lower() in mapping else m[0], text)


class MentionPolicy:
    def __init__(self, service, accounts, binding, event, payload, event_id):
        self.service, self.accounts, self.binding, self.event_id = service, accounts, binding, event_id
        self.cache = {}
        self.profiles = {}
        self.names = set()
        entries = payload.get('_events', []) if event == 'threadle_bundle' else [{'payload': payload}]
        for entry in entries:
            value = entry['payload']
            # A new comment must not re-ping everyone mentioned in the old PR description.
            objects = [value['comment']] if value.get('comment') else [value.get(key, {}) for key in ('pull_request', 'issue', 'discussion', 'review')]
            for obj in objects:
                self.names.update(names(obj.get('body', '')))

    def allowed(self, channel, discord, github, login):
        key = channel, discord
        if key in self.cache:
            return self.cache[key]
        allowed = False
        try:
            api = self.service.discord()
            destination = api.call('GET', '/channels/' + channel)
            if str(destination.get('guild_id')) != self.binding.config['guild_id']:
                return False
            parent = destination
            if destination.get('type') in (10, 11, 12):
                if destination.get('type') == 12:
                    api.call('GET', f'/channels/{channel}/thread-members/{discord}')
                parent = api.call('GET', '/channels/' + str(destination['parent_id']))
            guild = self.binding.config['guild_id']
            member = api.call('GET', f'/guilds/{guild}/members/{discord}')
            roles = api.call('GET', f'/guilds/{guild}/roles')
            allowed = str(member.get('user', {}).get('id')) == discord and bool(channel_permissions(guild, member, roles, parent) & (1 << 10))
        except ApiError:
            # Mention lookup must not delay or lose the underlying GitHub notification.
            allowed = False
        self.cache[key] = allowed
        return allowed

    def prepare(self, method, path, data):
        if self.binding.auth != 'app' or method not in ('POST', 'PATCH') or not isinstance(data, dict) or not re.fullmatch(r'/channels/\d+/messages(?:/\d+)?', path) or not data.get('embeds'):
            return data, None
        channel = path.split('/')[2]
        visible = set()
        for embed in data['embeds']:
            visible.update(names(embed.get('description', '')))
            for field in embed.get('fields', []):
                visible.update(names(field.get('value', '')))
        wanted = visible & self.names
        if not wanted:
            return data, None
        fresh = set(data.get('_mention_names', ())) if method == 'POST' and not data.get('flags', 0) & (1 << 12) else set()
        with self.accounts.db() as db:
            rows = db.execute('SELECT a.discord,a.github,p.discord IS NOT NULL FROM personal_accounts a '
                              'JOIN personal_guilds g ON a.discord=g.discord '
                              'LEFT JOIN personal_mention_prefs p ON p.discord=g.discord AND p.guild=g.guild '
                              'WHERE g.guild=? AND (g.mentions=1 OR p.discord IS NOT NULL)',
                              (self.binding.config['guild_id'],)).fetchall()
            already_sent = {row[0] for row in db.execute(
                'SELECT discord FROM mention_receipts WHERE event=? AND channel=?', (self.event_id, channel)
            )} if fresh else set()
        if not rows:
            return data, None
        by_identity = {row[1]: row for row in rows}
        mapping = {}
        pings = set()
        for login in sorted(wanted)[:20]:
            # Resolve the current numeric identity, rather than trust a cached login:
            # renames keep working, and a reassigned old login never pings its old owner.
            if login not in self.profiles:
                try:
                    token = self.service.tokens.token(self.binding.installation, self.binding.repo_id)
                    github_api = self.service.api_factory('GitHub', 'https://api.github.com', token, 'Bearer')
                    self.profiles[login] = github_api.call('GET', '/users/' + login)
                except ApiError:
                    self.profiles[login] = {}
            profile = self.profiles[login]
            row = by_identity.get(str(profile.get('id')))
            if row and str(profile.get('login', '')).lower() == login:
                discord, github, ping = row
                if self.allowed(channel, discord, github, login):
                    mapping[login] = discord
                    if ping and login in fresh and discord not in already_sent:
                        pings.add(discord)
                    with self.accounts.db() as db:
                        db.execute('UPDATE personal_accounts SET login=? WHERE github=?', (profile['login'], github))
        if not mapping:
            return data, None
        value = copy.deepcopy(data)
        for embed in value['embeds']:
            if 'description' in embed:
                embed['description'] = replace(embed['description'], mapping)
            for field in embed.get('fields', []):
                field['value'] = replace(field.get('value', ''), mapping)
        value['allowed_mentions'] = {'parse': [], 'users': sorted(pings), 'replied_user': False}
        if pings:
            value['_thread_card_v2'] = True
        return value, (channel, pings) if pings else None

    def remember(self, operation):
        if operation is None:
            return
        channel, pings = operation
        with self.accounts.db() as db:
            db.executemany('INSERT OR IGNORE INTO mention_receipts VALUES(?,?,?,?)',
                           [(self.event_id, channel, discord, time.time()) for discord in pings])
