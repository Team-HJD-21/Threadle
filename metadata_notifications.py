"""Edit related metadata notifications after sending the first one immediately."""
import time
from discord_threads import nonce

FAMILIES={
    'assigned':'assignees','unassigned':'assignees',
    'labeled':'labels','unlabeled':'labels',
    'milestoned':'milestone','demilestoned':'milestone',
    'review_requested':'reviewers','review_request_removed':'reviewers',
    'typed':'type','untyped':'type',
}


def source_key(queue,topic,family,event_key):
    # This is an edit window, not a delivery delay. Use receipt time so delayed DND replays stay separate.
    with queue.connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata_bursts(topic TEXT NOT NULL,family TEXT NOT NULL,source TEXT NOT NULL,started REAL NOT NULL,until REAL NOT NULL,PRIMARY KEY(topic,family));
        CREATE TABLE IF NOT EXISTS metadata_events(topic TEXT NOT NULL,family TEXT NOT NULL,event TEXT NOT NULL,source TEXT NOT NULL,PRIMARY KEY(topic,family,event));
        ''')
        db.execute('BEGIN IMMEDIATE')
        saved=db.execute('SELECT source FROM metadata_events WHERE topic=? AND family=? AND event=?',(topic.key,family,event_key)).fetchone()
        if saved:return saved[0]
        received=db.execute('SELECT received FROM coalescing_jobs WHERE id=?',(event_key.removeprefix('webhook:'),)).fetchone()
        now=received[0] if received else time.time()
        current=db.execute('SELECT source,started,until FROM metadata_bursts WHERE topic=? AND family=?',(topic.key,family)).fetchone()
        if current and current[1]<=now<=current[2]:source=current[0]
        else:
            source=family+':'+nonce(event_key)
            db.execute('INSERT OR REPLACE INTO metadata_bursts VALUES(?,?,?,?,?)',(topic.key,family,source,now,now+15))
        db.execute('INSERT INTO metadata_events VALUES(?,?,?,?)',(topic.key,family,event_key,source))
        return source


def render(embed,obj,english):
    result=dict(embed);lines=[]
    for field,ko,en in [('labels','라벨','Labels'),('assignees','담당자','Assignees'),('requested_reviewers','리뷰 요청','Reviewers'),('requested_teams','리뷰 팀','Review teams')]:
        if field not in obj:continue
        values=[str(v.get('name') or v.get('login') or '') if isinstance(v,dict) else str(v) for v in obj[field] or []]
        lines.append((en if english else ko)+': '+(', '.join(values) or ('None' if english else '없음')))
    if 'milestone' in obj:lines.append(('Milestone' if english else '마일스톤')+': '+str((obj.get('milestone') or {}).get('title') or ('None' if english else '없음')))
    if embed.get('description'):lines.append(('Latest change: ' if english else '최근 변경: ')+embed['description'])
    result['description']='\n'.join(lines)
    return result
