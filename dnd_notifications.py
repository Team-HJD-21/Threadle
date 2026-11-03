"""Durable end-of-DND roundups, independent of ordinary alert batching."""
import datetime as dt
import json
import re
from discord_threads import clip,nonce


def schema(queue):
    with queue.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS dnd_delivery_modes(delivery TEXT PRIMARY KEY,until REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS dnd_activity(period REAL NOT NULL,event TEXT NOT NULL,topic TEXT NOT NULL,
                kind TEXT NOT NULL,number INTEGER NOT NULL,title TEXT NOT NULL,thread TEXT NOT NULL,url TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(period,event,topic));
            CREATE INDEX IF NOT EXISTS dnd_pending ON dnd_activity(done,period,kind,number,event);
            CREATE TABLE IF NOT EXISTS dnd_roundups(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS dnd_migrations(id TEXT PRIMARY KEY);
        """)


def delivery_mode(queue,delivery,event,options,now):
    if event=='workflow_run':return None
    from team_notifications import allowed_time
    until=allowed_time(now,options)
    schema(queue)
    with queue.connect() as db:
        saved=db.execute('SELECT until FROM dnd_delivery_modes WHERE delivery=?',(delivery,)).fetchone()
        until=max(until if until>now else 0,saved[0] if saved else 0)
        if until:db.execute('INSERT INTO dnd_delivery_modes VALUES(?,?) ON CONFLICT(delivery) DO UPDATE SET until=excluded.until',(delivery,until))
    return until or None


class DndRoundup:
    def __init__(self,notices):
        self.notices=notices;self.queue=notices.queue
        schema(self.queue)
        self.migrate()

    def migrate(self):
        # Previously deferred topic heads-ups become one roundup; CI failures are released immediately.
        options=self.notices.options
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM dnd_migrations WHERE id='v1'").fetchone():return
            rows=db.execute('SELECT channel,event_key,topic,title,url,due FROM notice_events WHERE sent=0').fetchall()
            for channel,event,topic,title,url,due in rows:
                if ':ci:' in topic or 'CI failed' in title or 'CI 실패' in title:
                    db.execute('UPDATE notice_events SET due=?,bucket=? WHERE channel=? AND event_key=? AND topic=?',(self.notices.clock(),'dnd-ci-release:'+event,channel,event,topic));continue
                local=dt.datetime.fromtimestamp(due+options.get('utc_offset',9)*3600,dt.timezone.utc)
                match=re.fullmatch(r'github-thread:.+:(pr|issue|discussion):(\d+)',topic)
                if not options.get('quiet') or not match or (local.hour,local.minute,local.second)!=(options.get('quiet_end',8),0,0):continue
                db.execute('INSERT OR IGNORE INTO dnd_activity(period,event,topic,kind,number,title,thread,url) VALUES(?,?,?,?,?,?,?,?)',
                    (due,event,topic,match[1],int(match[2]),title,url,url))
                db.execute('UPDATE notice_events SET sent=1 WHERE channel=? AND event_key=? AND topic=?',(channel,event,topic))
            db.execute("INSERT INTO dnd_migrations VALUES('v1')")

    def record(self,topic,event_key,thread,until):
        link=f"https://discord.com/channels/{self.notices.binding.config['guild_id']}/{thread}"
        with self.queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO dnd_activity(period,event,topic,kind,number,title,thread,url) VALUES(?,?,?,?,?,?,?,?)',
                (until,event_key,topic.key,topic.kind,topic.number,clip(topic.title,140),link,topic.url))

    def has_pending(self):
        with self.queue.connect() as db:
            return db.execute('SELECT 1 FROM dnd_activity WHERE done=0 LIMIT 1').fetchone() is not None or db.execute('SELECT 1 FROM dnd_roundups LIMIT 1').fetchone() is not None

    def flush(self):
        n=self.notices
        from team_notifications import allowed_time
        if allowed_time(n.clock(),n.options)>n.clock():return
        with self.queue.connect() as db:
            if db.execute('SELECT 1 FROM deferred_thread_jobs d JOIN jobs j ON j.id=d.delivery WHERE j.done=0 LIMIT 1').fetchone():return
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            saved=db.execute('SELECT id,payload FROM dnd_roundups ORDER BY rowid LIMIT 1').fetchone()
            if saved:
                identity,raw=saved;batch=json.loads(raw)
            else:
                period=db.execute('SELECT min(period) FROM dnd_activity WHERE done=0 AND period<=?',(n.clock(),)).fetchone()[0]
                if period is None:return
                rows=db.execute('SELECT event,topic,kind,number,title,thread,url FROM dnd_activity WHERE done=0 AND period=? ORDER BY kind,number,event',(period,)).fetchall()
                batch={'period':period,'rows':rows}
                raw=json.dumps(batch,ensure_ascii=False,sort_keys=True);identity='dnd:'+nonce(raw)
                db.execute('INSERT INTO dnd_roundups VALUES(?,?)',(identity,raw))
        with self.queue.connect() as db:
            exists=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='deleted_topics'").fetchone()
            deleted={row[0] for row in db.execute('SELECT topic FROM deleted_topics')} if exists else set()
        topics={}
        for event,topic,kind,number,title,thread,url in batch['rows']:
            if topic in deleted:continue
            row=topics.setdefault(topic,{'kind':kind,'number':number,'title':title,'thread':thread,'count':0})
            row['count']+=1
        lines=[]
        for value in topics.values():
            title=clip(value['title'].replace('@','@\u200b'),80)
            title=re.sub(r'([\\`*_\[\]~|])',r'\\\1',title)
            label={'pr':'PR','issue':'Issue','discussion':'Discussion'}[value['kind']]
            count=value['count']
            lines.append(f"• [{label} #{value['number']} · {title}]({value['thread']}) · "+(f'{count} updates' if n.english else f'{count}건'))
        for index in range(0,len(lines),8):
            description='**'+n.binding.repo+'**\n\n'+'\n'.join(lines[index:index+8])
            n.feed(n.binding.config['alert_channel']).send('dnd-roundup:'+n.binding.repo,identity+':'+str(index//8),
                '🧵 Here’s what changed during Do Not Disturb.' if n.english else '🧵 방해금지 시간 동안 도착한 소식을 모아왔어요.',
                'Updates during Do Not Disturb' if n.english else '방해금지 시간 동안 업데이트된 항목',
                description,'https://github.com/'+n.binding.repo,0x0969DA)
        with self.queue.connect() as db:
            db.executemany('UPDATE dnd_activity SET done=1 WHERE period=? AND event=? AND topic=?',[(batch['period'],r[0],r[1]) for r in batch['rows']])
            db.execute('DELETE FROM dnd_roundups WHERE id=?',(identity,))
