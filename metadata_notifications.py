"""Refresh original creation details; retain legacy metadata-card recovery helpers."""
import json
from discord_threads import nonce

FAMILIES={
    'assigned':'assignees','unassigned':'assignees',
    'labeled':'labels','unlabeled':'labels',
    'milestoned':'milestone','demilestoned':'milestone',
    'review_requested':'reviewers','review_request_removed':'reviewers',
    'typed':'type','untyped':'type',
}
FIELDS=('labels','assignees','requested_reviewers','requested_teams','milestone','type')


def source_key(queue,topic,family,event_key):
    with queue.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata_bursts(topic TEXT NOT NULL,family TEXT NOT NULL,source TEXT NOT NULL,started REAL NOT NULL,until REAL NOT NULL,PRIMARY KEY(topic,family));
        CREATE TABLE IF NOT EXISTS metadata_events(topic TEXT NOT NULL,family TEXT NOT NULL,event TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(topic,family,event));
        CREATE TABLE IF NOT EXISTS metadata_states(topic TEXT PRIMARY KEY,source TEXT NOT NULL);
        ''')
        db.execute('BEGIN IMMEDIATE')
        # Legacy retries keep their existing receipt/source, preventing duplicate uncertain sends.
        saved=db.execute('SELECT source FROM metadata_events WHERE topic=? AND family=? AND event=?',(topic.key,family,event_key)).fetchone()
        if saved:return saved[0]
        state=db.execute('SELECT source FROM metadata_states WHERE topic=?',(topic.key,)).fetchone()
        if state:source=state[0]
        else:
            candidates=[]
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='message_sources'").fetchone():
                for (old,) in db.execute('SELECT source FROM metadata_bursts WHERE topic=?',(topic.key,)).fetchall():
                    hashed='github-source:'+nonce(topic.key+':'+old)
                    row=db.execute('SELECT m.revision,p.message FROM message_sources m JOIN source_pages p ON p.source=m.source AND p.page=0 WHERE m.source=?',(hashed,)).fetchone()
                    if row:candidates.append((row[0],int(row[1]),old))
            source=max(candidates)[2] if candidates else 'metadata:state'
            db.execute('INSERT INTO metadata_states VALUES(?,?)',(topic.key,source))
        db.execute('INSERT INTO metadata_events VALUES(?,?,?,?)',(topic.key,family,event_key,source))
        return source


def render(embed,obj,english,queue,topic,version):
    # Called only after MutableMessages has rejected stale/repeated events.
    with queue.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata_snapshot(topic TEXT PRIMARY KEY,revision REAL NOT NULL,value TEXT NOT NULL)')
        previous=db.execute('SELECT revision,value FROM metadata_snapshot WHERE topic=?',(topic.key,)).fetchone()
        values=json.loads(previous[1]) if previous else {}
        if not previous or version>=previous[0]:
            values.update({key:obj[key] for key in FIELDS if key in obj})
            db.execute('INSERT OR REPLACE INTO metadata_snapshot VALUES(?,?,?)',(topic.key,version,json.dumps(values)))
    result=dict(embed);lines=[]
    for field,ko,en in [('labels','라벨','Labels'),('assignees','담당자','Assignees'),('requested_reviewers','리뷰 요청','Reviewers'),('requested_teams','리뷰 팀','Review teams')]:
        if field not in values:continue
        items=[str(v.get('name') or v.get('login') or '') if isinstance(v,dict) else str(v) for v in values[field] or []]
        lines.append((en if english else ko)+': '+(', '.join(items) or ('None' if english else '없음')))
    for field,ko,en,key in [('milestone','마일스톤','Milestone','title'),('type','유형','Type','name')]:
        if field in values:lines.append((en if english else ko)+': '+str((values[field] or {}).get(key) or ('None' if english else '없음')))
    if embed.get('description'):lines.append(('Latest change: ' if english else '최근 변경: ')+embed['description'])
    result['title']=embed.get('title','').split(' · ',1)[0]+' · Details updated'
    result['description']='\n'.join(lines)
    return result


def creation_snapshot(queue,topic,obj,event,payload,language,version):
    """All metadata and body edits refresh the original creation card, including fields.

    Merge omitted webhook fields with the last accepted snapshot only after the
    shared source revision check, so late events cannot overwrite newer content.
    """
    from discord_threads import Topic
    from notification_layout import merge_snapshot,creation_embed
    with queue.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS creation_snapshots(topic TEXT PRIMARY KEY,value TEXT NOT NULL)')
        row=db.execute('SELECT value FROM creation_snapshots WHERE topic=?',(topic.key,)).fetchone()
        previous=json.loads(row[0]) if row else dict(topic.metadata)
        for key in ('_status_actor','_status_actor_state'):
            if key in topic.metadata:previous[key]=topic.metadata[key]
        base=Topic(topic.repo,topic.kind,topic.number,previous.get('title',topic.title),topic.url,previous)
        snapshot=merge_snapshot(base,[(event,payload)])
        snapshot=Topic(topic.repo,topic.kind,topic.number,snapshot.metadata.get('title',topic.title),topic.url,snapshot.metadata)
        db.execute('INSERT OR REPLACE INTO creation_snapshots VALUES(?,?)',(topic.key,json.dumps(snapshot.metadata)))
    return creation_embed(snapshot,payload,language)
