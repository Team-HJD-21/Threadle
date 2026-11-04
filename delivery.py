import json
import state_db as sqlite3
import time
from contextlib import contextmanager
from discord_threads import Threads, marker

COALESCE_SECONDS=0


class Queue:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, event TEXT NOT NULL, '
                       'payload TEXT, done INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0, next REAL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS coalescing_jobs(id TEXT PRIMARY KEY,topic TEXT NOT NULL,received REAL NOT NULL,group_id TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS coalescing_topic ON coalescing_jobs(topic,received)')
            db.execute('CREATE TABLE IF NOT EXISTS delivery_groups(id TEXT PRIMARY KEY,leader TEXT NOT NULL,job_ids TEXT NOT NULL,payload TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS delivery_latency(delivery TEXT PRIMARY KEY,event TEXT NOT NULL,received REAL NOT NULL,started REAL,first_discord REAL,completed REAL,deferred INTEGER NOT NULL DEFAULT 0,webhook INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE INDEX IF NOT EXISTS delivery_latency_completed ON delivery_latency(completed)')
            db.execute('CREATE TABLE IF NOT EXISTS links (topic TEXT PRIMARY KEY, channel TEXT NOT NULL, thread TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deferred_threads(topic TEXT PRIMARY KEY,channel TEXT NOT NULL,message TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deferred_thread_jobs(delivery TEXT PRIMARY KEY,topic TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deferred_thread_pace(id INTEGER PRIMARY KEY,next REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def put(self, delivery, event, payload, received=None):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM jobs WHERE id=?', (delivery,)).fetchone():
                return
            if db.execute('SELECT count(*) FROM jobs WHERE done=0').fetchone()[0] >= 10000:
                raise sqlite3.OperationalError('Queue capacity reached')
            db.execute('INSERT OR IGNORE INTO jobs(id,event,payload) VALUES(?,?,?)',
                       (delivery, event, json.dumps(payload)))
            db.execute('INSERT OR IGNORE INTO delivery_latency(delivery,event,received,webhook) VALUES(?,?,?,?)',(delivery,event,time.time() if received is None else received,int(received is not None)))
            from notification_layout import topic_identity
            topic = topic_identity(event,payload)
            if topic:
                db.execute('INSERT OR IGNORE INTO coalescing_jobs VALUES(?,?,?,NULL)',(delivery,topic,time.time()))

    def next(self, window_seconds=0):
        now=time.time()
        with self.connect() as db:
            return db.execute("""
                SELECT id,event,payload,attempts FROM jobs
                WHERE done=0 AND next<=?
                AND NOT EXISTS (SELECT 1 FROM deferred_thread_jobs d JOIN deferred_thread_pace p ON p.id=1
                    WHERE d.delivery=jobs.id AND p.next>?)
                AND NOT EXISTS (SELECT 1 FROM coalescing_jobs c WHERE c.id=jobs.id
                    AND c.group_id IS NULL AND ?>0 AND c.received>?)
                AND NOT EXISTS (SELECT 1 FROM coalescing_jobs current
                    JOIN coalescing_jobs older ON older.topic=current.topic JOIN jobs prior ON prior.id=older.id
                    WHERE current.id=jobs.id AND prior.done=0 AND prior.rowid<jobs.rowid
                    AND CASE WHEN json_valid(jobs.payload) THEN coalesce(json_extract(jobs.payload,'$.action'),'') ELSE '' END<>'deleted'
                    AND NOT EXISTS (SELECT 1 FROM deferred_thread_jobs d WHERE d.delivery=prior.id AND prior.attempts=0))
                AND NOT EXISTS (SELECT 1 FROM coalescing_jobs c JOIN delivery_groups g ON g.id=c.group_id
                    WHERE c.id=jobs.id AND g.leader<>jobs.id)
                ORDER BY rowid LIMIT 1
            """,(now,now,window_seconds,now-window_seconds)).fetchone()

    def group(self, job, seconds=COALESCE_SECONDS):
        delivery,event,raw,attempts=job
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT topic,received,group_id FROM coalescing_jobs WHERE id=?',(delivery,)).fetchone()
            if not row:
                return job, [delivery]
            topic,received,group=row
            if group:
                saved=db.execute('SELECT payload,job_ids FROM delivery_groups WHERE id=?',(group,)).fetchone()
                return (delivery,'threadle_bundle',saved[0],attempts),json.loads(saved[1])
            if seconds <= 0:
                return job,[delivery]
            if time.time()<received+seconds:
                return None
            rows=db.execute('SELECT j.id,j.event,j.payload FROM jobs j JOIN coalescing_jobs c ON c.id=j.id WHERE j.done=0 AND c.group_id IS NULL AND c.topic=? AND c.received<=? ORDER BY j.rowid LIMIT 20',(topic,received+seconds)).fetchall()
            if not rows:
                return job,[delivery]
            ids=[item[0] for item in rows]
            from notification_layout import group_payload
            payload=group_payload([(e,json.loads(data)) for _,e,data in rows])
            from discord_threads import nonce
            group='coalesce:'+nonce(json.dumps(ids))
            raw=json.dumps(payload)
            db.execute('INSERT INTO delivery_groups VALUES(?,?,?,?)',(group,delivery,json.dumps(ids),raw))
            db.executemany('UPDATE coalescing_jobs SET group_id=? WHERE id=?',[(group,identity) for identity in ids])
            return (delivery,'threadle_bundle',raw,attempts),ids

    def save_group(self, leader, payload):
        with self.connect() as db:
            db.execute('UPDATE delivery_groups SET payload=? WHERE leader=? AND payload IS NOT NULL',(json.dumps(payload),leader))

    def finish_group(self, ids):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for identity in ids:
                db.execute('UPDATE jobs SET done=1,payload=NULL WHERE id=?',(identity,))
                db.execute('UPDATE delivery_groups SET payload=NULL WHERE leader=?',(identity,))
                db.execute('DELETE FROM coalescing_jobs WHERE id=?',(identity,))

    def finish(self, delivery):
        self.finish_group([delivery])

    def retry(self, delivery, attempts, retry_after=None):
        from api_cooldowns import number
        requested=number(retry_after)
        delay=max(.1,requested) if requested is not None else min(900,2**min(attempts+1,10))
        with self.connect() as db:
            db.execute('UPDATE jobs SET attempts=?,next=? WHERE id=?',
                       (attempts + 1, time.time() + delay, delivery))


