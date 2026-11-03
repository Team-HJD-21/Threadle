import unittest
import test_message_edits as fixtures
from message_edits import deliver_events
from discord_threads import ApiError,make_embed

class CommentDeletionTests(unittest.TestCase):
    setUp=fixtures.EditTests.setUp
    tearDown=fixtures.EditTests.tearDown
    payload=fixtures.EditTests.payload
    send=fixtures.EditTests.send

    def test_delete_preserves_other_comments_and_thread(self):
        thread=self.send(self.payload(),'first')
        self.send(self.payload('Other',identity=8),'other')
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual([m['embeds'][0]['description'] for m in self.api.posts[thread]],['Other'])
        self.assertIn(thread,self.api.threads)
        self.assertIsNone(self.send(self.payload('Late','edited'),'late'))
        self.assertIsNone(self.send(self.payload(),'old-create'))
        self.send(self.payload(action='deleted'),'repeat')
        self.assertEqual(len(self.api.posts[thread]),1)

    def test_all_long_text_and_image_pages_removed(self):
        thread=self.send(self.payload('Long text '*1500+' ![a](https://example.com/a.png) ![b](https://example.com/b.png)'),'long')
        self.assertGreater(len(self.api.posts[thread]),2)
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(self.api.posts[thread],[])

    def test_delete_before_create_does_not_create_thread(self):
        self.assertIsNone(self.send(self.payload(action='deleted'),'delete'))
        self.assertIsNone(self.send(self.payload(),'late'))
        self.assertFalse(self.api.threads)

    def test_comment_families_are_independent(self):
        events=('issue_comment','discussion_comment','pull_request_review_comment')
        thread=None
        for event in events:thread=self.send(self.payload(event),event,event)
        for index,event in enumerate(events):
            self.send(self.payload(action='deleted'),'delete-'+event,event)
            self.assertEqual(len(self.api.posts[thread]),2-index)

    def test_out_of_order_bundle_deletion_wins(self):
        entries=[{'event':'issue_comment','payload':p} for p in (self.payload(),self.payload(action='deleted'),self.payload('late','edited'),self.payload('Other',identity=8))]
        thread=self.send({'_events':entries},'bundle','threadle_bundle')
        self.assertEqual([m['embeds'][0]['description'] for m in self.api.posts[thread]],['Other'])

    def test_missing_message_is_idempotent(self):
        thread=self.send(self.payload(),'first');self.api.posts[thread].clear()
        self.send(self.payload(action='deleted'),'delete')
        self.assertIsNone(self.send(self.payload(),'retry'))

    def test_lost_delete_response_recovers(self):
        thread=self.send(self.payload(),'first');original=self.api.call;failed=[]
        def call(method,path,data=None):
            result=original(method,path,data)
            if method=='DELETE' and not failed:failed.append(True);raise ApiError('Discord','network')
            return result
        self.api.call=call
        with self.assertRaises(ApiError):self.send(self.payload(action='deleted'),'delete')
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(self.api.posts[thread],[])

    def test_permission_error_retries_and_foreign_author_is_preserved(self):
        thread=self.send(self.payload(),'first');original=self.api.call
        def call(method,path,data=None):
            if method=='DELETE':raise ApiError('Discord',403,50013)
            return original(method,path,data)
        self.api.call=call
        with self.assertRaises(ApiError):self.send(self.payload(action='deleted'),'delete')
        self.api.call=original;self.api.posts[thread][0]['author']['id']='other'
        with self.assertRaises(ValueError):self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.api.posts[thread][0]['author']['id']='bot'
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(self.api.posts[thread],[])

    def test_legacy_mixed_comments_preserve_other_embed(self):
        mixed={'_embeds':[make_embed(self.topic,'issue_comment',self.payload('A')),make_embed(self.topic,'issue_comment',self.payload('B',identity=8))]}
        thread=self.threads.deliver(self.topic,mixed,'legacy',lambda:False)
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.assertEqual([e['description'] for e in self.api.posts[thread][0]['embeds']],['B'])

    def test_missing_source_mapping_recovers_using_marker(self):
        thread=self.send(self.payload(),'first')
        with self.queue.connect() as db:
            for table in ('source_pages','message_sources'):db.execute('DELETE FROM '+table)
        self.send(self.payload(action='deleted'),'delete')
        self.assertEqual(self.api.posts[thread],[])
