"""Event-driven, editable cards for GitHub code/file review conversations."""
import datetime as dt

from discord_threads import nonce
from topic_state import RED,GREEN

COMMENT_FIELDS='''id databaseId body url createdAt updatedAt diffHunk originalLine originalStartLine author{login ... on User{databaseId}}
  replyTo{databaseId} review:pullRequestReview{databaseId state body submittedAt url author{login} comments(first:1){totalCount}}'''
QUERY='''query($owner:String!,$name:String!,$number:Int!,$cursor:String){
  repository(owner:$owner,name:$name){pullRequest(number:$number){id
    reviewThreads(first:50,after:$cursor){pageInfo{hasNextPage endCursor} nodes{
      id isResolved isOutdated path line startLine originalLine originalStartLine diffSide
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


def created_order(thread):
    root=thread['comments'][0]
    try:
        return dt.datetime.fromisoformat(root['createdAt'].replace('Z','+00:00')).timestamp(),int(root['databaseId'])
    except (KeyError,TypeError,ValueError,OverflowError):
        raise RuntimeError('Review creation order unavailable; retrying') from None


def expect_edits(entries,current):
    visible={str(c[key]):c for thread in current for c in thread['comments'] for key in ('id','databaseId')}
    for event,payload in entries:
        if event!='pull_request_review_comment' or payload.get('action')!='edited':continue
        value=payload.get('comment') or {}
        actual=next((visible[str(value[key])] for key in ('id','node_id') if str(value.get(key)) in visible),None)
        if actual is None:continue  # A later deletion may already have removed it.
        try:
            expected=dt.datetime.fromisoformat(value['updated_at'].replace('Z','+00:00'))
            observed=dt.datetime.fromisoformat(actual['updatedAt'].replace('Z','+00:00'))
        except (KeyError,TypeError,ValueError):continue
        if observed<expected:raise RuntimeError('Review edit snapshot is stale; retrying')


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
    from review_code import preview
    code=preview(thread)
    if code:header+='\n\n'+code
    elif location:header+='\n\n`'+location.replace('`','ˋ')+'`'
    conversation=[]
    for comment in comments:
        author=(comment.get('author') or {}).get('login','GitHub').replace('*','\\*')
        try:
            stamp=int(dt.datetime.fromisoformat(comment['createdAt'].replace('Z','+00:00')).timestamp())
            when=' · <t:'+str(stamp)+':s>'
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
            db.execute('CREATE TABLE IF NOT EXISTS review_parent_ready(topic TEXT NOT NULL,review TEXT NOT NULL,PRIMARY KEY(topic,review))')
            db.execute('CREATE TABLE IF NOT EXISTS review_parent_authors(topic TEXT NOT NULL,review TEXT NOT NULL,login TEXT NOT NULL,PRIMARY KEY(topic,review))')
            db.execute('CREATE TABLE IF NOT EXISTS review_batch_ready(topic TEXT NOT NULL,review TEXT NOT NULL,PRIMARY KEY(topic,review))')
            db.execute('CREATE TABLE IF NOT EXISTS review_deleted_comments(topic TEXT NOT NULL,comment TEXT NOT NULL,PRIMARY KEY(topic,comment))')

    def reconcile_deletions(self,topic,entries,current):
        # Comment IDs cannot be reused. Persist an authenticated deletion before
        # rendering so stale snapshots or late create/edit webhooks cannot revive it.
        with self.queue.connect() as db:
            for event,payload in entries:
                if event=='pull_request_review_comment' and payload.get('action')=='deleted':
                    value=payload.get('comment') or {}
                    identities=[str(value[key]) for key in ('id','node_id') if value.get(key)]
                    if not identities:raise ValueError('Deleted review comment identity missing')
                    db.executemany('INSERT OR IGNORE INTO review_deleted_comments VALUES(?,?)',[(topic.key,identity) for identity in identities])
            retired={r[0] for r in db.execute('SELECT comment FROM review_deleted_comments WHERE topic=?',(topic.key,))}
        current=[dict(thread,comments=[c for c in thread['comments'] if str(c['databaseId']) not in retired and str(c['id']) not in retired]) for thread in current]
        entries=[(event,payload) for event,payload in entries if not (
            event=='pull_request_review_comment' and payload.get('action') in ('created','edited') and
            any(str((payload.get('comment') or {}).get(key)) in retired for key in ('id','node_id')))]
        return entries,current,retired

    def expand_review(self,topic,current,targets,new_targets,entries):
        reviews=set()
        for thread in current:
            if thread['id'] not in new_targets or not thread['comments']:continue
            root=thread['comments'][0]
            if not any(event=='pull_request_review_comment' and payload.get('action')=='created' and
                       str((payload.get('comment') or {}).get('id'))==str(root['databaseId']) for event,payload in entries):continue
            identity=str((root.get('review') or {}).get('databaseId',''))
            if not identity.isdigit():raise RuntimeError('Submitted review identity unavailable; retrying')
            with self.queue.connect() as db:
                ready=db.execute('SELECT 1 FROM review_batch_ready WHERE topic=? AND review=?',(topic.key,identity)).fetchone()
            if not ready:reviews.add(identity)
        for identity in reviews:
            comments={str(c['databaseId']):c for thread in current for c in thread['comments']
                      if str((c.get('review') or {}).get('databaseId'))==identity}
            expected=[(c.get('review') or {}).get('comments',{}).get('totalCount') for c in comments.values()]
            if not expected or any(type(count) is not int or count<1 or count>len(comments) for count in expected):
                raise RuntimeError('Submitted review snapshot is not complete; retrying')
            siblings={thread['id'] for thread in current if thread['comments'] and
                      str((thread['comments'][0].get('review') or {}).get('databaseId'))==identity}
            targets.update(siblings);new_targets.update(siblings)
        return reviews

    def parent(self,topic,review,entries,event_key,mutable,closed,language):
        """A child webhook can beat review.submitted. Publish its actual parent first."""
        from discord_threads import make_embed
        identity=str(review.get('databaseId',''))
        if not identity.isdigit() or str(review.get('state','')).upper()=='PENDING' or 'body' not in review:
            raise RuntimeError('Submitted parent review is not yet available; retrying')
        login=(review.get('author') or {}).get('login') or 'GitHub'
        with self.queue.connect() as db:
            saved=db.execute('SELECT login FROM review_parent_authors WHERE topic=? AND review=?',(topic.key,identity)).fetchone()
            if saved and saved[0]==login:return
            source='github-source:'+nonce(topic.key+':review:'+identity)
            current=db.execute('SELECT revision FROM message_sources WHERE source=?',(source,)).fetchone()
        value={'id':int(identity),'state':review['state'].lower(),'body':review['body'] or '',
               'submitted_at':review.get('submittedAt'),'html_url':review.get('url') or topic.url,
               'user':{'login':login}}
        for event,payload in entries:
            candidate=payload.get('review') or {}
            if event=='pull_request_review' and str(candidate.get('id'))==identity and payload.get('action') in ('submitted','edited'):
                value.update(candidate)
        policy=getattr(self.threads.api,'mention_policy',None)
        if policy is not None:
            from account_mentions import names
            policy.names.update(names(value.get('body','')))
        payload={'action':'edited' if current else 'submitted','review':value}
        # Render as a submitted review even when adopting/completing an existing card.
        rendered=make_embed(topic,'pull_request_review',dict(payload,action='submitted'),language)
        if current:
            # Adopt old synthetic cards even if a later webhook already changed
            # their revision. Preserve their message IDs and update silently.
            version=max(dt.datetime.now(dt.timezone.utc).timestamp(),current[0]+.001)
            value=dict(value,updated_at=dt.datetime.fromtimestamp(version,dt.timezone.utc).isoformat())
        mutable.update(topic,'review:'+identity,value,'pull_request_review',payload,
                       'review-parent:v2:'+identity+':'+nonce(login)+':'+nonce(event_key),rendered,closed,language)
        with self.queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO review_parent_ready VALUES(?,?)',(topic.key,identity))
            db.execute('INSERT OR REPLACE INTO review_parent_authors VALUES(?,?,?)',(topic.key,identity,login))

    def deliver(self,topic,entries,event_key,closed,language,current=None):
        from message_edits import MutableMessages
        if current is None:current=snapshot(self.github,topic)
        entries,current,retired=self.reconcile_deletions(topic,entries,current)
        expect_edits(entries,current)
        reply_context=getattr(self.threads,'review_replies',None)
        if reply_context is not None:reply_context.expect(topic,entries,current)
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
        batches=self.expand_review(topic,current,targets,new_targets,entries)
        policy=getattr(self.threads.api,'mention_policy',None)
        if policy is not None:
            from account_mentions import names
            for node in new_targets:
                if node in by_id:
                    for value in by_id[node]['comments']:policy.names.update(names(value.get('body') or ''))
        delivered=None
        order=lambda node:created_order(by_id[node]) if node in by_id and by_id[node]['comments'] else (float('-inf'),int(roots[node]))
        for node in sorted(targets,key=order):
            root=roots[node];kind=source_kind(root);thread=by_id.get(node)
            if root in retired or not thread or not thread['comments']:
                mutable.delete(topic,kind,{})
                continue
            reply_context=getattr(self.threads,'review_replies',None)
            if reply_context is not None:thread=reply_context.clean(topic,thread)
            # Adopt the original root-comment source so existing users keep their card.
            # Always render current GitHub state: old webhook retries cannot undo resolve.
            source='github-source:'+nonce(topic.key+':'+kind)
            with self.queue.connect() as db:
                previous=db.execute('SELECT revision FROM message_sources WHERE source=?',(source,)).fetchone()
            self.parent(topic,thread['comments'][0].get('review') or {},entries,event_key,mutable,closed,language)
            version=dt.datetime.now(dt.timezone.utc).timestamp()
            if previous:version=max(version,previous[0]+0.001)
            obj={'html_url':thread['comments'][0].get('url'),
                 'updated_at':dt.datetime.fromtimestamp(version,dt.timezone.utc).isoformat()}
            rendered=render(topic,thread)
            fingerprint=nonce(rendered['description']+str(rendered['color']))
            delivered=mutable.update(topic,kind,obj,'threadle_review_conversation',{'action':'snapshot' if node in new_targets else 'edited'},
                                     event_key+':conversation:'+node+':'+fingerprint,rendered,closed,language)
            if delivered is not None and reply_context is not None:reply_context.applied(topic,thread,delivered)
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
        with self.queue.connect() as db:
            db.executemany('INSERT OR IGNORE INTO review_batch_ready VALUES(?,?)',[(topic.key,identity) for identity in batches])
        return delivered
