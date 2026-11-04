import copy
import datetime as dt
import re

from discord_threads import Api,ApiError,Topic,make_embed,nonce
from notification_layout import CREATION,creation_embed,merge_snapshot,message_pages
from notification_media import attach_public_images

STATE_CHANGES={'closed','reopened','merged','converted_to_draft','ready_for_review','answered','unanswered'}

COMMENTS={'issue_comment','discussion_comment','pull_request_review_comment'}
FOLDED={'assigned','unassigned','labeled','unlabeled','milestoned','demilestoned','review_requested','review_request_removed','typed','untyped','field_added','field_removed'}


def identity(event,payload):
    action=payload.get('action')
    from metadata_notifications import FAMILIES
    if event in CREATION and action in set(FAMILIES)|{'field_added','field_removed','converted_to_draft','ready_for_review'}:
        return 'body',payload.get('pull_request') or payload.get('issue') or payload.get('discussion') or {}
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
            if kind.startswith('metadata:'):continue
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

    def update(self,topic,kind,obj,event,payload,event_key,embed,closed,language='ko'):
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
        if not mapped and (not kind.startswith('metadata:') or current):
            recovered_version=self.recover(topic,source,thread_id,kind,obj)
            if recovered_version>version:
                with self.queue.connect() as db:db.execute('UPDATE message_sources SET revision=? WHERE source=?',(recovered_version,source))
                return thread_id
        if thread.get('thread_metadata',{}).get('archived'):threads.api.call('PATCH','/channels/'+thread_id,{'archived':False})
        if kind=='body':
            from metadata_notifications import creation_snapshot
            embed=creation_snapshot(self.queue,topic,obj,event,payload,language,version)
        if kind.startswith('metadata:'):
            from metadata_notifications import render
            embed=render(embed,obj,language=='en',self.queue,topic,version)
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



def refresh_state_only(threads,topic,closed):
    """Update owned cards/title and archive state without a new thread message."""
    with threads.queue.connect() as db:row=db.execute('SELECT channel,thread FROM links WHERE topic=?',(topic.key,)).fetchone()
    if not row:
        closed()
        return None
    channel,thread_id=row
    try:thread=threads.api.call('GET','/channels/'+thread_id)
    except ApiError as error:
        if error.status!=404:raise
        closed()
        return None
    if thread.get('parent_id')!=channel:raise ValueError('State refresh thread parent mismatch')
    archived=bool(thread.get('thread_metadata',{}).get('archived'))
    locked=bool(thread.get('thread_metadata',{}).get('locked'))
    owned=thread.get('owner_id')==threads.bot['id']
    if archived and not locked and owned:threads.api.call('PATCH','/channels/'+thread_id,{'archived':False})
    try:finished=closed()
    except Exception:
        if archived and not locked and owned:
            try:threads.api.call('PATCH','/channels/'+thread_id,{'archived':True})
            except Exception:pass
        raise
    if not locked and owned and finished:threads.api.call('PATCH','/channels/'+thread_id,{'archived':True})
    return thread_id


def description_edits(topic,entries):
    if topic.kind!='pr':return []
    edits=[]
    for event,payload in entries:
        if event not in ('pull_request','pull_request_target') or payload.get('action')!='edited':continue
        change=(payload.get('changes') or {}).get('body')
        obj=payload.get('pull_request') or {}
        if not isinstance(change,dict) or 'from' not in change or 'body' not in obj:continue
        if (change['from'] or '')==(obj['body'] or ''):continue
        edits.append((revision(obj),payload))
    return edits


def description_notice(threads,topic,entries,event_key,closed,language):
    """A body edit deserves an unread notice, while the full description stays in place."""
    edits=description_edits(topic,entries)
    if not edits:return
    payload=max(edits,key=lambda item:item[0])[1]
    source='github-source:'+nonce(topic.key+':body')
    with threads.queue.connect() as db:
        accepted=db.execute('SELECT event,thread FROM message_sources WHERE source=?',(source,)).fetchone()
        page=db.execute('SELECT message FROM source_pages WHERE source=? AND page=0',(source,)).fetchone()
        ready=db.execute('SELECT 1 FROM source_revisions WHERE source=? AND event=?',(source,event_key+':description-notice-ready')).fetchone()
    # Stale deliveries must not announce a change that was rejected by the body updater.
    if not ready or not accepted or accepted[0]!=event_key+':body':return
    url=f'https://discord.com/channels/{threads.guild}/{accepted[1]}/{page[0]}' if page else topic.url
    english=language=='en'
    embed={'title':f'PR #{topic.number} · Description updated','url':url,
           'description':('The PR description was updated. [Read the updated description]('+url+').' if english
                          else 'PR 설명이 수정됐어요. [수정된 설명 다시 읽기]('+url+').'),
           'author':{'name':str(payload.get('sender',{}).get('login') or 'GitHub')[:100]},
           'color':0x8B949E}
    threads.deliver(topic,embed,event_key+':description-notice',closed)

