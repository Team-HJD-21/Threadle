import datetime as dt
import json
import math
import re
import time

from alert_feed import AlertFeed, activity, english_activity
from discord_threads import clip, nonce
from repository_summary import open_prs, open_issue_digest


def validate_options(options):
    if not isinstance(options, dict):
        raise ValueError('알림 설정을 확인해 주세요.')
    ranges = {'review_hours': (0, 720), 'batch_seconds': (0, 300), 'utc_offset': (-12, 14),
              'digest_hour': (0, 23), 'digest_weekday': (0, 6), 'quiet_start': (0, 23), 'quiet_end': (0, 23)}
    for key, (low, high) in ranges.items():
        value = options.get(key, 0)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('알림 시간 설정이 올바르지 않습니다.')
    if options.get('batch_seconds', 0) not in (0, 60, 300) or options.get('digest', 'off') not in ('off', 'daily', 'weekly'):
        raise ValueError('알림 주기를 확인해 주세요.')
    if type(options.get('quiet', False)) is not bool:
        raise ValueError('조용한 시간 설정을 확인해 주세요.')
    if options.get('quiet') and options.get('quiet_start', 0) == options.get('quiet_end', 0):
        raise ValueError('조용한 시간의 시작과 종료를 다르게 설정해 주세요.')
    return options


def allowed_time(timestamp, options):
    if not options.get('quiet'):
        return timestamp
    offset = options.get('utc_offset', 9) * 3600
    local = dt.datetime.fromtimestamp(timestamp + offset, dt.timezone.utc)
    start, end = options.get('quiet_start', 22), options.get('quiet_end', 8)
    quiet = start <= local.hour < end if start < end else local.hour >= start or local.hour < end
    if not quiet:
        return timestamp
    next_time = local.replace(hour=end, minute=0, second=0, microsecond=0)
    if next_time <= local:
        next_time += dt.timedelta(days=1)
    return next_time.timestamp() - offset


