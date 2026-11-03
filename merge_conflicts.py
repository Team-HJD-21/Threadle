"""Shared repository conflict checks; durable urgent alerts per Discord binding."""
import logging
import threading
import time
from discord_threads import clip
from runtime_logging import log_exception

LOG=logging.getLogger('merge-conflicts')
QUERY='''query($owner:String!,$name:String!,$cursor:String){repository(owner:$owner,name:$name){databaseId pullRequests(first:100,after:$cursor,states:OPEN){nodes{number title mergeable headRefName baseRefName} pageInfo{hasNextPage endCursor}}}}'''


def key(binding):return f'{binding.auth}:{binding.installation}:{binding.repo_id}'


class ConflictMonitor:
    def __init__(self,service,clock=time.time):
        self.service=service;self.clock=clock
        with service.registry.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS conflict_scans(id TEXT PRIMARY KEY,due REAL NOT NULL,generation INTEGER NOT NULL DEFAULT 0,last REAL NOT NULL DEFAULT 0,retries INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS conflict_signals(id TEXT NOT NULL,delivery TEXT NOT NULL,received REAL NOT NULL,PRIMARY KEY(id,delivery));
            CREATE TABLE IF NOT EXISTS conflict_policy(id TEXT PRIMARY KEY);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if db.execute("INSERT OR IGNORE INTO conflict_policy VALUES('events-v1')").rowcount:
                db.execute('UPDATE conflict_scans SET due=1e30 WHERE retries=0 AND NOT EXISTS(SELECT 1 FROM conflict_signals s WHERE s.id=conflict_scans.id AND s.received>conflict_scans.last)')

    def signal(self,binding,delivery,event,payload):
        entries=[(r['event'],r['payload']) for r in payload.get('_events',[])] if event=='threadle_bundle' else [(event,payload)]
        if not any(e=='push' and str(p.get('ref','')).startswith('refs/heads/') or e=='pull_request' and p.get('action') in ('opened','reopened','synchronize','edited','closed','ready_for_review','converted_to_draft') for e,p in entries):return
        now=self.clock();identity=key(binding)
        with self.service.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM conflict_signals WHERE id=? AND delivery=?',(identity,delivery)).fetchone():return
            db.execute('INSERT INTO conflict_signals VALUES(?,?,?)',(identity,delivery,now))
            db.execute('INSERT INTO conflict_scans(id,due,generation) VALUES(?,?,1) ON CONFLICT(id) DO UPDATE SET due=max(last+10,min(due,excluded.due)),generation=generation+1,retries=0',(identity,now+3))

    def snapshot(self,binding):
        github=self.service.github(binding);owner,name=binding.repo.split('/');cursor=None;seen=set();rows=[]
        while True:
            result=github.call('POST','/graphql',{'query':QUERY,'variables':{'owner':owner,'name':name,'cursor':cursor}})
            repository=result.get('data',{}).get('repository')
            if result.get('errors') or not repository or repository.get('databaseId')!=binding.repo_id:raise RuntimeError('Conflict snapshot repository scope or GraphQL error')
            connection=repository['pullRequests'];page=connection['nodes']
            for row in page:
                if not isinstance(row,dict) or not isinstance(row.get('number'),int) or row['number']<=0 or row.get('mergeable') not in ('UNKNOWN','MERGEABLE','CONFLICTING'):raise ValueError('Invalid conflict snapshot')
            rows.extend(page)
            info=connection['pageInfo']
            if not info['hasNextPage']:return rows
            cursor=info.get('endCursor')
            if not cursor or cursor in seen:raise RuntimeError('Conflict snapshot cursor did not advance')
            seen.add(cursor)

    def publish(self,binding,row,episode,target,resolved=False):
        from alert_feed import AlertFeed
        from team_notifications import allowed_time
        from discord_threads import nonce,Topic
        queue=self.service.queue(binding);number=row['number'];config=dict(binding.config)
        if target=='thread':
            topic=Topic(binding.repo,'pr',number,'','')
            with queue.connect() as db:link=db.execute('SELECT thread FROM links WHERE topic=?',(topic.key,)).fetchone()
            if not link:return
            config.update(alert_channel=link[0],alert_scope='merge-conflict-thread:'+str(number))
        feed=AlertFeed(self.service.discord(),config,queue)
        with queue.connect() as db:previous=db.execute('SELECT message FROM merge_conflict_posts WHERE number=? AND episode=? AND target=?',(number,episode,target)).fetchone()
        if previous:return
        english=config.get('language')=='en';url=f'https://github.com/{binding.repo}/pull/{number}'
        def branch(value):return '`'+clip(str(value or 'unknown').replace('`','').replace('@','@\u200b'),140)+'`'
        description='**'+binding.repo+'**\n'+('Branch: ' if english else '브랜치: ')+branch(row.get('headRefName'))+' → '+branch(row.get('baseRefName'))+'\n\n'
        description+=('This PR conflicts with its base branch. Resolve the conflicts before merging.' if english else '대상 브랜치와 Merge conflict가 생겼어요. 충돌을 해결한 뒤 병합해 주세요.')
        description+=('\n[Open PR](' if english else '\n[PR 확인하기](')+url+')'
        source=binding.repo+':merge-conflict:'+str(number);event='conflict:'+str(number)+':'+str(episode)
        silent=target=='thread' and (resolved or allowed_time(self.clock(),config.get('team_notifications',{}))>self.clock())
        content='🧵 A PR has a merge conflict.' if english else '🧵 PR에서 Merge conflict를 발견했어요.'
        label='Conflict resolved' if resolved else 'Merge conflict'
        if resolved:
            content='🧵 The merge conflict has been resolved.' if english else '🧵 Merge conflict가 해결됐어요.'
            description=description.replace('This PR conflicts with its base branch. Resolve the conflicts before merging.','The merge conflict has been resolved.').replace('대상 브랜치와 Merge conflict가 생겼어요. 충돌을 해결한 뒤 병합해 주세요.','대상 브랜치와의 Merge conflict가 해결됐어요.')
        feed.send(source,event,content,clip(f"PR #{number} · {label} · "+row.get('title',''),230),description,url,0x2DA44E if resolved else 0xD1242F,silent=silent)
        if config.get('alert_scope'):source+=':channel:'+config['alert_scope']
        receipt='github-alert:'+nonce(source+':'+event)
        with queue.connect() as db:
            saved=db.execute('SELECT message FROM alerts WHERE receipt=?',(receipt,)).fetchone()
            if not saved or not saved[0]:raise RuntimeError('Conflict message receipt missing')
            db.execute('INSERT OR IGNORE INTO merge_conflict_posts VALUES(?,?,?,?,?,?,?)',(number,episode,target,feed.channel,saved[0],'resolved' if resolved else 'conflict',receipt))

    def update_posts(self,binding,number,episode,state):
        from discord_threads import ApiError,marker
        from root_status import editable_embed
        queue=self.service.queue(binding);api=self.service.discord()
        with queue.connect() as db:posts=db.execute('SELECT target,channel,message,receipt FROM merge_conflict_posts WHERE number=? AND episode=? AND state<>?',(number,episode,state)).fetchall()
        for target,channel,message,receipt in posts:
            try:
                current=api.call('GET',f'/channels/{channel}/messages/{message}')
                if not hasattr(self,'bot_id'):self.bot_id=api.call('GET','/users/@me')['id']
                if current.get('author',{}).get('id')!=self.bot_id or receipt not in marker(current):raise ValueError('Conflict message ownership mismatch')
                embeds=[editable_embed(e) for e in current.get('embeds',[])]
                label='Conflict resolved' if state=='resolved' else 'Closed'
                english=binding.config.get('language')=='en'
                content=('🧵 The merge conflict has been resolved.' if english else '🧵 Merge conflict가 해결됐어요.') if state=='resolved' else ('🧵 This PR is no longer open.' if english else '🧵 이 PR은 더 이상 열려 있지 않아요.')
                for embed in embeds:
                    parts=embed.get('title','').split(' · ',2)
                    embed['title']=' · '.join([parts[0],label,*parts[2:]])
                    embed['color']=0x2DA44E if state=='resolved' else 0x8B949E
                    embed['description']=embed.get('description','').replace('This PR conflicts with its base branch. Resolve the conflicts before merging.',('The merge conflict has been resolved.' if state=='resolved' else 'This PR is no longer open.')).replace('대상 브랜치와 Merge conflict가 생겼어요. 충돌을 해결한 뒤 병합해 주세요.',('대상 브랜치와의 Merge conflict가 해결됐어요.' if state=='resolved' else '이 PR은 더 이상 열려 있지 않아요.'))
                api.call('PATCH',f'/channels/{channel}/messages/{message}',{'content':content,'embeds':embeds,'allowed_mentions':{'parse':[]}})
            except ApiError as error:
                if error.status!=404:raise
            with queue.connect() as db:db.execute('UPDATE merge_conflict_posts SET state=? WHERE number=? AND episode=? AND target=?',(state,number,episode,target))

    def deliver(self,binding,rows):
        queue=self.service.queue(binding)
        with queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS merge_conflicts(number INTEGER PRIMARY KEY,conflicting INTEGER NOT NULL,episode INTEGER NOT NULL,sent INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS merge_conflict_posts(number INTEGER NOT NULL,episode INTEGER NOT NULL,target TEXT NOT NULL,channel TEXT NOT NULL,message TEXT NOT NULL,state TEXT NOT NULL,receipt TEXT NOT NULL,PRIMARY KEY(number,episode,target))')
            existing=db.execute('SELECT number,episode FROM merge_conflicts WHERE conflicting=1').fetchall()
        present={row['number'] for row in rows}
        for number,episode in existing:
            if number not in present:
                self.update_posts(binding,number,episode,'closed')
                with queue.connect() as db:db.execute('UPDATE merge_conflicts SET conflicting=0,sent=0 WHERE number=?',(number,))
        for row in rows:
            if self.service.registry.get(binding.id)!=binding:return
            number=row['number'];state=row['mergeable']
            if state=='UNKNOWN':continue
            with queue.connect() as db:
                previous=db.execute('SELECT conflicting,episode,sent FROM merge_conflicts WHERE number=?',(number,)).fetchone()
            if state=='MERGEABLE':
                if previous:
                    self.publish(binding,row,previous[1],'thread',resolved=True)
                    self.update_posts(binding,number,previous[1],'resolved')
                    with queue.connect() as db:db.execute('UPDATE merge_conflicts SET conflicting=0,sent=0 WHERE number=?',(number,))
                continue
            episode=(previous[1] if previous and previous[0] else (previous[1] if previous else 0)+1)
            with queue.connect() as db:
                db.execute('INSERT INTO merge_conflicts VALUES(?,1,?,0) ON CONFLICT(number) DO UPDATE SET conflicting=1,episode=excluded.episode',(number,episode))
            self.publish(binding,row,episode,'alert')
            self.publish(binding,row,episode,'thread')
            with queue.connect() as db:db.execute('UPDATE merge_conflicts SET sent=1 WHERE number=? AND episode=?',(number,episode))

    def tick(self):
        now=self.clock()
        with self.service.registry.db() as db:
            pending=db.execute('SELECT id,generation,retries FROM conflict_scans WHERE due<=? ORDER BY due',(now,)).fetchall()
        if not pending:return
        groups={}
        for binding in self.service.registry.active():
            if binding.config.get('alert_channel'):groups.setdefault(key(binding),[]).append(binding)
        with self.service.registry.db() as db:
            due=[row for row in pending if row[0] in groups][:3]
            db.execute('DELETE FROM conflict_signals WHERE received<?',(now-7*86400,))
        for identity,generation,retries in due:
            try:
                rows=self.snapshot(groups[identity][0]);failed=False
                for binding in groups[identity]:
                    if self.service.registry.get(binding.id)!=binding:continue
                    try:self.deliver(binding,rows)
                    except Exception as error:
                        failed=True;log_exception(LOG,'Conflict alert will retry binding=%s error=%s',binding.id,type(error).__name__,level=logging.WARNING)
                unknown=any(row['mergeable']=='UNKNOWN' for row in rows)
                delay=min(900,30*2**min(retries,5)) if failed else 30 if unknown and retries<5 else None
                attempts=retries+1 if failed or unknown else 0
            except Exception as error:
                attempts=retries+1;delay=min(900,30*2**min(retries,5))
                log_exception(LOG,'Conflict scan will retry scope=%s error=%s',identity,type(error).__name__,level=logging.WARNING)
            with self.service.registry.db() as db:
                db.execute('UPDATE conflict_scans SET due=?,last=?,retries=? WHERE id=? AND generation=?',(self.clock()+delay if delay is not None else 1e30,self.clock(),attempts,identity,generation))
                db.execute('UPDATE conflict_scans SET last=?,due=max(due,?) WHERE id=? AND generation<>?',(self.clock(),self.clock()+10,identity,generation))

    def run(self,stop):
        while not stop.is_set():
            try:self.tick()
            except Exception as error:log_exception(LOG,'Conflict monitor will retry error=%s',type(error).__name__,level=logging.WARNING)
            stop.wait(5)

    def start(self,stop):
        worker=threading.Thread(target=self.run,args=(stop,),daemon=True,name='merge-conflicts');worker.start();return worker
