"""Event-driven, editable cards for GitHub code/file review conversations."""
import datetime as dt

from discord_threads import nonce
from topic_state import RED,GREEN

COMMENT_FIELDS='''id databaseId body url createdAt updatedAt author{login}
  replyTo{databaseId} review:pullRequestReview{databaseId state url author{login}}'''
QUERY='''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){pullRequest(number:$number){id
    reviewThreads(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
      id isResolved path line startLine
      comments(first:100){pageInfo{hasNextPage endCursor} nodes{'''+COMMENT_FIELDS+'''}}
    }}}}}'''
MORE='''query($owner:String!,$name:String!,$number:Int!,$thread:ID!,$cursor:String!){
  repository(owner:$owner,name:$name){pullRequest(number:$number){id}}
  node(id:$thread){... on PullRequestReviewThread{pullRequest{id}
    comments(first:100,after:$cursor){pageInfo{hasNextPage endCursor} nodes{'''+COMMENT_FIELDS+'''}}
  }}}'''


def relevant(event,payload):
    return (event=='pull_request_review_comment' and payload.get('action') in ('created','edited','deleted')
            or event=='pull_request_review_thread' and payload.get('action') in ('resolved','unresolved'))


def _next(connection,seen):
    page=connection.get('pageInfo') or {}
    if not page.get('hasNextPage'):return None
    cursor=page.get('endCursor')
    if not cursor or cursor in seen:raise RuntimeError('Review conversation pagination did not advance')
    seen.add(cursor)
    return cursor


def snapshot(github,topic):
    """Read the current PR only, never poll or infer resolution from a comment."""
    owner,name=topic.repo.split('/')
    variables={'owner':owner,'name':name,'number':topic.number,'cursor':None}
    result=[];seen=set()
    while True:
        response=github.call('POST','/graphql',{'query':QUERY,'variables':dict(variables)})
        if response.get('errors'):raise RuntimeError('Could not read review conversations')
        pr=((response.get('data') or {}).get('repository') or {}).get('pullRequest')
        if not pr or 'reviewThreads' not in pr:raise RuntimeError('Review conversation snapshot unavailable')
        connection=pr['reviewThreads']
        for thread in connection['nodes']:
            comments=thread['comments'];nodes=list(comments['nodes']);comment_seen=set()
            cursor=_next(comments,comment_seen)
            while cursor:
                values=dict(variables,thread=thread['id'],cursor=cursor)
                extra=github.call('POST','/graphql',{'query':MORE,'variables':values})
                if extra.get('errors'):raise RuntimeError('Could not read review conversation replies')
                data=extra.get('data') or {};node=data.get('node') or {}
                scoped=((data.get('repository') or {}).get('pullRequest') or {}).get('id')
                if scoped!=pr['id'] or node.get('pullRequest',{}).get('id')!=pr['id']:
                    raise ValueError('Review conversation repository/PR scope mismatch')
                comments=node['comments'];nodes.extend(comments['nodes'])
                cursor=_next(comments,comment_seen)
            thread=dict(thread,comments=nodes)
            # Unsubmitted draft reviews must never be published to Discord.
            thread['comments']=[c for c in nodes if (c.get('review') or {}).get('state')!='PENDING']
            result.append(thread)
        variables['cursor']=_next(connection,seen)
        if variables['cursor'] is None:return result


def source_kind(root):return 'comment:pull_request_review_comment:'+str(root)


def review_label(review):
    state={'COMMENTED':'Commented','APPROVED':'Approved','CHANGES_REQUESTED':'Changes requested',
           'DISMISSED':'Dismissed'}.get(str(review.get('state','')).upper(),'Review')
    author=(review.get('author') or {}).get('login','GitHub')
    label=f"Review #{review.get('databaseId','?')} · {author} · {state}"
    return '['+label+']('+review['url']+')' if review.get('url') else label


def render(topic,thread):
    comments=thread['comments'];root=comments[0]
    location=thread.get('path') or ''
    line=thread.get('line')
    if line:
        start=thread.get('startLine')
        location+=':'+str(start)+'–'+str(line) if start and start!=line else ':'+str(line)
    review=root.get('review') or {}
    header='**Review**\n'+review_label(review)+'\n\n' if review else ''
    header+='**Current Status**\n`'+('Resolved' if thread['isResolved'] else 'Unresolved')+'`'
    if location:header+='\n\n`'+location.replace('`','ˋ')+'`'
    conversation=[]
    for comment in comments:
        author=(comment.get('author') or {}).get('login','GitHub').replace('*','\\*')
        try:
            stamp=int(dt.datetime.fromisoformat(comment['createdAt'].replace('Z','+00:00')).timestamp())
            when=' · <t:'+str(stamp)+':f>'
        except (KeyError,TypeError,ValueError,OverflowError):when=''
        conversation.append('**'+author+'**'+when+'\n'+(comment.get('body') or ''))
    return {'title':f'PR #{topic.number} · Review thread','url':root.get('url') or topic.url,
            'description':header+'\n\n'+'\n\n────\n\n'.join(conversation),
            'color':GREEN if thread['isResolved'] else RED}