def deliver_events(threads,topic,event,payload,event_key,closed,language,github=None):
    from delivery import CachedThreads
    if not isinstance(threads,CachedThreads):return threads.deliver(topic,make_embed(topic,event,payload,language),event_key,closed)
    entries=[(row['event'],row['payload']) for row in payload['_events']] if event=='threadle_bundle' else [(event,payload)]
    from review_conversations import relevant,ReviewConversations
    if github is not None and topic.kind=='pr' and any(relevant(e,p) for e,p in entries):
        reviewed=ReviewConversations(threads,github).deliver(topic,entries,event_key,closed,language)
        entries=[(e,p) for e,p in entries if not relevant(e,p)]
        if not entries:return reviewed
        event='threadle_bundle'
        payload=dict(payload,_events=[{'event':e,'payload':p} for e,p in entries])
    sources={};remaining=[]
    mutable=None
    # Deletions win over late creates/edits, including entries bundled out of order.
    for kind,value in entries:
        if kind in COMMENTS and value.get('action')=='deleted':
            key,obj=identity(kind,value)
            if key is None:raise ValueError('Deleted comment identity missing')
            if mutable is None:mutable=MutableMessages(threads)
            mutable.delete(topic,key,obj)
    transitions=any(e in CREATION and value.get('action') in STATE_CHANGES for e,value in entries)
    created=any(e in CREATION and value.get('action') in ('opened','created') for e,value in entries)
    for index,(kind,value) in enumerate(entries):
        if kind in CREATION and value.get('action') in STATE_CHANGES:continue
        if created and kind in CREATION and value.get('action') in FOLDED:continue
        key,obj=identity(kind,value)
        if key and key.startswith('metadata:'):
            from metadata_notifications import source_key
            key=source_key(threads.queue,topic,key,event_key)
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
        if not remaining:return refresh_state_only(threads,topic,closed) if transitions else None
        if mutable is None:return threads.deliver(topic,make_embed(topic,event,payload,language),event_key,closed)
        rest=dict(payload,_events=remaining,_created=False)
        return threads.deliver(topic,make_embed(topic,'threadle_bundle',rest,language),event_key,closed)
    thread=None
    for source,(kind,value,obj) in sources.items():
        previous=None
        if source=='body':
            with threads.queue.connect() as db:
                body_source='github-source:'+nonce(topic.key+':body')
                saved=db.execute('SELECT revision FROM message_sources WHERE source=?',(body_source,)).fetchone()
                previous=saved[0] if saved else None
                attempted=db.execute('SELECT 1 FROM source_revisions WHERE source=? AND event=?',(body_source,event_key+':body')).fetchone()
                if not created and not attempted and any(previous is None or version>=previous for version,_ in description_edits(topic,entries)):
                    db.execute('INSERT OR IGNORE INTO source_revisions VALUES(?,?)',(body_source,event_key+':description-notice-ready'))
            merged=merge_snapshot(topic,entries)
            for field in ('body','title','created_at','updated_at'):
                if field in obj:merged.metadata[field]=obj[field]
            merged=Topic(merged.repo,merged.kind,merged.number,merged.metadata.get('title',merged.title),merged.url,merged.metadata)
            rendered=creation_embed(merged,value,language)
            if created:
                obj=merged.metadata
                value=dict(value)
                value[{'pr':'pull_request','issue':'issue','discussion':'discussion'}[topic.kind]]=obj
        else:
            rendered=make_embed(topic,kind,value,language)
        thread=mutable.update(topic,source,obj,kind,value,event_key+':'+source,rendered,closed,language=language)
        if source=='body' and not created:description_notice(threads,topic,entries,event_key,closed,language)
    if remaining:
        rest=dict(payload,_events=remaining,_created=False)
        thread=threads.deliver(topic,make_embed(topic,'threadle_bundle',rest,language),event_key+':state',closed)
    return thread
