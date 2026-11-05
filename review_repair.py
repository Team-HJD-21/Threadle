"""One-off, resumable reconciliation of already delivered review conversations."""
import logging
import time

from discord_threads import ApiError,Topic,nonce
from review_conversations import ReviewConversations,snapshot,source_kind

LOG=logging.getLogger('review-repair')
VERSION='review-authors-context-v2'


class ReviewRepair:
    def __init__(self,service,binding,threads,clock=time.time):
        self.service,self.binding,self.threads,self.clock=service,binding,threads,clock
        self.queue=threads.queue
        self.next_check=0
        self.complete=False
        # Initialize mappings on installations that have never received a review.
        ReviewConversations(threads,None)
        from message_edits import MutableMessages
        MutableMessages(threads)
        with self.queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS review_repair_flags(version TEXT PRIMARY KEY)')
            db.execute('CREATE TABLE IF NOT EXISTS review_repair_jobs(version TEXT,topic TEXT,done INTEGER DEFAULT 0,attempts INTEGER DEFAULT 0,next REAL DEFAULT 0,PRIMARY KEY(version,topic))')
            if not db.execute('SELECT 1 FROM review_repair_flags WHERE version=?',(VERSION,)).fetchone():
                db.execute('INSERT OR IGNORE INTO review_repair_jobs(version,topic) SELECT ?,topic FROM review_conversations GROUP BY topic',(VERSION,))
                db.execute('INSERT INTO review_repair_flags VALUES(?)',(VERSION,))

    def tick(self):
        if self.complete or self.clock()<self.next_check:return False
        with self.queue.connect() as db:
            row=db.execute('SELECT topic,attempts FROM review_repair_jobs WHERE version=? AND done=0 AND next<=? ORDER BY rowid LIMIT 1',(VERSION,self.clock())).fetchone()
            if not row:
                future=db.execute('SELECT min(next) FROM review_repair_jobs WHERE version=? AND done=0',(VERSION,)).fetchone()[0]
                if future is None:self.complete=True
                else:self.next_check=future
        if not row:return False
        key,attempts=row
        try:
            self.repair(key)
            with self.queue.connect() as db:
                db.execute('UPDATE review_repair_jobs SET done=1 WHERE version=? AND topic=?',(VERSION,key))
            LOG.info('Existing review cards reconciled binding=%s topic=%s',self.binding.id,key)
        except Exception as error:
            with self.queue.connect() as db:
                db.execute('UPDATE review_repair_jobs SET attempts=attempts+1,next=? WHERE version=? AND topic=?',(self.clock()+min(900,2**min(attempts+1,10)),VERSION,key))
            LOG.warning('Existing review reconciliation deferred binding=%s error=%s',self.binding.id,type(error).__name__)
        return True

    def repair(self,key):
        binding=self.binding
        if self.service.registry.get(binding.id)!=binding:raise RuntimeError('Connection changed')
        prefix='github-thread:'+binding.repo+':pr:'
        if not key.startswith(prefix) or not key[len(prefix):].isdigit():raise ValueError('Review repair scope mismatch')
        with self.queue.connect() as db:
            link=db.execute('SELECT channel,thread FROM links WHERE topic=?',(key,)).fetchone()
            known=db.execute('SELECT node,root FROM review_conversations WHERE topic=?',(key,)).fetchall()
            sources={r[0] for r in db.execute('SELECT source FROM message_sources')}
            deleted={r[0] for r in db.execute('SELECT source FROM deleted_sources WHERE done=1')}
            members=db.execute('SELECT comment,node FROM review_members WHERE topic=?',(key,)).fetchall()
        tracked=[(node,root) for node,root in known if 'github-source:'+nonce(key+':'+source_kind(root)) in sources-deleted]
        if not link or not tracked:return
        github=self.service.github(binding)
        actual=github.call('GET',f'/repositories/{binding.repo_id}')
        if actual.get('id')!=binding.repo_id or actual.get('full_name')!=binding.repo:raise RuntimeError('Repository changed')
        number=int(key[len(prefix):])
        pr=github.call('GET',f'/repos/{binding.repo}/pulls/{number}')
        if pr.get('number')!=number:raise ValueError('PR scope mismatch')
        topic=Topic(binding.repo,'pr',number,pr.get('title',''),pr['html_url'],pr)
        current=snapshot(github,topic)
        visible={str(c['databaseId']) for thread in current for c in thread['comments']}
        nodes={node for node,_ in tracked}
        identities={root for _,root in tracked}|{identity for identity,node in members if node in nodes and identity.isdigit()}
        entries=[]
        # A missing node alone is never proof of deletion. Confirm each missing
        # comment with the repository credential before removing any old text.
        for identity in sorted(identities,key=int):
            if identity in visible:continue
            try:
                github.call('GET',f'/repos/{binding.repo}/pulls/comments/{identity}')
            except ApiError as error:
                if error.status!=404:raise
                entries.append(('pull_request_review_comment',{'action':'deleted','comment':{'id':int(identity)}}))
            else:raise RuntimeError('Review snapshot has not converged')
        entries.extend(('pull_request_review_comment',{'action':'edited','comment':{'id':int(root)}}) for _,root in tracked if root in visible)
        if self.service.registry.get(binding.id)!=binding:raise RuntimeError('Connection changed')
        from delivery import CachedThreads
        routed=CachedThreads(self.threads.api,binding.config['guild_id'],dict(binding.config['channels'],pr=link[0]),self.queue,binding.config.get('language','ko'))
        routed.bot=self.threads.bot;routed.media_fetcher=getattr(github,'fetch_media',None)
        routed.silent_notifications=lambda:True
        if self.service.comment_bridge is not None:routed.review_replies=self.service.comment_bridge.replies.context(binding)
        from root_status import TopicRoots
        roots=TopicRoots(routed,github)
        ReviewConversations(routed,github).deliver(topic,entries,'repair:'+VERSION,lambda:roots.closed(topic),binding.config.get('language','ko'),current=current)
