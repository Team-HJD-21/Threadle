"""One restart notice per connected Discord guild, independent of repo count."""
import logging
import re
import threading
import time
import uuid

from discord_threads import ApiError,nonce
from runtime_logging import log_exception
from team_notifications import allowed_time

LOG=logging.getLogger('restart-notices')


class RestartNotices:
    def __init__(self,service,run=None,clock=time.time):
        self.service=service;self.run_id=run or uuid.uuid4().hex;self.clock=clock
        self.captured=False;self.bot=None

    def capture(self):
        bindings=self.service.registry.active(include_paused=True)
        guilds={}
        for binding in bindings:
            guilds.setdefault(str(binding.config['guild_id']),[]).append(binding)
        with self.service.registry.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS restart_notices(run TEXT NOT NULL,guild TEXT NOT NULL,channel TEXT NOT NULL,language TEXT NOT NULL,silent INTEGER NOT NULL,message TEXT,status TEXT NOT NULL,created REAL NOT NULL,PRIMARY KEY(run,guild))')
            if not self.captured:
                now=self.clock()
                for guild,items in guilds.items():
                    selected=min(items,key=lambda b:(int(b.config.get('alert_channel') or b.config['channels']['issue']),b.id))
                    channel=str(selected.config.get('alert_channel') or selected.config['channels']['issue'])
                    silent=any(allowed_time(now,b.config.get('team_notifications',{}))>now for b in items)
                    db.execute('INSERT OR IGNORE INTO restart_notices VALUES(?,?,?,?,?,NULL,?,?)',
                               (self.run_id,guild,channel,selected.config.get('language','ko'),int(silent),'pending',now))
                db.execute('DELETE FROM restart_notices WHERE created<? AND run<>?',(now-7*86400,self.run_id))
        self.captured=True
        return guilds

    @staticmethod
    def embed(language,ready):
        english=language=='en'
        if ready:
            title='Threadle restarted' if english else 'Threadle 서버가 다시 시작됐어요'
            description=('Notification processing has resumed. Queued GitHub updates will continue to be processed.' if english
                         else '알림 처리를 다시 시작했어요. 대기 중인 GitHub 알림을 이어서 처리합니다.')
        else:
            title='Threadle is restarting' if english else 'Threadle 서버를 다시 시작하고 있어요'
            description=('Notifications may be briefly delayed while Threadle starts.' if english
                         else '시작하는 동안 알림 전달이 잠시 지연될 수 있어요.')
        return {'title':title,'description':description,'color':0x2DA44E if ready else 0x0969DA}

    def check(self,ready=False):
        guilds=self.capture()
        with self.service.registry.db() as db:
            rows=db.execute("SELECT guild,channel,language,silent,message,status FROM restart_notices WHERE run=? AND status NOT IN ('ready','skipped')",(self.run_id,)).fetchall()
        api=None
        for guild,channel,language,silent,message,status in rows:
            try:
                if guild not in guilds:
                    self.finish(guild,'skipped');continue
                api=api or self.service.discord()
                target=api.call('GET','/channels/'+channel)
                if str(target.get('guild_id'))!=guild or target.get('type') not in (0,5):
                    raise ValueError('Restart notice channel scope mismatch')
                if not message:
                    sent=api.call('POST','/channels/'+channel+'/messages',{
                        'embeds':[self.embed(language,False)],'allowed_mentions':{'parse':[]},
                        'nonce':nonce('threadle-restart:'+self.run_id+':'+guild),'enforce_nonce':True,
                        **({'flags':4096} if silent else {})})
                    message=str(sent['id'])
                    with self.service.registry.db() as db:
                        db.execute("UPDATE restart_notices SET message=?,status='starting' WHERE run=? AND guild=?",(message,self.run_id,guild))
                    LOG.info('Restart notice sent guild=%s channel=%s',guild,channel)
                if ready:
                    if not re.fullmatch(r'[0-9]+',message):raise ValueError('Invalid restart message identity')
                    self.bot=self.bot or api.call('GET','/users/@me')['id']
                    current=api.call('GET',f'/channels/{channel}/messages/{message}')
                    if str(current.get('author',{}).get('id'))!=str(self.bot) or str(current.get('channel_id',channel))!=channel:
                        raise ValueError('Restart notice message ownership mismatch')
                    api.call('PATCH',f'/channels/{channel}/messages/{message}',{
                        'embeds':[self.embed(language,True)],'allowed_mentions':{'parse':[]}})
                    self.finish(guild,'ready')
                    LOG.info('Restart notice updated guild=%s channel=%s',guild,channel)
            except ApiError as error:
                if error.status in (403,404):
                    self.finish(guild,'skipped')
                    LOG.warning('Restart notice unavailable guild=%s channel=%s status=%s',guild,channel,error.status)
                else:log_exception(LOG,'Restart notice will retry')
            except Exception:log_exception(LOG,'Restart notice will retry')
        with self.service.registry.db() as db:
            return bool(db.execute("SELECT 1 FROM restart_notices WHERE run=? AND status NOT IN ('ready','skipped')",(self.run_id,)).fetchone())

    def finish(self,guild,status):
        with self.service.registry.db() as db:db.execute('UPDATE restart_notices SET status=? WHERE run=? AND guild=?',(status,self.run_id,guild))

    def start(self,stop,ready):
        def worker():
            deadline=time.monotonic()+180
            while not stop.is_set() and time.monotonic()<deadline:
                try:
                    if not self.check(ready.is_set()):return
                except Exception:log_exception(LOG,'Restart announcement worker will retry')
                stop.wait(2 if not ready.is_set() else 10)
            if not stop.is_set():LOG.warning('Restart announcement retry window expired')
        thread=threading.Thread(target=worker,name='RestartNotices',daemon=True)
        thread.start()
        return thread
