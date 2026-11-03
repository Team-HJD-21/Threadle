"""Keep new-topic roots visible during DND, defer thread creation durably."""
import time
from discord_threads import ApiError,marker,nonce
from delivery import CachedThreads
from root_status import starter_embed


class DeferredThread(Exception):
    pass


def schema(queue):
    with queue.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS deferred_threads(topic TEXT PRIMARY KEY,channel TEXT NOT NULL,message TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deferred_thread_jobs(delivery TEXT PRIMARY KEY,topic TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS deferred_thread_pace(id INTEGER PRIMARY KEY,next REAL NOT NULL);
        ''')


def wake(queue):
    # A changed connection config restarts its worker: re-evaluate edited/disabled DND settings.
    schema(queue)
    with queue.connect() as db:
        db.execute('UPDATE jobs SET next=0 WHERE done=0 AND id IN (SELECT delivery FROM deferred_thread_jobs)')


def finish(queue,delivery):
    schema(queue)
    with queue.connect() as db:db.execute('DELETE FROM deferred_thread_jobs WHERE delivery=?',(delivery,))


def defer(threads,roots,topic,event,payload,delivery,options,now=None):
    if event=='workflow_run' or not isinstance(threads,CachedThreads):return False
    from team_notifications import allowed_time
    now=time.time() if now is None else now
    queue=threads.queue;schema(queue)
    entries=[(r['event'],r['payload']) for r in payload['_events']] if event=='threadle_bundle' else [(event,payload)]
    from message_edits import COMMENTS,MutableMessages,identity
    deletions=[(kind,value) for kind,value in entries if kind in COMMENTS and value.get('action')=='deleted']
    # Tombstones must be recorded immediately, not after earlier queued creates are replayed.
    for kind,value in deletions:
        key,obj=identity(kind,value)
        if key is None:raise ValueError('Deleted comment identity missing')
        MutableMessages(threads).delete(topic,key,obj)
    active=[]
    for kind,value in entries:
        key,obj=identity(kind,value)
        if key and MutableMessages(threads).is_deleted(topic,key):continue
        active.append((kind,value))
    if not active:
        with queue.connect() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dnd_activity'").fetchone():
                db.executemany('DELETE FROM dnd_activity WHERE topic=? AND event=?',[(topic.key,'webhook:'+delivery+':part:'+str(index)) for index in range(len(entries))])
        return False
    until=allowed_time(now,options)
    with queue.connect() as db:
        pending=db.execute('SELECT 1 FROM deferred_thread_jobs WHERE delivery=?',(delivery,)).fetchone()
        linked=db.execute('SELECT 1 FROM links WHERE topic=?',(topic.key,)).fetchone()
    if until<=now:
        if pending:
            current=roots.read(topic)
            if current is None and topic.kind in ('issue','discussion'):
                with queue.connect() as db:db.execute('INSERT OR IGNORE INTO deleted_topics(topic) VALUES(?)',(topic.key,))
                roots.remove(topic)
                return 'gone'
            if current is not None:roots.update(topic,current)
            # One deferred group per second per connection, using queue scheduling rather than sleep.
            with queue.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row=db.execute('SELECT next FROM deferred_thread_pace WHERE id=1').fetchone()
                due=row[0] if row else 0
                if due>now:
                    db.execute('UPDATE jobs SET next=? WHERE id=?',(due,delivery));return True
                db.execute('INSERT INTO deferred_thread_pace VALUES(1,?) ON CONFLICT(id) DO UPDATE SET next=excluded.next',(now+1,))
        return False
    if linked:return False
    location=roots.location(topic)
    if location:
        channel,root=location
        try:thread=threads.api.call('GET','/channels/'+root)
        except ApiError as error:
            if error.status!=404:raise
        else:
            if thread.get('parent_id')!=channel:raise ValueError('Deferred root thread parent mismatch')
            return False
    else:
        channel=threads.channels[topic.kind]
        message=threads.api.call('POST',f'/channels/{channel}/messages',{
            'embeds':[starter_embed(topic)],'flags':4096,'allowed_mentions':{'parse':[]},
            'nonce':nonce(channel+':'+topic.key),'enforce_nonce':True})
        if message.get('author',{}).get('id')!=threads.bot['id'] or topic.key not in marker(message):raise ValueError('Deferred starter ownership mismatch')
        root=message['id']
    with queue.connect() as db:
        db.execute('INSERT OR REPLACE INTO root_index VALUES(?,?,?)',(topic.key,channel,root))
        db.execute('INSERT OR REPLACE INTO deferred_threads VALUES(?,?,?)',(topic.key,channel,root))
    current=roots.read(topic)
    if current is None and topic.kind in ('issue','discussion'):
        with queue.connect() as db:db.execute('INSERT OR IGNORE INTO deleted_topics(topic) VALUES(?)',(topic.key,))
        roots.remove(topic)
        return 'gone'
    if current is not None:roots.update(topic,current)
    with queue.connect() as db:
        db.execute('INSERT OR REPLACE INTO deferred_thread_jobs VALUES(?,?)',(delivery,topic.key))
        db.execute('UPDATE jobs SET next=? WHERE id=?',(until,delivery))
    return True