class ReviewConversations:
    def __init__(self,threads,github):
        self.threads=threads;self.github=github;self.queue=threads.queue
        with self.queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS review_conversations(topic TEXT NOT NULL,node TEXT NOT NULL,root TEXT NOT NULL,PRIMARY KEY(topic,node))')
            db.execute('CREATE TABLE IF NOT EXISTS review_members(topic TEXT NOT NULL,comment TEXT NOT NULL,node TEXT NOT NULL,PRIMARY KEY(topic,comment))')

    def deliver(self,topic,entries,event_key,closed,language):
        from message_edits import MutableMessages
        current=snapshot(self.github,topic)
        mutable=MutableMessages(self.threads)
        with self.queue.connect() as db:
            roots=dict(db.execute('SELECT node,root FROM review_conversations WHERE topic=?',(topic.key,)))
            members=dict(db.execute('SELECT comment,node FROM review_members WHERE topic=?',(topic.key,)))
        by_id={t['id']:t for t in current};targets=set();new_targets=set();deleted=[]
        for thread in current:
            if not thread['comments']:continue
            root=str(thread['comments'][0]['databaseId'])
            roots.setdefault(thread['id'],root)
            for comment in thread['comments']:
                for key in ('id','databaseId'):members[str(comment[key])]=thread['id']
        for event,payload in entries:
            if not relevant(event,payload):continue
            if event=='pull_request_review_thread':
                value=payload.get('thread') or {}
                target=next((str(value.get(k)) for k in ('node_id','id') if str(value.get(k)) in by_id),None)
                if target is None:
                    ids={str(c.get('id')) for c in value.get('comments',[]) if isinstance(c,dict)}
                    target=next((members[c] for c in ids if c in members),None)
            else:
                value=payload.get('comment') or {}
                target=next((members[str(value[k])] for k in ('id','node_id','in_reply_to_id') if str(value.get(k)) in members),None)
            if target:
                targets.add(target)
                if event=='pull_request_review_comment' and payload.get('action')=='created':new_targets.add(target)
            elif payload.get('action')=='deleted':deleted.append(value)
            else:raise RuntimeError('Review conversation is not yet visible; retrying')
        # Save identities only after a complete snapshot; these mappings contain no bodies.
        with self.queue.connect() as db:
            db.executemany('INSERT OR IGNORE INTO review_conversations VALUES(?,?,?)',[(topic.key,node,root) for node,root in roots.items()])
            db.executemany('INSERT OR REPLACE INTO review_members VALUES(?,?,?)',[(topic.key,comment,node) for comment,node in members.items()])
        delivered=None
        for node in sorted(targets):
            root=roots[node];kind=source_kind(root);thread=by_id.get(node)
            if not thread or not thread['comments']:
                mutable.delete(topic,kind,{})
                continue
            # Adopt the original root-comment source so existing users keep their card.
            # Always render current GitHub state: old webhook retries cannot undo resolve.
            source='github-source:'+nonce(topic.key+':'+kind)
            with self.queue.connect() as db:
                previous=db.execute('SELECT revision FROM message_sources WHERE source=?',(source,)).fetchone()
            version=dt.datetime.now(dt.timezone.utc).timestamp()
            if previous:version=max(version,previous[0]+0.001)
            obj={'html_url':thread['comments'][0].get('url'),
                 'updated_at':dt.datetime.fromtimestamp(version,dt.timezone.utc).isoformat()}
            rendered=render(topic,thread)
            fingerprint=nonce(rendered['description']+str(rendered['color']))
            delivered=mutable.update(topic,kind,obj,'threadle_review_conversation',{'action':'snapshot' if node in new_targets else 'edited'},
                                     event_key+':conversation:'+node+':'+fingerprint,rendered,closed,language)
            # Older versions sent replies separately. Remove only those owned reply cards.
            for comment in thread['comments']:
                identity=str(comment['databaseId'])
                if identity==root:continue
                source='github-source:'+nonce(topic.key+':'+source_kind(identity))
                with self.queue.connect() as db:
                    legacy=db.execute('SELECT 1 FROM message_sources WHERE source=?',(source,)).fetchone()
                if legacy:mutable.delete(topic,source_kind(identity),{'html_url':comment.get('url')})
        for value in deleted:
            if value.get('id'):mutable.delete(topic,source_kind(value['id']),value)
        return delivered
