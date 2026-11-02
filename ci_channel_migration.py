import json
import logging
import re
import threading

from discord_threads import ApiError
from guild_lifecycle import GuildLifecycle

LOG = logging.getLogger('ci-channel-migration')
VERSION = 'ci-cd-name-v1'


class CiChannelMigration:
    def __init__(self, service):
        self.service = service
        with service.registry.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS channel_migration_flags(version TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS channel_migration_guilds(guild TEXT PRIMARY KEY,scanned INTEGER NOT NULL DEFAULT 0)')
            db.execute('CREATE TABLE IF NOT EXISTS channel_migration_targets(channel TEXT PRIMARY KEY,guild TEXT NOT NULL,target TEXT NOT NULL,done INTEGER NOT NULL DEFAULT 0)')

    def check(self):
        api = self.service.discord()
        with self.service.registry.db() as db:
            captured=db.execute('SELECT 1 FROM channel_migration_flags WHERE version=?',(VERSION,)).fetchone()
        if not captured:
            guilds=GuildLifecycle.membership(api)
            with self.service.registry.db() as db:
                db.executemany('INSERT OR IGNORE INTO channel_migration_guilds(guild) VALUES(?)',[(guild,) for guild in guilds])
                db.execute('INSERT OR IGNORE INTO channel_migration_flags VALUES(?)',(VERSION,))
        with self.service.registry.db() as db:
            guilds=[row[0] for row in db.execute('SELECT guild FROM channel_migration_guilds WHERE scanned=0')]
            known={str(json.loads(row[0]).get('ci_channel')) for row in db.execute('SELECT config FROM bindings')}
        for guild in guilds:
            try:
                channels=api.call('GET','/guilds/'+guild+'/channels')
                targets=[]
                for row in channels:
                    channel=str(row.get('id',''))
                    managed=channel in known or bool(re.fullmatch(r'GitHub Notify managed channel \| layout=[0-9a-f]+; kind=ci',row.get('topic') or ''))
                    if row.get('type')==0 and channel.isdigit() and managed and row.get('name','').endswith('github-ci'):
                        targets.append((channel,guild,row['name'][:-len('github-ci')]+'github-ci-cd'))
                with self.service.registry.db() as db:
                    db.executemany('INSERT OR IGNORE INTO channel_migration_targets(channel,guild,target) VALUES(?,?,?)',targets)
                    db.execute('UPDATE channel_migration_guilds SET scanned=1 WHERE guild=?',(guild,))
            except ApiError as error:
                if error.status==404 and error.code==10004:
                    with self.service.registry.db() as db:
                        db.execute('UPDATE channel_migration_guilds SET scanned=1 WHERE guild=?',(guild,))
                else:
                    LOG.warning('CI channel scan will retry guild=%s status=%s',guild,error.status)
        with self.service.registry.db() as db:
            targets=db.execute('SELECT channel,guild,target FROM channel_migration_targets WHERE done=0').fetchall()
        for channel,guild,target in targets:
            try:
                current=api.call('GET','/channels/'+channel)
                if str(current.get('guild_id'))!=guild or current.get('type')!=0:
                    raise ValueError('Migration channel server/type mismatch')
                if current.get('name')!=target and current.get('name','').endswith('github-ci'):
                    api.call('PATCH','/channels/'+channel,{'name':target})
                # Mark custom renames as finished too; never overwrite them in a later restart.
                with self.service.registry.db() as db:
                    db.execute('UPDATE channel_migration_targets SET done=1 WHERE channel=?',(channel,))
            except ApiError as error:
                if error.status==404 and error.code==10003:
                    with self.service.registry.db() as db:
                        db.execute('UPDATE channel_migration_targets SET done=1 WHERE channel=?',(channel,))
                else:
                    LOG.warning('CI channel rename will retry channel=%s status=%s',channel,error.status)
        with self.service.registry.db() as db:
            pending=db.execute('SELECT count(*) FROM channel_migration_guilds WHERE scanned=0').fetchone()[0]
            pending+=db.execute('SELECT count(*) FROM channel_migration_targets WHERE done=0').fetchone()[0]
        return pending==0

    def run(self,stop):
        while not stop.is_set():
            try:
                if self.check():
                    return
            except Exception as error:
                LOG.warning('CI channel migration deferred error=%s',type(error).__name__)
            stop.wait(60)

    def start(self,stop):
        worker=threading.Thread(target=self.run,args=(stop,),daemon=True,name='ci-channel-migration')
        worker.start()
        return worker
