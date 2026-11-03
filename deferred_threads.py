"""Release legacy DND-held jobs and retain their original starter ownership."""
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
        db.execute('DELETE FROM deferred_thread_pace')


def finish(queue,delivery):
    schema(queue)
    with queue.connect() as db:db.execute('DELETE FROM deferred_thread_jobs WHERE delivery=?',(delivery,))


def defer(threads,roots,topic,event,payload,delivery,options,now=None):
    if not isinstance(threads,CachedThreads):return False
    schema(threads.queue)
    with threads.queue.connect() as db:
        pending=db.execute('SELECT 1 FROM deferred_thread_jobs WHERE delivery=?',(delivery,)).fetchone()
    if pending and topic is not None:
        current=roots.read(topic)
        if current is None and topic.kind in ('issue','discussion'):
            with threads.queue.connect() as db:db.execute('INSERT OR IGNORE INTO deleted_topics(topic) VALUES(?)',(topic.key,))
            roots.remove(topic)
            return 'gone'
        if current is not None:roots.update(topic,current)
    return False
