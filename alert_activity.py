"""One compact, durable repository activity card per channel and time window."""
from collections import Counter
import hashlib
import json
import threading

from discord_threads import ApiError, clip, nonce

LOCKS = [threading.RLock() for _ in range(64)]
PAGE_SIZE = 10
LIFECYCLE_GAP = 300
LIFECYCLE_SPAN = 900


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
                CREATE TABLE IF NOT EXISTS activity_details(channel TEXT,window TEXT,topic TEXT,description TEXT,
                    PRIMARY KEY(channel,window,topic));
                CREATE TABLE IF NOT EXISTS activity_windows(channel TEXT,category TEXT,window TEXT,started REAL,updated REAL,
                    PRIMARY KEY(channel,category));
            ''')

    def lock(self):
        return LOCKS[int(hashlib.sha256((str(self.feed.queue.path) + ':' + str(self.feed.channel)).encode()).hexdigest(), 16) % len(LOCKS)]

    def lifecycle(self, item, category, now):
        """Send immediately; append a nearby closure to its durable card."""
        feed = self.feed
        with self.lock():
            with feed.queue.connect() as db:
                saved = db.execute('SELECT window FROM activity_events WHERE channel=? AND event=? AND topic=?',
                                   (feed.channel, item[0], item[1])).fetchone()
                group = self.repo + ':' + category + (':en' if self.english else ':ko')
                current = db.execute('SELECT window,started,updated FROM activity_windows WHERE channel=? AND category=?',
                                     (feed.channel, group)).fetchone()
                if saved:
                    window = saved[0]
                elif current and 0 <= now-current[2] <= LIFECYCLE_GAP and 0 <= now-current[1] < LIFECYCLE_SPAN:
                    window = current[0]
                    db.execute('UPDATE activity_windows SET updated=? WHERE channel=? AND category=?', (now, feed.channel, group))
                else:
                    window = 'lifecycle:' + category + ':' + nonce(self.repo + ':' + item[0] + ':' + item[1] + ':' + str(now))
                    db.execute('INSERT OR REPLACE INTO activity_windows VALUES(?,?,?,?,?)', (feed.channel, group, window, now, now))
            self.update([item], window)

    def adjacent_window(self, item):
        feed = self.feed
        event, topic = item[:2]
        fresh = 'adjacent:' + nonce(self.repo + ':' + str(self.english) + ':' + topic + ':' + event)
        with feed.queue.connect() as db:
            previous = db.execute(
                "SELECT window FROM activity_events WHERE channel=? AND topic=? "
                "AND window LIKE 'adjacent:%' ORDER BY rowid DESC LIMIT 1",
                (feed.channel, topic)).fetchone()
            if not previous:
                return fresh
            window = previous[0]
            key = 'activity:' + self.repo + ':' + str(feed.channel) + ':' + window
            message = db.execute('SELECT message FROM alerts WHERE receipt=?',
                                 (receipt(feed, key, '0'),)).fetchone()
        if message and message[0]:
            try:
                latest = feed.api.call('GET', f'/channels/{feed.channel}/messages?limit=1')
                if latest and latest[0]['id'] == message[0]:
                    return window
            except ApiError:
                pass
        return fresh

    def update(self, items, window, adjacent=False):
        """Persist before sending. Replays retain their original window across restarts."""
        feed = self.feed
        with self.lock():
            if adjacent and len({item[1] for item in items}) == 1:
                window = self.adjacent_window(items[0])
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
                    if selected.startswith('adjacent:'):
                        activities = Counter(activities)
                        if activity:
                            activities[activity] += 1
                    elif activity and activity not in activities:
                        activities.append(activity)
                    db.execute('INSERT INTO activity_items VALUES(?,?,?,?,?,?) ON CONFLICT(channel,window,topic) '
                               'DO UPDATE SET label=excluded.label,activities=excluded.activities,url=excluded.url',
                               (feed.channel, selected, topic, clip(label, 70), json.dumps(activities), url))
                    db.execute('INSERT INTO activity_events VALUES(?,?,?,?)', (feed.channel, event, topic, selected))
                    if selected.startswith('lifecycle:'):
                        db.execute('INSERT OR REPLACE INTO activity_details VALUES(?,?,?,?)',
                                   (feed.channel, selected, topic, description))
            for selected in sorted(windows):
                self.publish(selected)

    def render(self, window):
        from app_emojis import prefix
        from notification_text import units
        with self.feed.queue.connect() as db:
            rows = db.execute('SELECT i.label,i.activities,i.url,d.description FROM activity_items i '
                              'LEFT JOIN activity_details d ON d.channel=i.channel AND d.window=i.window AND d.topic=i.topic '
                              'WHERE i.channel=? AND i.window=? ORDER BY i.rowid', (self.feed.channel, window)).fetchall()
        lifecycle = window.startswith('lifecycle:')
        main_alert = not self.feed.config.get('alert_scope')
        title = clip(self.repo, 140) + (' · Recent activity' if self.english else ' · 최근 활동')
        color, separator, limit = 0x0969DA, '\n', PAGE_SIZE
        if lifecycle:
            category = window.split(':', 2)[1]
            title = clip(self.repo, 110) + ' · ' + category + ' · ' + str(len(rows)) + (' items' if self.english else '건')
            color = 0xD1242F if category == 'PR Closed' else 0x8957E5
            separator, limit = '\n\n', 5
        groups, group, size = [], [], 0
        for label, raw, url, details in rows:
            label = label.replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]')
            if lifecycle:
                emoji = prefix(self.feed.api, 'threadle_merge') if main_alert and category == 'PR Merged' else ''
                entry = f'{emoji}**[{label}]({url})**\n' + (details or '')
            else:
                activities = json.loads(raw)
                # Count distinct webhook events by activity kind.
                counts = Counter(activities)
                activities = list(counts)
                text = ', '.join(clip(item, 60) + (f' ×{counts[item]}' if counts[item] > 1 else '')
                                 for item in activities[:4])
                if len(activities) > 4:
                    text += f' +{len(activities)-4}'
                emoji = ''
                if label.startswith('PR #'):
                    if main_alert and any(item in ('Opened', 'Created') for item in activities[:4]):
                        emoji += prefix(self.feed.api, 'threadle_pr_sent')
                    if 'Review requested' in activities[:4]:
                        emoji += prefix(self.feed.api, 'threadle_review_please')
                entry = f'• {emoji}[{label}]({url})' + (f' · {text}' if text else '')
            if group and (len(group) >= limit or size + units(entry) + len(separator) > 3500):
                groups.append(group)
                group, size = [], 0
            group.append((entry, url))
            size += units(entry) + len(separator)
        if group:
            groups.append(group)
        return [(title + (f' ({page+1}/{len(groups)})' if len(groups) > 1 else ''),
                 separator.join(entry for entry, _ in group), group[0][1], color)
                for page, group in enumerate(groups)]

    def publish(self, window):
        feed = self.feed
        for page, (title, body, url, color) in enumerate(self.render(window)):
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
                message_id = feed.send(key, event, '', title, body, url, color)
                # A lost POST response may have left an older version on Discord.
                recovering = message is not None
            else:
                message_id, recovering = message[0], True
            if recovering:
                data = {
                    'content': '', 'allowed_mentions': {'parse': []},
                    'embeds': [{'title': title, 'description': body, 'url': url,
                                'color': color, 'footer': {'text': identity}}]}
                try:
                    feed.api.call('PATCH', '/channels/' + feed.channel + '/messages/' + str(message_id), data)
                except ApiError as error:
                    if error.status != 404:
                        raise
                    with feed.queue.connect() as db:
                        db.execute('DELETE FROM alerts WHERE receipt=?', (identity,))
                    feed.send(key, event, '', title, body, url, color)
            with feed.queue.connect() as db:
                db.execute('INSERT OR REPLACE INTO activity_pages VALUES(?,?,?,?)',
                           (feed.channel, window, page, digest))
