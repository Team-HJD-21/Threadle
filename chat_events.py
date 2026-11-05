"""Metadata-only, event-driven history for Threadle-managed Discord conversations."""
import logging
import re
import threading
import time

LOG = logging.getLogger('chat-events')
MESSAGE = re.compile(r'^/channels/([1-9][0-9]*)/messages/([1-9][0-9]*)$')


class ChatEvents:
    def __init__(self, service, clock=time.time):
        self.service, self.clock = service, clock
        self.lock = threading.RLock()
        self.scopes, self.checked, self.pruned = {}, float('-inf'), 0
        with self.db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS chat_events(
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, guild TEXT NOT NULL,
                    channel TEXT NOT NULL, message TEXT, author TEXT, actor TEXT,
                    source TEXT NOT NULL, outcome TEXT NOT NULL, created REAL NOT NULL);
                CREATE INDEX IF NOT EXISTS chat_events_created ON chat_events(created);
                CREATE INDEX IF NOT EXISTS chat_events_message ON chat_events(channel,message,created);
                CREATE TABLE IF NOT EXISTS discord_delete_intents(
                    channel TEXT NOT NULL,message TEXT NOT NULL,state TEXT NOT NULL,
                    reason TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(channel,message));
                CREATE TABLE IF NOT EXISTS chat_audit_events(
                    id TEXT PRIMARY KEY,guild TEXT NOT NULL,channel TEXT,actor TEXT,
                    target TEXT,action INTEGER NOT NULL,count INTEGER,created REAL NOT NULL);
            ''')

    def db(self):
        return self.service.registry.db()

    def managed(self, guild, channel, parent=None):
        guild, channel = str(guild), str(channel)
        with self.lock:
            if self.clock() - self.checked >= 5:
                scopes = {}
                for binding in self.service.registry.active():
                    config = binding.config
                    owner = str(config['guild_id'])
                    channels = set(str(v) for v in config.get('channels', {}).values() if v)
                    channels.update(str(config[k]) for k in ('alert_channel', 'ci_channel') if config.get(k))
                    for group in config.get('routing_groups', []):
                        if isinstance(group, dict) and group.get('channel'):
                            channels.add(str(group['channel']))
                    with self.service.queue(binding).connect() as db:
                        for thread, root in db.execute('SELECT thread,channel FROM links'):
                            channels.update((str(thread), str(root)))
                    scopes.setdefault(owner, set()).update(channels)
                self.scopes, self.checked = scopes, self.clock()
            allowed = self.scopes.get(guild, set())
            if channel in allowed:
                return True
            if parent is not None and str(parent) in allowed:
                allowed.add(channel)
                return True
            return False

    def record(self, kind, guild, channel, message=None, author=None, actor=None,
               source='gateway', outcome='observed', parent=None):
        if not self.managed(guild, channel, parent):
            return False
        with self.db() as db:
            if not author and message:
                previous=db.execute('SELECT author FROM chat_events WHERE guild=? AND channel=? AND message=? AND author IS NOT NULL ORDER BY created DESC,id DESC LIMIT 1',
                                    (str(guild),str(channel),str(message))).fetchone()
                author=previous[0] if previous else None
            db.execute('INSERT INTO chat_events(kind,guild,channel,message,author,actor,source,outcome,created) VALUES(?,?,?,?,?,?,?,?,?)',
                       (kind, str(guild), str(channel), str(message) if message else None,
                        str(author) if author else None, str(actor) if actor else None,
                        source, outcome, self.clock()))
        LOG.info('Chat event kind=%s guild=%s channel=%s message=%s author=%s actor=%s source=%s outcome=%s',
                 kind, guild, channel, message or '-', author or '-', actor or 'unknown', source, outcome)
        self.prune()
        return True

    def prune(self):
        with self.lock:
            if self.clock() - self.pruned < 300:
                return
            with self.db() as db:
                for table in ('chat_events', 'chat_audit_events', 'discord_delete_intents'):
                    db.execute('DELETE FROM ' + table + ' WHERE created<?', (self.clock() - 30*86400,))
            self.pruned = self.clock()

    def intent(self, channel, message, reason='automatic-cleanup'):
        # This is deliberately not best-effort telemetry: persist BEFORE DELETE.
        with self.db() as db:
            db.execute('INSERT INTO discord_delete_intents VALUES(?,?,?,?,?) ON CONFLICT(channel,message) DO UPDATE SET state=excluded.state,reason=excluded.reason,created=excluded.created',
                       (str(channel), str(message), 'pending', reason, self.clock()))

    def finish(self, channel, message, state):
        with self.db() as db:
            db.execute('UPDATE discord_delete_intents SET state=? WHERE channel=? AND message=?',
                       (state, str(channel), str(message)))

    def delete_state(self, channel, message):
        with self.db() as db:
            row = db.execute('SELECT state FROM discord_delete_intents WHERE channel=? AND message=?',
                             (str(channel), str(message))).fetchone()
        return row[0] if row else None

    def own_delete(self, channel, message):
        return self.delete_state(channel,message) in ('pending','done')

    def before_api(self, method, path):
        match = MESSAGE.fullmatch(path)
        if method == 'DELETE' and match:
            self.intent(*match.groups())

    def after_api(self, method, path, result):
        match = MESSAGE.fullmatch(path)
        if method == 'DELETE' and match:
            self.finish(*match.groups(), 'done')
        if method not in ('POST','PATCH','DELETE'):
            return
        match = re.fullmatch(r'/channels/([1-9][0-9]*)/messages(?:/([1-9][0-9]*))?',path)
        if not match:
            return
        self.managed('',match[1])
        guild = next((guild for guild,channels in self.scopes.items() if match[1] in channels),None)
        if guild:
            message = match[2] or (result or {}).get('id')
            self.record('message-'+{'POST':'create','PATCH':'edit','DELETE':'delete'}[method],
                        guild,match[1],message,actor=getattr(self,'bot_id',None),source='threadle',outcome='succeeded')

    def failed_api(self, method, path, status):
        match = MESSAGE.fullmatch(path)
        if method == 'DELETE' and match and status in (400, 401, 403, 429):
            self.finish(*match.groups(), 'failed')

    def audit(self, value):
        # Audit MESSAGE_DELETE aggregates a channel/author/count, not message IDs.
        # Store the evidence separately; never invent an exact deletion actor.
        guild, channel = str(value['guild']), value.get('channel')
        if not channel or not self.managed(guild, channel):
            return False
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO chat_audit_events VALUES(?,?,?,?,?,?,?,?)',
                       (str(value['id']), guild, str(channel), value.get('actor'),
                        value.get('target'), int(value['action']), value.get('count'), self.clock()))
        LOG.info('Chat audit guild=%s channel=%s audit=%s actor=%s target=%s action=%s count=%s',
                 guild,channel,value['id'],value.get('actor') or 'unknown',value.get('target') or '-',
                 value['action'],value.get('count'))
        self.prune()
        return True