class TeamNotifications:
    def __init__(self, binding, queue, api, clock=time.time):
        self.binding, self.queue, self.api, self.clock = binding, queue, api, clock
        self.options = validate_options(binding.config.get('team_notifications', {}))
        self.english = binding.config.get('language') == 'en'
        self.next_tick = 0
        self.next_flush = 0
        with queue.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS notice_events(channel TEXT NOT NULL, event_key TEXT NOT NULL,
                    topic TEXT NOT NULL, title TEXT NOT NULL, description TEXT NOT NULL, url TEXT NOT NULL,
                    color INTEGER NOT NULL, due REAL NOT NULL, bucket TEXT NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(channel,event_key,topic));
                CREATE TABLE IF NOT EXISTS review_waits(number INTEGER NOT NULL, reviewer TEXT NOT NULL,
                    requested REAL NOT NULL, generation TEXT NOT NULL, PRIMARY KEY(number,reviewer));
                CREATE TABLE IF NOT EXISTS team_observed(event_key TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS team_schedule(period TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS open_pr_waits(number INTEGER PRIMARY KEY, due REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS open_pr_policy(id INTEGER PRIMARY KEY, hours INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS pr_reminder_batches(id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            ''')
            if not self.options.get('review_hours'):
                db.execute('DELETE FROM review_waits')
                db.execute('DELETE FROM open_pr_waits')
            else:
                db.execute('DELETE FROM review_waits WHERE requested<?', (self.options.get('review_since', 0),))
            hours = self.options.get('review_hours', 0)
            previous = db.execute('SELECT hours FROM open_pr_policy WHERE id=1').fetchone()
            if previous and previous[0] != hours:
                db.execute('DELETE FROM open_pr_waits')
                db.execute('DELETE FROM pr_reminder_batches')
            if not hours:
                db.execute('DELETE FROM pr_reminder_batches')
            db.execute('INSERT OR REPLACE INTO open_pr_policy VALUES(1,?)', (hours,))

    def has_pending(self):
        with self.queue.connect() as db:
            return db.execute('SELECT 1 FROM notice_events WHERE sent=0 LIMIT 1').fetchone() is not None

    def feed(self, channel):
        scope = str(channel) if str(channel) != str(self.binding.config.get('alert_channel')) else ''
        config = dict(self.binding.config, alert_channel=str(channel), alert_scope=scope)
        return AlertFeed(self.api, config, self.queue)

    def schedule(self, key, title, description, link, event_key, channels, critical=False, color=0x8957E5):
        seconds = 0 if critical else self.options.get('batch_seconds', 0)
        now = self.clock()
        due = (math.floor(now / seconds) + 1) * seconds if seconds else now
        due = allowed_time(due, self.options)
        for channel in set(channels):
            if due <= now:
                self.feed(channel).send(key, event_key, ('🧵 Here’s your update: ' if self.english else '🧵 새 소식을 가져왔어요! ') + title, title, description, link, color)
            else:
                bucket = str(int(due))
                with self.queue.connect() as db:
                    db.execute('INSERT OR IGNORE INTO notice_events(channel,event_key,topic,title,description,url,color,due,bucket) VALUES(?,?,?,?,?,?,?,?,?)',
                               (str(channel), event_key, key, title, description, link, color, due, bucket))

    def topic(self, topic, event, payload, event_key, thread, channels):
        label = {'pr': 'PR', 'issue': 'ISSUE', 'discussion': 'Discussion'}[topic.kind]
        title = f'{label} #{topic.number} · ' + (english_activity(event, payload) if self.english else activity(event, payload))
        link = f"https://discord.com/channels/{self.binding.config['guild_id']}/{thread}"
        description = (f'[Open thread]({link}) · [View on GitHub]({topic.url})' if self.english
                       else f'[스레드 확인하기]({link}) · [GitHub 원문]({topic.url})')
        critical = (event == 'workflow_run' or any(item['payload'].get('action')=='closed' or item['payload'].get('review',{}).get('state','').lower()=='changes_requested' for item in payload.get('_events',[])) or payload.get('action') == 'closed'
                    or payload.get('review', {}).get('state', '').lower() == 'changes_requested')
        self.schedule(topic.key, title, description, link, event_key, channels, critical)

    def ci(self, repo, payload, event_key):
        run = payload['workflow_run']
        if run.get('event') == 'pull_request' or run.get('conclusion') != 'failure':
            return
        link = run['html_url']
        self.schedule(repo + ':ci:' + str(run['id']), ('CI failed · ' if self.english else 'CI 실패 · ') + clip(run.get('name', ''), 150),
                      ('[View run on GitHub](' if self.english else '[GitHub 실행 결과](') + link + ')', link, event_key, [self.binding.config['alert_channel']], True, 0xD1242F)

    def flush(self):
        with self.queue.connect() as db:
            due = db.execute('SELECT channel,topic,bucket FROM notice_events WHERE sent=0 AND due<=? GROUP BY channel,topic,bucket LIMIT 20', (self.clock(),)).fetchall()
        for channel, topic, bucket in due:
            if allowed_time(self.clock(), self.options) > self.clock():
                return
            with self.queue.connect() as db:
                rows = db.execute('SELECT event_key,title,description,url,color FROM notice_events WHERE sent=0 AND channel=? AND topic=? AND bucket=? ORDER BY event_key',
                                  (channel, topic, bucket)).fetchall()
            key = 'batch:' + nonce(json.dumps([r[0] for r in rows]))
            title = rows[0][1] if len(rows) == 1 else f'{len(rows)} updates' if self.english else f'알림 {len(rows)}건 모아보기'
            description = '\n'.join('• ' + clip(row[1], 150) for row in rows[:15]) + '\n\n' + rows[-1][2]
            if len(rows) > 15:
                description += (f'\n{len(rows)-15} more updates. Open the thread for full activity.' if self.english else f'\n외 {len(rows)-15}건 · 전체 활동은 스레드에서 확인하세요.')
            self.feed(channel).send(topic, key, ('🧵 Here’s your update: ' if self.english else '🧵 새 소식을 가져왔어요! ') + title, title, description, rows[-1][3], rows[-1][4])
            with self.queue.connect() as db:
                db.executemany('UPDATE notice_events SET sent=1 WHERE channel=? AND event_key=? AND topic=?', [(channel, r[0], topic) for r in rows])

    def observe(self, topic, event, payload, event_key):
        if topic.kind != 'pr' or not self.options.get('review_hours'):
            return
        pr = payload.get('pull_request', {})
        action = payload.get('action')
        with self.queue.connect() as db:
            if db.execute('SELECT 1 FROM team_observed WHERE event_key=?', (event_key,)).fetchone():
                return
            if pr.get('state') == 'closed' or pr.get('draft') or action == 'converted_to_draft':
                db.execute('DELETE FROM review_waits WHERE number=?', (topic.number,))
            elif event == 'pull_request' and action in ('review_requested', 'opened', 'reopened', 'ready_for_review'):
                reviewers = []
                if action == 'review_requested':
                    if payload.get('requested_reviewer', {}).get('login'):
                        reviewers.append('user:' + payload['requested_reviewer']['login'])
                    if payload.get('requested_team', {}).get('slug'):
                        reviewers.append('team:' + payload['requested_team']['slug'])
                else:
                    reviewers = ['user:' + r['login'] for r in pr.get('requested_reviewers', [])]
                    reviewers += ['team:' + r['slug'] for r in pr.get('requested_teams', [])]
                for reviewer in reviewers:
                    if reviewer != 'user:' + pr.get('user', {}).get('login', ''):
                        db.execute('INSERT INTO review_waits VALUES(?,?,?,?) ON CONFLICT(number,reviewer) DO UPDATE SET requested=excluded.requested,generation=excluded.generation',
                                   (topic.number, reviewer, self.clock(), event_key))
            elif event == 'pull_request' and action == 'review_request_removed':
                for key, kind, field in [('requested_reviewer', 'user:', 'login'), ('requested_team', 'team:', 'slug')]:
                    value = payload.get(key, {}).get(field)
                    if value:
                        db.execute('DELETE FROM review_waits WHERE number=? AND reviewer=?', (topic.number, kind + value))
            elif event == 'pull_request_review' and action == 'submitted':
                reviewer = payload.get('review', {}).get('user', {}).get('login', '')
                db.execute('DELETE FROM review_waits WHERE number=? AND reviewer=?', (topic.number, 'user:' + reviewer))
            db.execute('INSERT INTO team_observed VALUES(?)', (event_key,))

    def review_targets(self, number):
        channels = set()
        with self.queue.connect() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pr_routes'").fetchone()
            route = db.execute('SELECT channel,groups_json FROM pr_routes WHERE number=?', (number,)).fetchone() if exists else None
        if route:
            channels.update(str(g['channel']) for g in json.loads(route[1]))
            if route[0] == self.binding.config['channels']['pr']:
                channels.add(self.binding.config['alert_channel'])
        return channels or {self.binding.config['alert_channel']}

    def reminders(self, github):
        hours = self.options.get('review_hours', 0)
        now = self.clock()
        if not hours or allowed_time(now, self.options) > now:
            return
        interval = hours * 3600
        prs = open_prs(github, self.binding.repo)
        numbers = {pr['number'] for pr in prs}
        with self.queue.connect() as db:
            for (number,) in db.execute('SELECT number FROM open_pr_waits').fetchall():
                if number not in numbers:
                    db.execute('DELETE FROM open_pr_waits WHERE number=?', (number,))
            for pr in prs:
                number = pr['number']
                started = self.options.get('review_since', self.options.get('enabled_at', now))
                try:
                    created = dt.datetime.fromisoformat(pr['created_at'].replace('Z', '+00:00')).timestamp()
                    started = max(started, created)
                except (KeyError, ValueError, TypeError):
                    pass
                db.execute('INSERT OR IGNORE INTO open_pr_waits VALUES(?,?)', (number, started + interval))
        with self.queue.connect() as db:
            pending=db.execute('SELECT id,payload FROM pr_reminder_batches LIMIT 1').fetchone()
        if pending:
            self.send_reminder_batch(*pending,open_numbers=numbers)
            return
        due_prs=[]
        destinations={}
        for pr in prs:
            number = pr['number']
            with self.queue.connect() as db:
                due = db.execute('SELECT due FROM open_pr_waits WHERE number=?', (number,)).fetchone()[0]
            if due > now:
                continue
            due_prs.append((number,due))
            for channel in self.review_targets(number):
                title=clip(pr.get('title',''),150).replace('\\','\\\\').replace('[','\\[').replace(']','\\]')
                line=f'• [#{number} · {title}](https://github.com/{self.binding.repo}/pull/{number})'
                if pr.get('draft'):line+=' · Draft' if self.english else ' · 초안'
                destinations.setdefault(str(channel),[]).append(line)
        if not due_prs:
            return
        batch={'due':due_prs,'channels':destinations,'hours':hours,'english':self.english}
        raw=json.dumps(batch,sort_keys=True)
        identity=nonce(raw)
        with self.queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO pr_reminder_batches VALUES(?,?)',(identity,raw))
        self.send_reminder_batch(identity,raw,open_numbers=numbers)

    def send_reminder_batch(self, identity, raw, open_numbers=None):
        batch=json.loads(raw)
        en=batch['english'];hours=batch['hours']
        def still_open(line):
            match=re.match(r'• \[#(\d+) · ',line)
            return open_numbers is None or match is not None and int(match[1]) in open_numbers
        for channel,lines in sorted(batch['channels'].items()):
            count=sum(still_open(line) for line in lines)
            pages=[];current=[];size=0
            for line in lines:
                length=len(line.encode('utf-16-le'))//2+1
                if current and size+length>3000:
                    pages.append(current);current=[];size=0
                current.append(line);size+=length
            if current:pages.append(current)
            for index,page in enumerate(pages):
                page=[line for line in page if still_open(line)]
                if not page:continue
                ending=(f'I’ll check again in {hours} hours.' if en else f'{hours}시간 뒤에도 열려 있으면 다시 알려드릴게요.')
                self.feed(channel).send('open-pr-roundup:'+identity,str(index),
                    '🧵 Here are the PRs that are still open.' if en else '🧵 아직 열린 PR을 모아왔어요.',
                    self.binding.repo+' · '+(f'{count} open PRs' if en else f'열린 PR {count}개'),
                    '\n'.join(page)+'\n\n'+ending,'https://github.com/'+self.binding.repo+'/pulls',0xD29922)
        with self.queue.connect() as db:
            for number,due in batch['due']:
                db.execute('UPDATE open_pr_waits SET due=? WHERE number=? AND due=?',(self.clock()+hours*3600,number,due))
            db.execute('DELETE FROM pr_reminder_batches WHERE id=?',(identity,))

    def digest(self, github):
        kind = self.options.get('digest', 'off')
        if kind == 'off' or allowed_time(self.clock(), self.options) > self.clock():
            return
        offset = self.options.get('utc_offset', 9) * 3600
        local = dt.datetime.fromtimestamp(self.clock() + offset, dt.timezone.utc)
        scheduled = local.replace(hour=self.options.get('digest_hour', 9), minute=0, second=0, microsecond=0)
        if (local < scheduled or scheduled.timestamp() - offset < self.options.get('enabled_at', self.clock())
                or kind == 'weekly' and local.weekday() != self.options.get('digest_weekday', 0)):
            return
        period = kind + ':' + scheduled.isoformat()
        with self.queue.connect() as db:
            if db.execute('SELECT 1 FROM team_schedule WHERE period=?', (period,)).fetchone():
                return
        prs = open_prs(github, self.binding.repo)
        count, oldest = open_issue_digest(github, self.binding.repo)
        review = [p for p in prs if not p.get('draft') and (p.get('requested_reviewers') or p.get('requested_teams'))]
        title = ('Daily PR & issue digest' if kind == 'daily' else 'Weekly PR & issue digest') if self.english else ('일일' if kind == 'daily' else '주간') + ' PR · Issue 현황'
        drafts = sum(bool(p.get('draft')) for p in prs)
        lines = [(f'{len(prs)} open PRs · {len(review)} awaiting review · {drafts} drafts' if self.english else f'현재 열린 PR {len(prs)}개 · 리뷰 요청 대기 {len(review)}개 · 초안 {drafts}개'), '']
        for pr in sorted(prs, key=lambda p: p.get('updated_at', ''))[:15]:
            lines.append(f'• #{pr["number"]} {clip(pr.get("title", ""), 100)}')
        url = f'https://github.com/{self.binding.repo}/pulls'
        if len(prs) > 15:
            lines.append(f'… {len(prs)-15} more PRs · /summary')
        def item_lines(items):
            return '\n'.join(f'• [#{item["number"]} {clip(item.get("title", ""), 85)}]({item["url"]})' for item in items) or ('None' if self.english else '없음')
        issue_embeds = [
            {'title': 'Oldest open issues' if self.english else '가장 오래된 미해결 Issue',
             'description': item_lines(oldest), 'color':0x2DA44E,
             'footer':{'text':f'{count} remaining issues…'}}]
        self.feed(self.binding.config['alert_channel']).send('digest:'  + self.binding.repo, period, ('🧵 Here’s your team roundup.' if self.english else '🧵 오늘 확인할 개발 소식을 모아왔어요.'), title, clip('\n'.join(lines), 2900), url, 0x0969DA, extra_embeds=issue_embeds)
        with self.queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO team_schedule VALUES(?)', (period,))

    def tick(self, github):
        if self.clock() >= self.next_flush:
            self.next_flush = self.clock() + 2
            self.flush()
        if self.clock() < self.next_tick:
            return
        self.next_tick = self.clock() + 60
        self.reminders(github)
        self.digest(github)
