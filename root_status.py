"""Refresh owned starter cards from current GitHub state; remove explicitly deleted topics."""
import copy
from discord_threads import ApiError,Topic,clip,iso_timestamp,marker,root_description


def state_label(kind,obj):
    if kind=='pr' and (obj.get('merged') or obj.get('merged_at')):return 'Merged'
    if obj.get('state')=='closed' or obj.get('closed'):return 'Closed'
    if kind=='pr' and obj.get('draft'):return 'Draft'
    if kind=='discussion' and (obj.get('isAnswered') or obj.get('answer_chosen_at') or obj.get('answer')):return 'Answered'
    return 'Open'


def decorate(topic,embed,obj):
    result=copy.deepcopy(embed)
    state=state_label(topic.kind,obj)
    result['title']=clip(f'{topic.kind.upper()} {state} #{topic.number} · '+str(obj.get('title',topic.title)),230)
    result['color']={'Open':0x2DA44E,'Draft':0x8B949E,'Closed':0xD1242F,'Merged':0x8957E5,'Answered':0x2DA44E}[state]
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
            with queue.connect() as db:db.execute('CREATE TABLE IF NOT EXISTS deleted_topics(topic TEXT PRIMARY KEY,cleaned INTEGER NOT NULL DEFAULT 0)')

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

    def closed(self,topic):
        obj=self.read(topic)
        if obj is None:return True
        self.update(topic,obj)
        return state_label(topic.kind,obj) in ('Closed','Merged')

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
                # Do not send queued heads-ups or DND links for a thread that was deleted.
                for table in ('notice_events','dnd_activity'):
                    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():db.execute('DELETE FROM '+table+' WHERE topic=?',(topic.key,))
