import copy
from unittest import TestCase
from unittest.mock import Mock, patch

import test_accounts as fixtures
from comment_bridge import CommentBridge, UserApi
from discord_threads import ApiError


class DeletionTests(TestCase):
    link = fixtures.AccountTests.link
    job = fixtures.AccountTests.job
    discord_call = fixtures.AccountTests.discord_call

    def setUp(self):
        fixtures.AccountTests.setUp(self)
        self.service.chat_events.clock = self.clock
        self.deleted = []
        self.missing = False
        self.remote_author = 91
        self.remote_topic = 7
        self.github.call.side_effect = self.github_call
        self.link()

    def github_call(self, method, path, data=None):
        if method == 'GET' and path in ('/repos/one/repo/issues/comments/44','/repos/one/repo/pulls/comments/44'):
            if self.missing:
                raise ApiError('GitHub',404)
            return {'id':44,'user':{'id':self.remote_author},'in_reply_to_id':10,
                    'issue_url':f'https://api.github.com/repos/one/repo/issues/{self.remote_topic}',
                    'pull_request_url':f'https://api.github.com/repos/one/repo/pulls/{self.remote_topic}'}
        if method == 'DELETE':
            self.deleted.append(path)
            self.missing = True
            return None
        if path == '/graphql' and 'DeleteDiscussionCommentInput' in data['query']:
            self.deleted.append(data['variables']['input']['id'])
            return {'data':{'deleteDiscussionComment':{'clientMutationId':data['variables']['input']['clientMutationId']}}}
        result = fixtures.AccountTests.github_call(self,method,path,data)
        if path == '/graphql' and 'after' in data['variables']:
            result['data']['repository']['discussion']['comments']['nodes'] = [] if self.missing else [
                {'id':'DC_44','databaseId':44,'author':{'databaseId':self.remote_author}}]
        return result

    def post(self):
        self.assertTrue(self.bridge.ingest(self.message))
        self.bridge.tick()
        self.assertEqual(self.job()['state'],'done')

    def delete(self):
        return self.bridge.deletions.ingest('1000','7777','8000')

    def test_manual_delete_removes_exact_comment_once_and_retains_receipt(self):
        self.post(); self.assertTrue(self.delete())
        self.bridge.tick()
        self.assertEqual(self.deleted,['/repos/one/repo/issues/comments/44'])
        self.assertEqual(self.job()['state'],'deleted')
        self.assertFalse(self.delete()); self.bridge.tick()
        self.assertEqual(len(self.deleted),1)
        self.assertEqual(self.job()['state'],'deleted')
        with self.accounts.db() as db:
            row=db.execute("SELECT actor,source FROM chat_events WHERE kind='message-delete' LIMIT 1").fetchone()
        self.assertEqual(tuple(row),(None,'gateway'))

    def test_pending_input_is_canceled_without_posting(self):
        self.bridge.ingest(self.message)
        self.assertTrue(self.delete()); self.bridge.tick()
        self.assertEqual(self.writes,[]); self.assertEqual(self.deleted,[])
        self.assertEqual(self.job()['state'],'failed')
        self.assertIsNone(self.job()['payload'])

    def test_bot_cleanup_intent_survives_restart_and_precedes_gateway_event(self):
        self.post()
        self.service.chat_events.intent('7777','8000','review-reply')
        restarted=CommentBridge(self.service,self.accounts,self.clock,lambda *_:self.github)
        self.assertFalse(restarted.deletions.ingest('1000','7777','8000'))
        restarted.tick();self.assertEqual(self.deleted,[])

    def test_review_cleanup_gateway_race_never_deletes_github_reply(self):
        self.post()
        with self.accounts.db() as db:
            db.execute("UPDATE bridge_replies SET mode='review',root='10',node='T1',applied=1")
        seen=[]
        def automatic(method,path,data=None):
            if method=='DELETE':
                seen.append(self.delete())
                return None
            result=self.discord_call(method,path,data)
            if method=='GET' and path=='/channels/7777/messages/8000':
                result.update(content=self.message['content'],attachments=[])
            return result
        self.api.call.side_effect=automatic
        self.bridge.tick();self.bridge.tick()
        self.assertEqual(seen,[False]);self.assertEqual(self.deleted,[])
        self.assertEqual(self.job()['state'],'done')

    def test_definite_denied_bot_cleanup_does_not_suppress_later_manual_delete(self):
        self.post(); events=self.service.chat_events
        events.intent('7777','8000');events.failed_api('DELETE','/channels/7777/messages/8000',403)
        self.assertTrue(self.delete());self.bridge.tick();self.assertEqual(len(self.deleted),1)

    def test_edited_legacy_review_input_is_not_misclassified_as_bot_cleanup(self):
        self.post()
        with self.accounts.db() as db:
            db.execute("UPDATE bridge_replies SET mode='review',applied=1,cleaned=1,root='10',node='T1'")
        self.assertTrue(self.delete()); self.bridge.tick()
        self.assertEqual(self.deleted,['/repos/one/repo/pulls/comments/44'])

    def test_wrong_guild_thread_or_unforwarded_message_has_no_remote_effect(self):
        self.post()
        for args in [('2000','7777','8000'),('1000','1002','8000'),('1000','7777','9999')]:
            self.assertFalse(self.bridge.deletions.ingest(*args))
        self.bridge.tick();self.assertEqual(self.deleted,[])

    def test_disconnect_or_relink_generation_cancels_queued_delete(self):
        self.post();self.delete();self.accounts.disconnect(discord='55');self.link()
        self.bridge.tick();self.assertEqual(self.deleted,[])
        self.assertEqual(self.job()['state'],'delete-failed')

    def test_removed_repository_connection_prevents_deletion(self):
        self.post();self.delete()
        with self.service.queue(self.binding).connect() as db:db.execute('DELETE FROM links')
        self.bridge.tick();self.assertEqual(self.deleted,[])
        self.assertEqual(self.job()['state'],'delete-failed')

    def test_disabled_comment_consent_prevents_deletion(self):
        self.post();self.delete();self.link(comments=False)
        self.bridge.tick();self.assertEqual(self.deleted,[])

    def test_missing_comment_does_not_delete_another_object(self):
        self.post();self.delete();self.missing=True;self.bridge.tick()
        self.assertEqual(self.deleted,[]);self.assertEqual(self.job()['state'],'deleted')

    def test_remote_author_and_topic_must_match_receipt(self):
        for key in ('remote_author','remote_topic'):
            with self.subTest(key=key):
                self.setUp();self.post();self.delete();setattr(self,key,99)
                self.bridge.tick();self.assertEqual(self.deleted,[])
                self.assertEqual(self.job()['state'],'delete-failed')

    def test_lost_delete_response_reconciles_404_without_repeating_write(self):
        self.post();self.delete();call=self.github.call.side_effect
        def lost(method,path,data=None):
            result=call(method,path,data)
            if method=='DELETE':raise ApiError('GitHub','network')
            return result
        self.github.call.side_effect=lost;self.bridge.tick()
        self.assertEqual(self.job()['state'],'deleting')
        restarted=CommentBridge(self.service,self.accounts,self.clock,lambda *_:self.github)
        self.clock.return_value+=31;restarted.tick()
        self.assertEqual(self.job()['state'],'deleted');self.assertEqual(len(self.deleted),1)

    def test_review_reply_deletes_reply_not_root(self):
        self.post()
        with self.accounts.db() as db:db.execute("UPDATE bridge_replies SET mode='review',root='10',node='T1'")
        self.delete();self.bridge.tick()
        self.assertEqual(self.deleted,['/repos/one/repo/pulls/comments/44'])

    def test_discussion_deletion_uses_scoped_node_and_personal_token(self):
        with self.service.queue(self.binding).connect() as db:
            db.execute('DELETE FROM links');db.execute('INSERT INTO links VALUES(?,?,?)',('github-thread:one/repo:discussion:7','1002','7777'))
        self.post();self.delete();self.bridge.tick()
        self.assertEqual(self.deleted,['DC_44'])

    def test_discussion_api_errors_do_not_count_as_successful_deletion(self):
        with self.service.queue(self.binding).connect() as db:
            db.execute('DELETE FROM links');db.execute('INSERT INTO links VALUES(?,?,?)',('github-thread:one/repo:discussion:7','1002','7777'))
        self.post();self.delete();call=self.github.call.side_effect
        def failure(method,path,data=None):
            if path=='/graphql':return {'errors':[{'message':'private detail'}]}
            return call(method,path,data)
        self.github.call.side_effect=failure;self.bridge.tick()
        self.assertEqual(self.deleted,[]);self.assertEqual(self.job()['state'],'deleting')

    def test_ambiguous_post_is_looked_up_and_deleted_never_reposted(self):
        self.post();body=self.writes[0]['body']
        with self.accounts.db() as db:db.execute("UPDATE comment_outbox SET state='uncertain',remote=NULL")
        self.comments=[{'id':44,'html_url':'url','body':body,'user':{'id':91}}]
        self.delete();self.bridge.tick()
        self.assertEqual(len(self.writes),1);self.assertEqual(len(self.deleted),1)

    def test_rate_limit_waits_then_uses_same_receipt(self):
        self.post();self.delete();call=self.github.call.side_effect
        def limited(method,path,data=None):
            if method=='DELETE':raise ApiError('GitHub',429,retry_after=120)
            return call(method,path,data)
        self.github.call.side_effect=limited;self.bridge.tick()
        count=self.github.call.call_count;self.bridge.tick();self.assertEqual(self.github.call.call_count,count)
        self.clock.return_value+=121;self.github.call.side_effect=call;self.bridge.tick()
        self.assertEqual(len(self.deleted),1)

    def test_user_api_accepts_204_without_json_or_request_body(self):
        response=Mock();response.read.return_value=b'';response.headers={}
        opened=Mock();opened.__enter__=Mock(return_value=response);opened.__exit__=Mock(return_value=False)
        with patch('comment_bridge.urlopen',return_value=opened) as request:
            self.assertIsNone(UserApi(self.service,'secret').call('DELETE','/repos/one/repo/issues/comments/44'))
        self.assertIsNone(request.call_args[0][0].data)
