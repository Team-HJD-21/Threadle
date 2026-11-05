"""One compact, durable repository activity card per channel and time window."""
import hashlib
import json
import threading

from discord_threads import ApiError, clip, nonce

LOCKS = [threading.RLock() for _ in range(64)]
PAGE_SIZE = 10


def receipt(feed, key, event):
    if feed.config.get('alert_scope'):
        key += ':channel:' + feed.config['alert_scope']
    return 'github-alert:' + nonce(key + ':' + event)


class ActivityCards:
    def __init__(self, feed, repo, english=False):
        self.feed, self.repo, self.english = feed, repo, english
        with feed.queue.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS activity_events(channel TEXT,event TEXT,topic TEXT,window TEXT,
                    PRIMARY KEY(channel,event,topic));
                CREATE TABLE IF NOT EXISTS activity_items(channel TEXT,window TEXT,topic TEXT,label TEXT,
                    activities TEXT,url TEXT,PRIMARY KEY(channel,window,topic));
                CREATE TABLE IF NOT EXISTS activity_pages(channel TEXT,window TEXT,page INTEGER,digest TEXT,
                    PRIMARY KEY(channel,window,page));
            ''')

    def update(self, items, window):
        """Persist before sending. Replays retain their original window across restarts."""
        feed = self.feed
        lock = LOCKS[int(hashlib.sha256((str(feed.queue.path) + ':' + str(feed.channel)).encode()).hexdigest(), 16) % len(LOCKS)]
        with lock:
            windows = set()
            with feed.queue.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for event, topic, title, description, url, color in items:
                    saved = db.execute('SELECT window FROM activity_events WHERE channel=? AND event=? AND topic=?',
                                       (feed.channel, event, topic)).fetchone()
                    selected = saved[0] if saved else window
                    windows.add(selected)
                    if saved:
                        continue
                    label, _, activity = title.partition(' · ')
                    previous = db.execute('SELECT activities FROM activity_items WHERE channel=? AND window=? AND topic=?',
                                          (feed.channel, selected, topic)).fetchone()
                    activities = json.loads(previous[0]) if previous else []
                    if activity and activity not in activities:
                        activities.append(activity)
                    db.execute('INSERT INTO activity_items VALUES(?,?,?,?,?,?) ON CONFLICT(channel,window,topic) '
                               'DO UPDATE SET label=excluded.label,activities=excluded.activities,url=excluded.url',
                               (feed.channel, selected, topic, clip(label, 70), json.dumps(activities), url))
                    db.execute('INSERT INTO activity_events VALUES(?,?,?,?)', (feed.channel, event, topic, selected))
            for selected in sorted(windows):
                self.publish(selected)

    def publish(self, window):
        feed = self.feed
        with feed.queue.connect() as db:
            rows = db.execute('SELECT label,activities,url FROM activity_items WHERE channel=? AND window=? ORDER BY rowid',
                              (feed.channel, window)).fetchall()
        for page, start in enumerate(range(0, len(rows), PAGE_SIZE)):
            lines = []
            for label, raw, url in rows[start:start + PAGE_SIZE]:
                activities = json.loads(raw)
                # Summarize kinds of activity, not duplicate webhook deliveries.
                text = ', '.join(clip(item, 60) for item in activities[:4])
                if len(activities) > 4:
                    text += f' +{len(activities)-4}'
                label = label.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
                lines.append(f'• [{label}]({url}) · {text}' if text else f'• [{label}]({url})')
            title = clip(self.repo, 140) + (' · Recent activity' if self.english else ' · 최근 활동')
            if len(rows) > PAGE_SIZE:
                title += f' ({page+1}/{(len(rows)+PAGE_SIZE-1)//PAGE_SIZE})'
            body = '\n'.join(lines)
            digest = hashlib.sha256((title + '\n' + body).encode()).hexdigest()
            key, event = 'activity:' + self.repo + ':' + str(feed.channel) + ':' + window, str(page)
            identity = receipt(feed, key, event)
            with feed.queue.connect() as db:
                old = db.execute('SELECT digest FROM activity_pages WHERE channel=? AND window=? AND page=?',
                                 (feed.channel, window, page)).fetchone()
                message = db.execute('SELECT message FROM alerts WHERE receipt=?', (identity,)).fetchone()
            if old and old[0] == digest:
                continue
            if not message or not message[0]:
                message_id = feed.send(key, event, '', title, body, rows[start][2], 0x0969DA)
                # A lost POST response may have left an older version on Discord.
                recovering = message is not None
            else:
                message_id, recovering = message[0], True
            if recovering:
                data = {
                    'content': '', 'allowed_mentions': {'parse': []},
                    'embeds': [{'title': title, 'description': body, 'url': rows[start][2],
                                'color': 0x0969DA, 'footer': {'text': identity}}]}
                try:
                    feed.api.call('PATCH', '/channels/' + feed.channel + '/messages/' + str(message_id), data)
                except ApiError as error:
                    if error.status != 404:
                        raise
                    with feed.queue.connect() as db:
                        db.execute('DELETE FROM alerts WHERE receipt=?', (identity,))
                    feed.send(key, event, '', title, body, rows[start][2], 0x0969DA)
            with feed.queue.connect() as db:
                db.execute('INSERT OR REPLACE INTO activity_pages VALUES(?,?,?,?)',
                           (feed.channel, window, page, digest))
