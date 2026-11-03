"""Best-effort, content-free receiver-to-Discord latency samples."""
import contextvars
import logging
import math
import re
import sqlite3
import statistics
import time
from contextlib import closing

ACTIVE=contextvars.ContextVar('delivery_latency',default=None)
LOG=logging.getLogger('delivery-latency')


def write(queue,sql,rows):
    try:
        with queue.connect() as db:db.executemany(sql,rows)
    except (sqlite3.Error,OSError):LOG.warning('Delivery timing unavailable; notification processing continues')


def begin(queue,identities):
    now=time.time();context={'queue':queue,'ids':identities,'first':None}
    write(queue,'UPDATE delivery_latency SET started=coalesce(started,?) WHERE delivery=?',[(now,i) for i in identities])
    return ACTIVE.set(context)


def discord_write(method,path):
    context=ACTIVE.get()
    if not context or context['first'] is not None or method not in ('POST','PATCH') or not re.fullmatch(r'/channels/\d+/messages(?:/\d+)?',path):return
    now=time.time();context['first']=now
    write(context['queue'],'UPDATE delivery_latency SET first_discord=coalesce(first_discord,?) WHERE delivery=?',[(now,i) for i in context['ids']])


def deferred():
    context=ACTIVE.get()
    if context:write(context['queue'],'UPDATE delivery_latency SET deferred=1 WHERE delivery=?',[(i,) for i in context['ids']])


def end(token):
    context=ACTIVE.get()
    try:
        if context:
            now=time.time()
            write(context['queue'],'UPDATE delivery_latency SET completed=? WHERE delivery=? AND EXISTS(SELECT 1 FROM jobs WHERE id=delivery_latency.delivery AND done=1)',[(now,i) for i in context['ids']])
            write(context['queue'],'DELETE FROM delivery_latency WHERE completed<?',[(now-30*86400,)])
    finally:ACTIVE.reset(token)


def summary(path):
    if not path.is_file():return None
    try:
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
            rows=db.execute('SELECT received,started,first_discord,completed FROM delivery_latency WHERE webhook=1 AND deferred=0 AND started IS NOT NULL AND completed IS NOT NULL AND first_discord IS NOT NULL ORDER BY completed DESC LIMIT 200').fetchall()
    except sqlite3.Error:return None
    rows=[r for r in rows if r[0]<=r[1]<=r[2]<=r[3]]
    if not rows:return None
    first=sorted(r[2]-r[0] for r in rows)
    return {'count':len(rows),'p50':statistics.median(first),'p95':first[max(0,math.ceil(len(first)*.95)-1)],
            'queue_p50':statistics.median(r[1]-r[0] for r in rows),'processing_p50':statistics.median(r[2]-r[1] for r in rows)}
