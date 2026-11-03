"""Refresh owned starter cards from current GitHub state; remove explicitly deleted topics."""
import copy
from discord_threads import ApiError,Topic,clip,iso_timestamp,marker,root_description


from topic_state import state_label,card_color,status_field


def decorate(topic,embed,obj):
    result=copy.deepcopy(embed)
    state=state_label(topic.kind,obj)
    result['title']=clip(f'{topic.kind.upper()} #{topic.number} · '+str(obj.get('title',topic.title)),230)
    result['color']=card_color(topic.kind,obj)
    result['fields']=[field for field in result.get('fields',[]) if field.get('name')!='Current Status']+[status_field(topic.kind,obj)]
    if topic.kind=='pr' and obj.get('head') and obj.get('base'):
        value=Topic(topic.repo,topic.kind,topic.number,topic.title,topic.url,obj)
        result['description']=root_description(value)
    return result


def editable_embed(original):
    allowed={'title','description','url','color','footer','author','timestamp','fields','image','thumbnail'}
    result={key:copy.deepcopy(value) for key,value in original.items() if key in allowed}
    for key in ('image','thumbnail'):
        if key in result:result[key]={name:value for name,value in result[key].items() if name=='url'}
    for key in ('author','footer'):
        if key in result:result[key].pop('proxy_icon_url',None)
    return result


def starter_embed(topic):
    obj=topic.metadata
    embed={'url':topic.url,'author':{'name':clip(obj.get('user',{}).get('login','GitHub'),100)},'footer':{'text':topic.key}}
    if root_description(topic):embed['description']=root_description(topic)
    if iso_timestamp(obj.get('created_at')):embed['timestamp']=iso_timestamp(obj['created_at'])
    return decorate(topic,embed,obj)


