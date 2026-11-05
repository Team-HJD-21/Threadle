import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from discord_threads import ApiError
from review_repair import ReviewRepair,VERSION
import test_review_conversations as fixtures
from test_review_conversations import comment,conversation,response


class ExistingReviewRepairTests(unittest.TestCase):
    setUp=fixtures.ReviewConversationTests.setUp
    tearDown=fixtures.ReviewConversationTests.tearDown
    send=fixtures.ReviewConversationTests.send
    posts=fixtures.ReviewConversationTests.posts
    all_posts=fixtures.ReviewConversationTests.all_posts

    def worker(self):
        self.binding=SimpleNamespace(id='binding',repo='owner/repo',repo_id=99,
            config={'guild_id':'g','channels':{'pr':'c'},'language':'ko'})
        service=SimpleNamespace(registry=Mock(),github=lambda _:self.github,comment_bridge=None)
        service.registry.get.return_value=self.binding
        def call(method,path,data=None):
            if path=='/graphql':return response(self.current)
            if path=='/repositories/99':return {'id':99,'full_name':'owner/repo'}
            if path=='/repos/owner/repo/pulls/1':return dict(self.topic.metadata,html_url=self.topic.url)
            if path.startswith('/repos/owner/repo/pulls/comments/'):
                raise ApiError('GitHub',404)
            raise AssertionError(path)
        self.github.call.side_effect=call
        return ReviewRepair(service,self.binding,self.threads,lambda:1000)

    def tick(self,worker):
        with patch('root_status.TopicRoots.closed',return_value=False):return worker.tick()

    def test_one_off_removes_old_deleted_replies_without_new_webhook(self):
        self.current[0]['comments']['nodes'].append(comment(2,'Old deleted reply'))
        self.send()
        original=self.posts()[0]['id']
        self.current[0]['comments']['nodes'].pop()
        worker=self.worker();self.assertTrue(self.tick(worker))
        self.assertNotIn('Old deleted reply',self.posts()[0]['embeds'][0]['description'])
        self.assertEqual(self.posts()[0]['id'],original)
        self.assertFalse(self.tick(worker))
        # Restart does not rescan already completed topics.
        self.assertFalse(self.tick(ReviewRepair(worker.service,self.binding,self.threads,lambda:1000)))

    def test_deleted_root_removes_all_continuations_but_keeps_other_cards(self):
        self.current=[conversation('T1',[comment(1,'long '+('paragraph\n'*1500)),comment(2)]),conversation('T2',[comment(3)])]
        self.send()
        self.assertGreater(len(self.posts()),2)
        self.current[0]['comments']['nodes'].pop(0)
        worker=self.worker();self.tick(worker)
        remaining=self.posts()
        self.assertEqual(len(remaining),1)
        self.assertIn('discussion_r3',remaining[0]['embeds'][0]['url'])
        self.assertIn('Changes requested',self.all_posts()[0]['embeds'][0]['title'])
        self.assertTrue(self.api.threads)  # The PR thread itself survives.

    def test_failed_read_keeps_every_message_and_retries_after_restart(self):
        self.send();worker=self.worker();before=copy.deepcopy(self.api.posts)
        self.github.call.side_effect=ApiError('GitHub',403)
        self.tick(worker)
        self.assertEqual(self.api.posts,before)
        with self.queue.connect() as db:
            done,attempts=db.execute('SELECT done,attempts FROM review_repair_jobs WHERE version=?',(VERSION,)).fetchone()
        self.assertEqual((done,attempts),(0,1))
        worker=self.worker();worker.clock=lambda:2000
        self.assertTrue(self.tick(worker))
        self.assertFalse(self.tick(worker))

    def test_snapshot_omission_of_still_existing_comment_never_deletes(self):
        self.send();worker=self.worker();self.current=[];before=copy.deepcopy(self.api.posts)
        original=self.github.call.side_effect
        self.github.call.side_effect=lambda method,path,data=None: {'id':1} if '/pulls/comments/' in path else original(method,path,data)
        self.tick(worker)
        self.assertEqual(self.api.posts,before)
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT done FROM review_repair_jobs').fetchone()[0],0)

    def test_completed_repair_stops_opening_database_on_every_idle_turn(self):
        self.send();worker=self.worker();self.tick(worker);self.tick(worker)
        with patch.object(self.queue,'connect',side_effect=AssertionError('Completed repair must stay idle')):
            self.assertFalse(self.tick(worker))

    def test_one_off_updates_old_parent_author_and_adds_actual_code_in_place(self):
        self.send();original=[row['id'] for row in self.all_posts()]
        self.all_posts()[0]['embeds'][0]['author']['name']='GitHub'
        with self.queue.connect() as db:db.execute('DELETE FROM review_parent_authors')
        self.current[0]['comments']['nodes'][0].update(diffHunk='@@ -7 +7 @@\n+public void Method() {}',originalLine=7)
        worker=self.worker();self.tick(worker)
        self.assertEqual([row['id'] for row in self.all_posts()],original)
        self.assertEqual(self.all_posts()[0]['embeds'][0]['author']['name'],'reviewer')
        self.assertIn('public void Method()',self.posts()[0]['embeds'][0]['description'])
        self.assertIn('```csharp\n',self.posts()[0]['embeds'][0]['description'])

    def test_readability_upgrade_refreshes_previously_repaired_cards_once(self):
        self.send();ids=[row['id'] for row in self.all_posts()]
        worker=self.worker()
        from review_repair import VERSION
        with self.queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO review_repair_flags VALUES(?)',('review-authors-context-v2',))
            db.execute('DELETE FROM review_repair_jobs WHERE version=?',(VERSION,))
            db.execute('DELETE FROM review_repair_flags WHERE version=?',(VERSION,))
        worker=ReviewRepair(worker.service,self.binding,self.threads,lambda:1000)
        self.assertTrue(self.tick(worker));self.assertFalse(self.tick(worker))
        self.assertEqual([row['id'] for row in self.all_posts()],ids)

    def test_one_off_regroups_an_existing_pr_body_split_around_review_messages(self):
        from message_edits import MutableMessages
        from discord_threads import nonce
        self.topic.metadata.update(body='Description paragraph.\n'*550,updated_at='2026-11-05T00:00:00Z')
        mutable=MutableMessages(self.threads)
        thread=mutable.update(self.topic,'body',self.topic.metadata,'pull_request',{'action':'opened'},'opened',None,lambda:False)
        source='github-source:'+nonce(self.topic.key+':body')
        self.send()
        with self.queue.connect() as db:pages=db.execute('SELECT page,message FROM source_pages WHERE source=? ORDER BY page',(source,)).fetchall()
        reviews=[r['id'] for r in self.all_posts() if r['id'] not in {row[1] for row in pages}]
        page,message=pages[-1]
        # Reproduce the old placement: a continuation was appended after reviews.
        old=next(r for r in self.api.posts[thread] if r['id']==message)
        new=self.api.call('POST',f'/channels/{thread}/messages',dict(old,nonce='old-layout'))
        self.api.call('DELETE',f'/channels/{thread}/messages/{message}')
        with self.queue.connect() as db:db.execute('UPDATE source_pages SET message=? WHERE source=? AND page=?',(new['id'],source,page))
        worker=self.worker();self.tick(worker)
        with self.queue.connect() as db:current=[r[0] for r in db.execute('SELECT message FROM source_pages WHERE source=? ORDER BY page',(source,))]
        ordered=[r['id'] for r in self.api.posts[thread]]
        self.assertEqual(ordered[-len(current):],current)
        self.assertFalse({r[1] for r in pages}&set(current))
        self.assertTrue(set(reviews)<=set(ordered))
        self.assertNotIn(new['id'],ordered)
