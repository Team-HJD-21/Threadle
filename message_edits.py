import copy
import datetime as dt
import re

from discord_threads import Api,ApiError,Topic,make_embed,nonce
from notification_layout import CREATION,creation_embed,merge_snapshot,message_pages
from notification_media import attach_public_images

COMMENTS={'issue_comment','discussion_comment','pull_request_review_comment'}
FOLDED={'assigned','unassigned','labeled','unlabeled','milestoned','demilestoned','review_requested','review_request_removed','typed','untyped','field_added','field_removed'}


def identity(event,payload):
    action=payload.get('action')
    if event in CREATION and action in ('opened','created','edited'):
        return 'body',payload.get('pull_request') or payload.get('issue') or payload.get('discussion') or {}
    obj=payload.get('comment') if event in COMMENTS and action in ('created','edited','deleted') else payload.get('review') if event=='pull_request_review' and action in ('submitted','edited') else None
    if obj is not None:
        value=obj.get('id') or obj.get('node_id')
        if value is not None and re.fullmatch(r'[A-Za-z0-9_=-]{1,200}',str(value)):
            return ('review:' if event=='pull_request_review' else 'comment:'+event+':')+str(value),obj
    return None,None


def revision(obj):
    value=obj.get('updated_at') or obj.get('updatedAt') or obj.get('submitted_at') or obj.get('created_at')
    try:return dt.datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
    except (ValueError,TypeError,OverflowError):return 0