class TopicRoots:
    def __init__(self,threads,github):
        self.threads=threads;self.github=github;self.snapshot=None;self.loaded=False
        queue=getattr(threads,'queue',None)
        from delivery import Queue
        self.queue=queue if isinstance(queue,Queue) else None
        queue=self.queue
        if queue is not None:
            with queue.connect() as db:
                db.execute('CREATE TABLE IF NOT EXISTS deleted_topics(topic TEXT PRIMARY KEY,cleaned INTEGER NOT NULL DEFAULT 0)')
                db.execute('CREATE TABLE IF NOT EXISTS topic_thread_names(topic TEXT PRIMARY KEY,name TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS topic_visuals(topic TEXT PRIMARY KEY,state TEXT NOT NULL,event TEXT NOT NULL)')

    def read(self,topic):
        if self.loaded:return self.snapshot
        if topic.kind=='discussion':
            owner,name=topic.repo.split('/')
            response=self.github.call('POST','/graphql',{'query':'query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){discussion(number:$number){title closed isAnswered}}}', 'variables':{'owner':owner,'name':name,'number':topic.number}})
            errors=response.get('errors',[])
            if errors and not all(error.get('type')=='NOT_FOUND' and error.get('path',[])[-1:]==['discussion'] for error in errors):raise RuntimeError('Could not read current Discussion status')
            self.snapshot=(response.get('data',{}).get('repository') or {}).get('discussion')
        else:
            path='pulls' if topic.kind=='pr' else 'issues'
            try:self.snapshot=self.github.call('GET',f'/repos/{topic.repo}/{path}/{topic.number}')
            except ApiError as error:
                if error.status!=404 and not (topic.kind=='issue' and error.status==410):raise
                self.snapshot=None
        self.loaded=True
        return self.snapshot

    def location(self,topic):
        if self.queue is not None:
            with self.queue.connect() as db:
                row=db.execute('SELECT channel,thread FROM links WHERE topic=?',(topic.key,)).fetchone()
                if row:return row
                row=db.execute('SELECT channel,message FROM root_index WHERE topic=?',(topic.key,)).fetchone()
                if row:return row
        channel=self.threads.channels.get(topic.kind)
        if not channel:return None
        for message in self.threads.messages(channel):
            if message.get('author',{}).get('id')==self.threads.bot['id'] and topic.key in marker(message):return channel,message['id']
        return None

    def update(self,topic,obj):
        location=self.location(topic)
        if not location:return
        channel,identity=location
        try:message=self.threads.api.call('GET',f'/channels/{channel}/messages/{identity}')
        except ApiError as error:
            if error.status!=404:raise
            return
        if message.get('author',{}).get('id')!=self.threads.bot['id'] or topic.key not in marker(message):raise ValueError('Starter message ownership mismatch')
        embeds=[editable_embed(embed) for embed in message.get('embeds',[])];changed=False
        for index,embed in enumerate(embeds):
            if embed.get('footer',{}).get('text','').split('\n',1)[0]!=topic.key:continue
            desired=decorate(topic,embed,obj)
            if desired!=embed:embeds[index]=desired;changed=True
        if changed:
            try:self.threads.api.call('PATCH',f'/channels/{channel}/messages/{identity}',{'embeds':embeds,'allowed_mentions':{'parse':[]}})
            except ApiError as error:
                if error.status!=404:raise

        self.update_thread_name(topic,obj,channel,identity)

    def update_thread_name(self,topic,obj,channel,identity):
        from topic_state import thread_title
        desired=thread_title(topic,obj)
        if self.queue is not None:
            with self.queue.connect() as db:
                previous=db.execute('SELECT name FROM topic_thread_names WHERE topic=?',(topic.key,)).fetchone()
                if previous and previous[0]==desired:return
        try:thread=self.threads.api.call('GET','/channels/'+identity)
        except ApiError as error:
            if error.status!=404:raise
            return
        if thread.get('parent_id')!=channel or thread.get('owner_id')!=self.threads.bot['id']:return
        if thread.get('name')!=desired:
            if thread.get('thread_metadata',{}).get('locked'):return
            self.threads.api.call('PATCH','/channels/'+identity,{'name':desired})
        if self.queue is not None:
            with self.queue.connect() as db:db.execute('INSERT OR REPLACE INTO topic_thread_names VALUES(?,?)',(topic.key,desired))

    def update_descriptions(self,topic,obj):
        if self.queue is None:return
        from discord_threads import nonce
        import json
        source='github-source:'+nonce(topic.key+':body')
        with self.queue.connect() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_sources'").fetchone():return
            current=db.execute('SELECT event FROM message_sources WHERE source=?',(source,)).fetchone()
            if not current:return
            visual=state_label(topic.kind,obj)+':'+str(card_color(topic.kind,obj))
            saved=db.execute('SELECT state,event FROM topic_visuals WHERE topic=?',(topic.key,)).fetchone()
            if saved==(visual,current[0]):return
            pages=db.execute('SELECT m.thread,p.message FROM message_sources m JOIN source_pages p ON p.source=m.source WHERE m.source=? ORDER BY p.page',(source,)).fetchall()
            if not pages:return
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='creation_snapshots'").fetchone():
                row=db.execute('SELECT value FROM creation_snapshots WHERE topic=?',(topic.key,)).fetchone()
                if row:
                    value=json.loads(row[0])
                    value.update({key:obj[key] for key in ('state','closed','draft','merged','merged_at','isAnswered','answer_chosen_at','answer') if key in obj})
                    db.execute('UPDATE creation_snapshots SET value=? WHERE topic=?',(json.dumps(value),topic.key))
        for thread,message_id in pages:
            try:message=self.threads.api.call('GET',f'/channels/{thread}/messages/{message_id}')
            except ApiError as error:
                if error.status!=404:raise
                continue
            if message.get('author',{}).get('id')!=self.threads.bot['id']:raise ValueError('Description message ownership mismatch')
            embeds=[editable_embed(e) for e in message.get('embeds',[])];changed=False
            for embed in embeds:
                # Mutable creation pages include image-only embeds; leave their content intact.
                if embed.get('color')!=card_color(topic.kind,obj):embed['color']=card_color(topic.kind,obj);changed=True
                for field in embed.get('fields',[]):
                    if field.get('name')=='Current Status' and field!=status_field(topic.kind,obj):field.update(status_field(topic.kind,obj));changed=True
            if changed:self.threads.api.call('PATCH',f'/channels/{thread}/messages/{message_id}',{'embeds':embeds,'allowed_mentions':{'parse':[]}})

        with self.queue.connect() as db:db.execute('INSERT OR REPLACE INTO topic_visuals VALUES(?,?,?)',(topic.key,visual,current[0]))

    def closed(self,topic):
        obj=self.read(topic)
        if obj is None:return True
        self.update(topic,obj)
        self.update_descriptions(topic,obj)
        return bool(obj.get('closed') or obj.get('state')=='closed' or (topic.kind=='pr' and (obj.get('merged') or obj.get('merged_at'))))

    def handle_deleted(self,topic,event,payload):
        if topic.kind not in ('issue','discussion') or self.queue is None:return False
        entries=[(row['event'],row['payload']) for row in payload.get('_events',[])] if event=='threadle_bundle' else [(event,payload)]
        expected='issues' if topic.kind=='issue' else 'discussion'
        explicit=any(kind==expected and row.get('action')=='deleted' for kind,row in entries)
        with self.queue.connect() as db:
            known=db.execute('SELECT cleaned FROM deleted_topics WHERE topic=?',(topic.key,)).fetchone()
        if not explicit and not known:return False
        obj=self.read(topic)
        if obj is not None:
            with self.queue.connect() as db:db.execute('DELETE FROM deleted_topics WHERE topic=?',(topic.key,))
            if explicit:self.update(topic,obj)
            return explicit # An obsolete delete event must not erase a restored/current topic.
        with self.queue.connect() as db:db.execute('INSERT OR IGNORE INTO deleted_topics(topic) VALUES(?)',(topic.key,))
        if not known or not known[0]:
            self.remove(topic)
            with self.queue.connect() as db:db.execute('UPDATE deleted_topics SET cleaned=1 WHERE topic=?',(topic.key,))
        return True

    def remove(self,topic):
        location=self.location(topic)
        if not location:return
        channel,identity=location;api=self.threads.api
        root=None;thread=None
        try:root=api.call('GET',f'/channels/{channel}/messages/{identity}')
        except ApiError as error:
            if error.status!=404:raise
        if root and (root.get('author',{}).get('id')!=self.threads.bot['id'] or topic.key not in marker(root)):raise ValueError('Refusing to delete an unrelated starter')
        try:thread=api.call('GET','/channels/'+identity)
        except ApiError as error:
            if error.status!=404:raise
        if thread and (thread.get('parent_id')!=channel or thread.get('owner_id')!=self.threads.bot['id']):raise ValueError('Refusing to delete an unrelated thread')
        if thread:
            try:api.call('DELETE','/channels/'+identity)
            except ApiError as error:
                if error.status!=404:raise
        if root:
            try:api.call('DELETE',f'/channels/{channel}/messages/{identity}')
            except ApiError as error:
                if error.status!=404:raise
        if self.queue is not None:
            with self.queue.connect() as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_sources'").fetchone():
                    sources=[row[0] for row in db.execute('SELECT source FROM message_sources WHERE thread=?',(identity,))]
                    for source in sources:
                        for table in ('source_pages','source_revisions','message_sources'):db.execute('DELETE FROM '+table+' WHERE source=?',(source,))
                db.execute('DELETE FROM links WHERE topic=?',(topic.key,))
                db.execute('DELETE FROM root_index WHERE topic=?',(topic.key,))
                db.execute('DELETE FROM deferred_threads WHERE topic=?',(topic.key,))
                # Do not send queued heads-ups or DND links for a thread that was deleted.
                for table in ('notice_events','dnd_activity'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table+' WHERE topic=?',(topic.key,))