class CachedThreads(Threads):
    def __init__(self, discord, guild, channels, queue, language='ko'):
        super().__init__(discord, guild, channels, language)
        self.queue = queue
        from message_tracking import bind
        bind(discord, queue)
        if queue is not None:
            with queue.connect() as db:
                migrating = not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='thread_receipts'").fetchone()
                db.execute('CREATE TABLE IF NOT EXISTS receipt_migration_jobs(id TEXT PRIMARY KEY)')
                if migrating:
                    db.execute('INSERT OR IGNORE INTO receipt_migration_jobs SELECT id FROM jobs WHERE done=0')
                db.execute('CREATE TABLE IF NOT EXISTS thread_receipts(receipt TEXT PRIMARY KEY, message TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS root_index(topic TEXT PRIMARY KEY, channel TEXT, message TEXT)')
                db.execute('CREATE TABLE IF NOT EXISTS root_cursors(channel TEXT PRIMARY KEY, cursor TEXT)')

    def delivered(self, thread, receipt):
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT message FROM thread_receipts WHERE receipt=?', (receipt,)).fetchone()
            if row and row[0]:
                return True
            if row is None:
                db.execute('INSERT INTO thread_receipts(receipt) VALUES(?)', (receipt,))
                if not getattr(self, "recovering", False):
                    return False
        for message in self.messages(thread):
            if receipt in marker(message):
                self.remember_delivery(receipt, message['id'])
                return True
        return False

    def remember_delivery(self, receipt, message):
        with self.queue.connect() as db:
            db.execute('UPDATE thread_receipts SET message=? WHERE receipt=?', (message, receipt))

    def find(self, channel, topic):
        with self.queue.connect() as db:
            saved = db.execute('SELECT thread FROM links WHERE topic=? AND channel=?',
                               (topic.key, channel)).fetchone()
        if saved:
            thread = self.api.call('GET', '/channels/' + saved[0])
            if thread.get('parent_id') != channel:
                raise RuntimeError('Stored thread belongs to a different channel')
            return thread
        with self.queue.connect() as db:
            cursor = db.execute('SELECT cursor FROM root_cursors WHERE channel=?', (channel,)).fetchone()
        newest, roots = None, []
        for message in self.messages(channel):
            newest = newest or message['id']
            if cursor and message['id'] == cursor[0]:
                break
            if message.get('author', {}).get('id') == self.bot['id']:
                for key in marker(message):
                    if isinstance(key, str) and key.startswith('github-thread:'):
                        roots.append((key, channel, message['id']))
        with self.queue.connect() as db:
            db.executemany('INSERT OR REPLACE INTO root_index VALUES(?,?,?)', roots)
            if newest:
                db.execute('INSERT OR REPLACE INTO root_cursors VALUES(?,?)', (channel, newest))
            root = db.execute('SELECT message FROM root_index WHERE topic=? AND channel=?', (topic.key, channel)).fetchone()
        return self.start(channel, root[0], topic) if root else None

    def ensure(self, topic):
        with self.queue.connect() as db:
            saved = db.execute('SELECT channel FROM links WHERE topic=?', (topic.key,)).fetchone()
            if not saved:saved=db.execute('SELECT channel FROM deferred_threads WHERE topic=?',(topic.key,)).fetchone()
        previous = self.channels
        self.channels = dict(previous, **{topic.kind: saved[0]}) if saved else previous
        try:
            thread = super().ensure(topic)
            with self.queue.connect() as db:
                db.execute('INSERT OR REPLACE INTO links VALUES(?,?,?)',
                           (topic.key, self.channels[topic.kind], thread['id']))
            return thread
        finally:
            self.channels = previous

    def deliver(self, topic, embed, event_key, closed):
        delivery = event_key.removeprefix('webhook:')
        with self.queue.connect() as db:
            self.recovering = bool(db.execute('SELECT 1 FROM receipt_migration_jobs WHERE id=?', (delivery,)).fetchone())
        super().deliver(topic, embed, event_key, closed)
        with self.queue.connect() as db:
            return db.execute('SELECT thread FROM links WHERE topic=?', (topic.key,)).fetchone()[0]