class MutableMessages:
    def __init__(self,threads):
        self.threads=threads;self.queue=threads.queue
        with self.queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS message_sources(source TEXT PRIMARY KEY,thread TEXT NOT NULL,revision REAL NOT NULL,event TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS deleted_sources(source TEXT PRIMARY KEY,done INTEGER NOT NULL DEFAULT 0,thread TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS source_delete_pages(source TEXT NOT NULL,message TEXT NOT NULL,PRIMARY KEY(source,message))')
            db.execute('CREATE TABLE IF NOT EXISTS source_revisions(source TEXT NOT NULL,event TEXT NOT NULL,PRIMARY KEY(source,event))')
            db.execute('CREATE TABLE IF NOT EXISTS source_pages(source TEXT NOT NULL,page INTEGER NOT NULL,message TEXT NOT NULL,media TEXT,PRIMARY KEY(source,page))')

    def recover(self,topic,source,thread,kind,obj):
        found={};legacy=[];latest_version=0
        for message in self.threads.messages(thread):
            if message.get('author',{}).get('id')!=self.threads.bot['id']:continue
            embeds=message.get('embeds',[])
            for embed in embeds:
                for line in embed.get('footer',{}).get('text','').splitlines():
                    if line.startswith(source+':page:') and line.rsplit(':',1)[-1].isdigit():
                        index=int(line.rsplit(':',1)[-1])
                        if index in found and found[index]!=message['id']:raise ValueError('Ambiguous source message recovery')
                        found[index]=message['id']
                        for tag in embed.get('footer',{}).get('text','').splitlines():
                            if tag.startswith(source+':revision:'):
                                try:latest_version=max(latest_version,float(tag.rsplit(':',1)[1]))
                                except ValueError:pass
            # Old mixed-message/image groups are deliberately not adopted: editing them could erase another source.
            if kind!='body' and not obj.get('html_url'):continue
            url=obj.get('html_url') or topic.url
            if any('github-source:' in e.get('footer',{}).get('text','') for e in embeds):continue
            if not embeds or message.get('attachments') or any(e.get('image') or e.get('url')!=url for e in embeds):continue
            if kind=='body' and not all(e.get('fields') and e.get('title','').startswith(topic.kind.upper()+' #'+str(topic.number)+' · ') for e in embeds):continue
            legacy.append(message)
        if found:
            with self.queue.connect() as db:
                db.executemany('INSERT OR IGNORE INTO source_pages VALUES(?,?,?,NULL)',[(source,index,message) for index,message in found.items()])
        elif legacy:
            original=min(legacy,key=lambda row:int(row['id']))
            with self.queue.connect() as db:db.execute('INSERT OR IGNORE INTO source_pages VALUES(?,0,?,NULL)',(source,original['id']))
        return latest_version

    def is_deleted(self,topic,kind):
        source='github-source:'+nonce(topic.key+':'+kind)
        with self.queue.connect() as db:return db.execute('SELECT 1 FROM deleted_sources WHERE source=?',(source,)).fetchone() is not None

    def recover_delete(self,topic,source,thread,obj):
        from root_status import editable_embed
        url=obj.get('html_url')
        for message in self.threads.messages(thread):
            if message.get('author',{}).get('id')!=self.threads.bot['id']:continue
            embeds=message.get('embeds',[])
            tags=[line for embed in embeds for line in embed.get('footer',{}).get('text','').splitlines() if line.startswith('github-source:')]
            own=any(line.startswith(source+':page:') for line in tags)
            if own:
                with self.queue.connect() as db:db.execute('INSERT OR IGNORE INTO source_delete_pages VALUES(?,?)',(source,message['id']))
                continue
            if tags or not url:continue
            removed=[];active=False
            for index,embed in enumerate(embeds):
                if embed.get('url')==url:active=True;removed.append(index)
                elif active and embed.get('image') and not any(embed.get(key) for key in ('url','title','description','fields','author')):removed.append(index)
                else:active=False
            if not removed:continue
            kept=[editable_embed(embed) for index,embed in enumerate(embeds) if index not in removed]
            if not kept:
                with self.queue.connect() as db:db.execute('INSERT OR IGNORE INTO source_delete_pages VALUES(?,?)',(source,message['id']))
                continue
            # Old mixed-comment messages are edited surgically rather than deleting other comments.
            footer=embeds[-1].get('footer')
            if footer and not kept[-1].get('footer'):kept[-1]['footer']={key:value for key,value in footer.items() if key!='proxy_icon_url'}
            def image_urls(items):
                return {value for embed in items for key in ('image','thumbnail') for name,value in embed.get(key,{}).items() if name in ('url','proxy_url')}
            deleted_urls=image_urls([embed for index,embed in enumerate(embeds) if index in removed])
            kept_urls=image_urls([embed for index,embed in enumerate(embeds) if index not in removed])
            attachments=[]
            for attachment in message.get('attachments',[]):
                urls={attachment.get('url'),attachment.get('proxy_url'),'attachment://'+attachment.get('filename','')}
                if not (urls&deleted_urls) or urls&kept_urls:attachments.append({'id':attachment['id']})
            self.threads.api.call('PATCH',f"/channels/{thread}/messages/{message['id']}",{'embeds':kept,'attachments':attachments,'allowed_mentions':{'parse':[]}})

    def delete(self,topic,kind,obj):
        source='github-source:'+nonce(topic.key+':'+kind)
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO deleted_sources(source) VALUES(?)',(source,))
            done,saved_thread=db.execute('SELECT done,thread FROM deleted_sources WHERE source=?',(source,)).fetchone()
            if done:return
            current=db.execute('SELECT thread FROM message_sources WHERE source=?',(source,)).fetchone()
            linked=db.execute('SELECT thread FROM links WHERE topic=?',(topic.key,)).fetchone()
            thread=str(current[0] if current else linked[0] if linked else saved_thread) if current or linked or saved_thread else None
            db.execute('INSERT OR IGNORE INTO source_delete_pages SELECT source,message FROM source_pages WHERE source=?',(source,))
        if not thread:
            from root_status import TopicRoots
            location=TopicRoots(self.threads,None).location(topic)
            thread=str(location[1]) if location else None
        if thread:
            with self.queue.connect() as db:db.execute('UPDATE deleted_sources SET thread=? WHERE source=?',(thread,source))
            try:self.recover_delete(topic,source,thread,obj)
            except ApiError as error:
                if error.status!=404:raise
        with self.queue.connect() as db:pages=db.execute('SELECT message FROM source_delete_pages WHERE source=?',(source,)).fetchall()
        for (identity,) in pages if thread else []:
            try:
                message=self.threads.api.call('GET',f'/channels/{thread}/messages/{identity}')
                if message.get('author',{}).get('id')!=self.threads.bot['id']:raise ValueError('Refusing to delete another author comment message')
                tags=[line for embed in message.get('embeds',[]) for line in embed.get('footer',{}).get('text','').splitlines() if line.startswith('github-source:')]
                if tags and not any(line.startswith(source+':page:') for line in tags):raise ValueError('Comment message source mismatch')
                self.threads.api.call('DELETE',f'/channels/{thread}/messages/{identity}')
            except ApiError as error:
                if error.status!=404:raise
        with self.queue.connect() as db:
            for table in ('source_delete_pages','source_pages','source_revisions','message_sources'):db.execute('DELETE FROM '+table+' WHERE source=?',(source,))
            db.execute('UPDATE deleted_sources SET done=1 WHERE source=?',(source,))

    def update(self,topic,kind,obj,event,payload,event_key,embed,closed):
        if self.is_deleted(topic,kind):return None
        threads=self.threads;thread=threads.ensure(topic);thread_id=str(thread['id'])
        if thread.get('thread_metadata',{}).get('locked'):raise RuntimeError('The topic thread is moderator-locked')
        source='github-source:'+nonce(topic.key+':'+kind)
        version=revision(obj)
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=db.execute('SELECT revision,event FROM message_sources WHERE source=?',(source,)).fetchone()
            known=db.execute('SELECT 1 FROM source_revisions WHERE source=? AND event=?',(source,event_key)).fetchone()
            if current and (version<current[0] or known and current[1]!=event_key or payload.get('action') in ('opened','created','submitted') and current[1]!=event_key):
                return thread_id
            db.execute('INSERT OR IGNORE INTO source_revisions VALUES(?,?)',(source,event_key))
            db.execute('INSERT INTO message_sources VALUES(?,?,?,?) ON CONFLICT(source) DO UPDATE SET thread=excluded.thread,revision=excluded.revision,event=excluded.event',(source,thread_id,version,event_key))
            mapped=db.execute('SELECT 1 FROM source_pages WHERE source=?',(source,)).fetchone()
        if not mapped:
            recovered_version=self.recover(topic,source,thread_id,kind,obj)
            if recovered_version>version:
                with self.queue.connect() as db:db.execute('UPDATE message_sources SET revision=? WHERE source=?',(recovered_version,source))
                return thread_id
        if thread.get('thread_metadata',{}).get('archived'):threads.api.call('PATCH','/channels/'+thread_id,{'archived':False})
        pages=message_pages(embed)
        for index,items in enumerate(pages):
            receipt='github-event:'+nonce(topic.key+':'+event_key+':'+source+':page:'+str(index))
            origin=next((e['image']['url'] for e in items if e.get('image')),None)
            if threads.delivered(thread_id,receipt):
                with self.queue.connect() as db:
                    saved=db.execute('SELECT message FROM thread_receipts WHERE receipt=?',(receipt,)).fetchone()
                    if saved and saved[0]:db.execute('INSERT INTO source_pages VALUES(?,?,?,?) ON CONFLICT(source,page) DO UPDATE SET message=excluded.message,media=excluded.media',(source,index,saved[0],origin))
                continue
            with self.queue.connect() as db:row=db.execute('SELECT message,media FROM source_pages WHERE source=? AND page=?',(source,index)).fetchone()
            existing=None
            if row:
                try:existing=threads.api.call('GET',f'/channels/{thread_id}/messages/{row[0]}')
                except ApiError as error:
                    if error.status!=404:raise
                if existing and existing.get('author',{}).get('id')!=threads.bot['id']:raise ValueError('Refusing to edit another author message')
            embeds=copy.deepcopy(items)
            embeds[-1]['footer']={'text':receipt+'\n'+source+':page:'+str(index)+'\n'+source+':revision:'+str(version)}
            keep=[];files=[]
            if existing and origin and row[1]==origin and existing.get('attachments'):
                image=next((e.get('image') for e in existing.get('embeds',[]) if e.get('image')),None)
                if image:
                    for e in embeds:
                        if e.get('image'):e['image']={'url':image['url']}
                    keep=[{'id':a['id']} for a in existing['attachments']]
            if not keep and isinstance(threads.api,Api):files=attach_public_images(embeds,threads.media_fetcher)
            data={'embeds':embeds,'allowed_mentions':{'parse':[]},'attachments':keep,**({'_files':files} if files else {})}
            if existing:
                try:sent=threads.api.call('PATCH',f'/channels/{thread_id}/messages/{row[0]}',data)
                except ApiError as error:
                    if error.status!=404:raise
                    existing=None
            if not existing:
                sent=threads.api.call('POST',f'/channels/{thread_id}/messages',dict(data,nonce=nonce(thread_id+":"+receipt),enforce_nonce=True,**({'flags':1<<12} if threads.notifications_suppressed() or current or payload.get('action')=='edited' else {})))
            with self.queue.connect() as db:db.execute('INSERT INTO source_pages VALUES(?,?,?,?) ON CONFLICT(source,page) DO UPDATE SET message=excluded.message,media=excluded.media',(source,index,sent['id'],origin))
            threads.remember_delivery(receipt,sent['id'])
        with self.queue.connect() as db:surplus=db.execute('SELECT page,message FROM source_pages WHERE source=? AND page>=?',(source,len(pages))).fetchall()
        for page,message in surplus:
            try:
                existing=threads.api.call('GET',f'/channels/{thread_id}/messages/{message}')
                if existing.get('author',{}).get('id')!=threads.bot['id']:raise ValueError('Refusing to remove another author message')
                threads.api.call('DELETE',f'/channels/{thread_id}/messages/{message}')
            except ApiError as error:
                if error.status!=404:raise
            with self.queue.connect() as db:db.execute('DELETE FROM source_pages WHERE source=? AND page=?',(source,page))
        if closed():threads.api.call('PATCH','/channels/'+thread_id,{'archived':True})
        return thread_id


def deliver_events(threads,topic,event,payload,event_key,closed,language):
    from delivery import CachedThreads
    if not isinstance(threads,CachedThreads):return threads.deliver(topic,make_embed(topic,event,payload,language),event_key,closed)
    entries=[(row['event'],row['payload']) for row in payload['_events']] if event=='threadle_bundle' else [(event,payload)]
    sources={};remaining=[]
    mutable=None
    # Deletions win over late creates/edits, including entries bundled out of order.
    for kind,value in entries:
        if kind in COMMENTS and value.get('action')=='deleted':
            key,obj=identity(kind,value)
            if key is None:raise ValueError('Deleted comment identity missing')
            if mutable is None:mutable=MutableMessages(threads)
            mutable.delete(topic,key,obj)
    created=any(e in CREATION and value.get('action') in ('opened','created') for e,value in entries)
    for index,(kind,value) in enumerate(entries):
        key,obj=identity(kind,value)
        if key:
            if mutable is None:mutable=MutableMessages(threads)
            if mutable.is_deleted(topic,key):continue
            previous=sources.get(key)
            if previous and (revision(obj)<revision(previous[2]) or value.get('action') in ('opened','created','submitted') and previous[1].get('action')=='edited'):continue
            sources[key]=(kind,value,obj)
        elif created and kind in CREATION and value.get('action') in FOLDED:
            continue
        else:remaining.append({'event':kind,'payload':value})
    if not sources:
        if not remaining:return None
        if mutable is None:return threads.deliver(topic,make_embed(topic,event,payload,language),event_key,closed)
        rest=dict(payload,_events=remaining,_created=False)
        return threads.deliver(topic,make_embed(topic,'threadle_bundle',rest,language),event_key,closed)
    thread=None
    for source,(kind,value,obj) in sources.items():
        if source=='body':
            merged=merge_snapshot(topic,entries)
            for field in ('body','title','created_at','updated_at'):
                if field in obj:merged.metadata[field]=obj[field]
            merged=Topic(merged.repo,merged.kind,merged.number,merged.metadata.get('title',merged.title),merged.url,merged.metadata)
            rendered=creation_embed(merged,value,language)
        else:
            rendered=make_embed(topic,kind,value,language)
        thread=mutable.update(topic,source,obj,kind,value,event_key+':'+source,rendered,closed)
    if remaining:
        rest=dict(payload,_events=remaining,_created=False)
        thread=threads.deliver(topic,make_embed(topic,'threadle_bundle',rest,language),event_key+':state',closed)
    return thread
