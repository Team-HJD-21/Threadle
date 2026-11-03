"""Atomic settings-change audit with a durable public-notification outbox."""
import datetime as dt
import json
import logging
import time

FIELDS={
'language':('알림 언어','Language'),'utc_offset':('시간대','Time zone'),
'review_hours':('열린 PR 재알림','Open PR reminders'),'batch_seconds':('일반 알림 묶기','Batching'),
'daily_digest':('일일 요약','Daily digest'),'digest_hour':('일일 요약 시각','Daily hour'),
'weekly_digest':('주간 요약','Weekly digest'),'digest_weekday':('주간 요약 요일','Weekly day'),
'weekly_digest_hour':('주간 요약 시각','Weekly hour'),'quiet':('방해금지 사용','Do Not Disturb'),
'quiet_start':('방해금지 시작','DND start'),'quiet_end':('방해금지 종료','DND end')}


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS settings_changes(id TEXT PRIMARY KEY,binding TEXT NOT NULL,audit_id INTEGER NOT NULL,guild TEXT NOT NULL,repo TEXT NOT NULL,actor TEXT NOT NULL,happened REAL NOT NULL,old TEXT NOT NULL,new TEXT NOT NULL,sent INTEGER NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0,next REAL NOT NULL DEFAULT 0)')
    db.execute('CREATE INDEX IF NOT EXISTS settings_changes_due ON settings_changes(sent,next,happened)')


def snapshot(config):
    from digest_settings import enabled
    o=config.get('team_notifications',{})
    result=dict(language=config.get('language','ko'),utc_offset=o.get('utc_offset',9),review_hours=o.get('review_hours',0),batch_seconds=o.get('batch_seconds',0),daily_digest=bool(enabled(o,'daily')),digest_hour=o.get('digest_hour',9),weekly_digest=bool(enabled(o,'weekly')),digest_weekday=o.get('digest_weekday',0),weekly_digest_hour=o.get('weekly_digest_hour',o.get('digest_hour',9)),quiet=bool(o.get('quiet',False)),quiet_start=o.get('quiet_start',22),quiet_end=o.get('quiet_end',8))
    return result


def exists(registry,identity):
    with registry.db() as db:return db.execute('SELECT 1 FROM settings_changes WHERE id=?',(identity,)).fetchone() is not None


def record(db,binding,identity,actor,old,new,audit_id):
    keys=[key for key in FIELDS if old[key]!=new[key]]
    db.execute('INSERT INTO settings_changes(id,binding,audit_id,guild,repo,actor,happened,old,new) VALUES(?,?,?,?,?,?,?,?,?)',(identity,binding.id,audit_id,str(binding.config['guild_id']),binding.repo,str(actor),time.time(),json.dumps({k:old[k] for k in keys}),json.dumps({k:new[k] for k in keys})))


def lines(old,new,english=False):
    def value(key,v):
        if isinstance(v,bool):return ('On' if v else 'Off') if english else ('사용' if v else '사용 안 함')
        if key=='utc_offset':return f'UTC{v:+d}'
        if key=='language':return 'EN' if v=='en' else 'KR'
        if key=='digest_weekday':return (['Mon','Tue','Wed','Thu','Fri','Sat','Sun'] if english else ['월','화','수','목','금','토','일'])[v]
        if key in ('digest_hour','weekly_digest_hour','quiet_start','quiet_end'):return f'{v:02}:00'
        if key=='review_hours':return (str(v)+'h') if v else ('Off' if english else '사용 안 함')
        if key=='batch_seconds':return (str(v)+'s') if v else ('Instant' if english else '즉시')
        return str(v)
    return ['• '+FIELDS[k][int(english)]+': `'+value(k,old[k])+'` → `'+value(k,new[k])+'`' for k in FIELDS if k in old and k in new]


def flush(service):
    with service.registry.db() as db:
        row=db.execute('SELECT id,binding,guild,repo,actor,happened,old,new FROM settings_changes WHERE sent=0 AND next<=? ORDER BY happened LIMIT 1',(time.time(),)).fetchone()
    if not row:return
    identity,binding_id,guild,repo,actor,happened,old,new=row
    try:
        binding=service.registry.get(binding_id,include_paused=True)
        if binding is None:
            with service.registry.db() as db:db.execute('UPDATE settings_changes SET sent=1 WHERE id=?',(identity,))
            return
        if service.registry.get(binding_id) is None:
            with service.registry.db() as db:db.execute('UPDATE settings_changes SET next=? WHERE id=?',(time.time()+60,identity))
            return
        from alert_feed import AlertFeed
        from team_notifications import allowed_time
        feed=AlertFeed(service.discord(),binding.config,service.queue(binding));feed.check()
        en=binding.config.get('language')=='en'
        description=f'<@{actor}> · <t:{int(happened)}:f>\n'+repo+'\n\n'+'\n'.join(lines(json.loads(old),json.loads(new),en))
        feed.send('settings-change:'+identity,'changed',('⚙️ Notification settings changed.' if en else '⚙️ 알림 설정이 변경됐어요.'),repo,description,'https://discord.com/channels/'+guild+'/'+str(binding.config['alert_channel']),0x0969DA,silent=allowed_time(time.time(),binding.config.get('team_notifications',{}))>time.time())
        with service.registry.db() as db:db.execute('UPDATE settings_changes SET sent=1 WHERE id=?',(identity,))
    except Exception as error:
        delay=getattr(error,'retry_after',None)
        try:delay=max(30,float(delay or 30))
        except (TypeError,ValueError):delay=30
        with service.registry.db() as db:db.execute('UPDATE settings_changes SET attempts=attempts+1,next=? WHERE id=?',(time.time()+delay,identity))
        logging.getLogger('settings-audit').warning('Settings notification retry event=%s error=%s',identity,type(error).__name__)


def history(registry,binding):
    en=binding.config.get('language')=='en'
    with registry.db() as db:
        rows=db.execute("SELECT a.discord_user,a.happened,c.old,c.new FROM connection_audit a LEFT JOIN settings_changes c ON c.audit_id=a.id WHERE a.binding=? AND a.action='configure' AND a.github_user='discord-command' ORDER BY a.id DESC LIMIT 10",(binding.id,)).fetchall()
    if not rows:return ('No settings command changes recorded.' if en else '기록된 /settings 변경이 없습니다.')
    heading='**'+binding.repo+'** · /settings '+('history' if en else '변경 기록')
    pages=[];current=heading
    for actor,happened,old,new in rows:
        stamp=int(dt.datetime.fromisoformat(happened.replace(' ','T')).replace(tzinfo=dt.timezone.utc).timestamp())
        text=f'\n\n<@{actor}> · <t:{stamp}:f>\n'
        text+='\n'.join(lines(json.loads(old),json.loads(new),en)) if old else ('Legacy record: before/after values were not recorded.' if en else '이전 기록: 변경 전후 값은 기록되지 않았습니다.')
        if len(current)+len(text)>1750:pages.append(current);current=heading
        current+=text
    pages.append(current)
    return pages
