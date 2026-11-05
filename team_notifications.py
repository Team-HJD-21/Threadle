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
              'weekly_digest_hour': (0, 23), 'digest_hour': (0, 23), 'digest_weekday': (0, 6), 'quiet_start': (0, 23), 'quiet_end': (0, 23)}
    for key, (low, high) in ranges.items():
        value = options.get(key, 0)
        if type(value) is not int or not low <= value <= high:
            raise ValueError('알림 시간 설정이 올바르지 않습니다.')
    if options.get('batch_seconds', 0) not in (0, 60, 300) or options.get('digest', 'off') not in ('off', 'daily', 'weekly'):
        raise ValueError('알림 주기를 확인해 주세요.')
    for key in ('daily_digest','weekly_digest'):
        if key in options and type(options[key]) is not bool:raise ValueError('요약 사용 여부를 확인해 주세요.')
    if type(options.get('quiet', False)) is not bool:
        raise ValueError('방해금지 시간 설정을 확인해 주세요.')
    if options.get('quiet') and options.get('quiet_start', 0) == options.get('quiet_end', 0):
        raise ValueError('방해금지 시간의 시작과 종료를 다르게 설정해 주세요.')
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
        self.feeds = {}
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
        from dnd_notifications import DndRoundup
        self.dnd=DndRoundup(self)

    def has_pending(self):
        with self.queue.connect() as db:
            return db.execute('SELECT 1 FROM notice_events WHERE sent=0 LIMIT 1').fetchone() is not None or self.dnd.has_pending()

    def feed(self, channel):
        channel = str(channel)
        if channel in self.feeds:
            return self.feeds[channel]
        scope = str(channel) if str(channel) != str(self.binding.config.get('alert_channel')) else ''
        config = dict(self.binding.config, alert_channel=str(channel), alert_scope=scope)
        self.feeds[channel] = AlertFeed(self.api, config, self.queue)
        return self.feeds[channel]

    def schedule(self, key, title, description, link, event_key, channels, critical=False, color=0x8957E5, bypass_dnd=False):
        seconds = 0 if critical else self.options.get('batch_seconds', 0)
        now = self.clock()
        due = (math.floor(now / seconds) + 1) * seconds if seconds else now
        if not bypass_dnd:due = allowed_time(due, self.options)
        for channel in set(channels):
            if due <= now:
                if critical:
                    self.feed(channel).send(key, event_key, '', title, description, link, color)
                else:
                    self.feed(channel).activity(self.binding.repo,
                        [(event_key, key, title, description, link, color)], 'live:' + str(int(now // 60)))
            else:
                bucket = str(int(due))
                with self.queue.connect() as db:
                    db.execute('INSERT OR IGNORE INTO notice_events(channel,event_key,topic,title,description,url,color,due,bucket) VALUES(?,?,?,?,?,?,?,?,?)',
                               (str(channel), event_key, key, title, description, link, color, due, bucket))

    def topic(self, topic, event, payload, event_key, thread, channels, dnd_until=None):
        from alert_policy import filter_alert
        visible = filter_alert(event, payload)
        if visible is None:
            return
        event, payload = visible
        if event != 'workflow_run':
            now=self.clock()
            until=dnd_until or allowed_time(now,self.options)
            if dnd_until or until>now:
                for index in range(len(payload.get('_events') or [None])):
                    self.dnd.record(topic,event_key+':part:'+str(index),thread,until)
                return
        label = {'pr': 'PR', 'issue': 'ISSUE', 'discussion': 'Discussion'}[topic.kind]
        title = f'{label} #{topic.number} · ' + (english_activity(event, payload) if self.english else activity(event, payload))
        link = f"https://discord.com/channels/{self.binding.config['guild_id']}/{thread}"
        description = (f'[Open thread]({link}) · [View on GitHub]({topic.url})' if self.english
                       else f'[스레드 확인하기]({link}) · [GitHub 원문]({topic.url})')
        critical = (event == 'workflow_run' or any(item['payload'].get('action')=='closed' or item['payload'].get('review',{}).get('state','').lower()=='changes_requested' for item in payload.get('_events',[])) or payload.get('action') == 'closed'
                    or payload.get('review', {}).get('state', '').lower() == 'changes_requested')
        self.schedule(topic.key, title, description, link, event_key, channels, critical, bypass_dnd=event=='workflow_run')

    def ci(self, repo, payload, event_key):
        run = payload['workflow_run']
        event_key = 'ci-result:' + str(run['id']) + ':' + str(run.get('run_attempt') or 1)
        if run.get('conclusion') not in ('failure', 'timed_out', 'startup_failure', 'action_required'):
            return
        self.feed(self.binding.config['alert_channel']).ci_detail(repo, payload, event_key)
        link = run['html_url']
        description = ('Branch: ' if self.english else '브랜치: ') + '`' + clip(run.get('head_branch', 'unknown'), 100) + '`\n'
        description += ('[View run on GitHub](' if self.english else '[GitHub 실행 결과](') + link + ')'
        channel = self.binding.config.get('ci_channel')
        if channel:
            description = ('[Open CI channel](' if self.english else '[CI 채널 확인하기](') + f'https://discord.com/channels/{self.binding.config["guild_id"]}/{channel}) · ' + description
        self.schedule(repo + ':ci:' + str(run['id']), ('CI failed · ' if self.english else 'CI 실패 · ') + clip(run.get('name', ''), 150),
                      description, link, event_key, [self.binding.config['alert_channel']], True, 0xD1242F, bypass_dnd=True)

    def flush(self):
        self.dnd.flush()
        with self.queue.connect() as db:
            due = db.execute('SELECT channel,bucket FROM notice_events WHERE sent=0 AND due<=? GROUP BY channel,bucket ORDER BY MIN(due) LIMIT 20', (self.clock(),)).fetchall()
        for channel, bucket in due:
            # Normal activity is never flushed during Do Not Disturb. CI uses its
            # independent immediate path; old queued CI rows can still be recovered.
            quiet = allowed_time(self.clock(), self.options) > self.clock()
            with self.queue.connect() as db:
                rows = db.execute('SELECT event_key,topic,title,description,url,color FROM notice_events WHERE sent=0 AND channel=? AND bucket=? ORDER BY rowid',
                                  (channel, bucket)).fetchall()
            critical = [row for row in rows if ':ci:' in row[1]]
            for event, topic, title, description, url, color in critical:
                self.feed(channel).send(topic, event, '', title, description, url, color)
            normal = [row for row in rows if ':ci:' not in row[1]] if not quiet else []
            if normal:
                self.feed(channel).activity(self.binding.repo, normal, 'batch:' + bucket)
            with self.queue.connect() as db:
                db.executemany('UPDATE notice_events SET sent=1 WHERE channel=? AND event_key=? AND topic=?',
                               [(channel, row[0], row[1]) for row in critical + normal])

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

    def digest(self, github, kind=None):
        from digest_settings import enabled
        if kind is None:
            for frequency in ('daily','weekly'):
                if enabled(self.options,frequency):self.digest(github,frequency)
            return
        if not enabled(self.options,kind) or allowed_time(self.clock(), self.options) > self.clock():
            return
        offset = self.options.get('utc_offset', 9) * 3600
        local = dt.datetime.fromtimestamp(self.clock() + offset, dt.timezone.utc)
        hour = self.options.get('weekly_digest_hour',self.options.get('digest_hour',9)) if kind=='weekly' else self.options.get('digest_hour',9)
        scheduled = local.replace(hour=hour, minute=0, second=0, microsecond=0)
        if (local < scheduled or scheduled.timestamp() - offset < self.options.get(kind+'_enabled_at',self.options.get('enabled_at', self.clock()))
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
            pr_title=clip(str(pr.get('title','')).replace('\n',' ').replace('@','@\u200b'),85)
            pr_title=re.sub(r'([\\`*_\[\]<>~|])',r'\\\1',pr_title)
            link=f'https://github.com/{self.binding.repo}/pull/{int(pr["number"])}'
            lines.append(f'• [#{pr["number"]} {pr_title}]({link})')
        url = f'https://github.com/{self.binding.repo}/pulls'
        if len(prs) > 15:
            lines.append(f'… {len(prs)-15} more PRs · /summary')
        def item_lines(items):
            return '\n'.join(f'• [#{item["number"]} {clip(item.get("title", ""), 85)}]({item["url"]})' for item in items) or ('None' if self.english else '없음')
        issue_embeds = [
            {'title': 'Oldest open issues' if self.english else '가장 오래된 미해결 Issue',
             'description': item_lines(oldest), 'color':0x2DA44E,
             'footer':{'text':f'{count} remaining issues…'}}]
        from notification_text import split_description,units
        pr_pages=split_description('\n'.join(lines),2700)
        cards=[{'title':title,'description':part,'url':url,'color':0x0969DA} for part in pr_pages]+issue_embeds
        groups=[];group=[];size=0
        for card in cards:
            weight=units(card.get('title',''))+units(card.get('description',''))+units(card.get('footer',{}).get('text',''))
            if group and size+weight>5200:groups.append(group);group=[];size=0
            group.append(card);size+=weight
        if group:groups.append(group)
        for index,cards in enumerate(groups):
            first=cards[0]
            self.feed(self.binding.config['alert_channel']).send('digest:'+self.binding.repo,period if index==0 else period+':page:'+str(index),('🧵 Here’s your team roundup.' if self.english else '🧵 오늘 확인할 개발 소식을 모아왔어요.'),first['title'],first['description'],url,first['color'],extra_embeds=cards[1:])
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
