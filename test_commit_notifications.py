import unittest
import copy
from unittest.mock import Mock
from commit_notifications import enrich_commits,completed
from discord_threads import Topic,make_embed
from delivery import Queue,CachedThreads
from discord_threads import ApiError
import test_message_edits as edits
import test_service as service_fixtures


class CommitTests(unittest.TestCase):
    def test_titles_and_links_replace_pr_description(self):
        github=Mock();github.call.return_value={'total_commits':2,'commits':[{'sha':'a'*40,'commit':{'message':'Fix movement\nDetails'}},{'sha':'b'*40,'commit':{'message':'Update UI'}}]}
        payload={'action':'synchronize','before':'c'*40,'after':'b'*40,'pull_request':{'body':'Do not repeat this PR description'}}
        result=enrich_commits(github,'owner/repo','pull_request',payload)
        embed=make_embed(Topic('owner/repo','pr',1,'PR','https://github.com/owner/repo/pull/1'),'pull_request',result,'ko')
        self.assertIn('Fix movement',embed['description']);self.assertIn('Update UI',embed['description'])
        self.assertNotIn('Do not repeat',embed['description']);self.assertNotIn('Details',embed['description'])
        self.assertIn('/commit/'+'a'*40,embed['description'])
    def test_failed_comparison_keeps_link_without_repeating_body(self):
        github=Mock();github.call.side_effect=RuntimeError('unavailable')
        payload={'action':'synchronize','before':'a'*40,'after':'b'*40,'pull_request':{'body':'Do not repeat'}}
        result=enrich_commits(github,'owner/repo','pull_request',payload)
        embed=make_embed(Topic('owner/repo','pr',1,'PR','url'),'pull_request',result,'en')
        self.assertIn('compare/',embed['description']);self.assertNotIn('Do not repeat',embed['description'])


class CommitDeduplicationTests(unittest.TestCase):
    setUp=edits.EditTests.setUp
    tearDown=edits.EditTests.tearDown
    send=edits.EditTests.send

    def change(self,before='a',after='b'):
        return {'action':'synchronize','before':before*40,'after':after*40,
                'pull_request':copy.deepcopy(self.topic.metadata),'sender':{'login':'author'},
                '_new_commits':[{'sha':after*40,'title':'New work'}],'_commit_total':1}

    def test_distinct_webhook_ids_for_one_transition_make_one_message_after_restart(self):
        payload=self.change();thread=self.send(payload,'delivery-one','pull_request')
        first=self.api.posts[thread][0]['id']
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        self.send(payload,'delivery-two','pull_request')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.assertEqual(self.api.posts[thread][0]['id'],first)

    def test_new_transition_is_not_folded_into_previous_commits(self):
        thread=self.send(self.change(),'one','pull_request')
        self.send(self.change('b','c'),'two','pull_request')
        # Returning to an old head is a different, legitimate transition.
        self.send(self.change('c','b'),'three','pull_request')
        self.assertEqual(len(self.api.posts[thread]),3)

    def test_same_transition_in_different_prs_is_delivered_to_both(self):
        self.send(self.change(),'one','pull_request')
        self.topic=Topic('owner/repo','pr',2,'Other','https://github.com/owner/repo/pull/2',dict(self.topic.metadata,number=2))
        self.send(self.change(),'two','pull_request')
        self.assertEqual(sum(map(len,self.api.posts.values())),2)

    def test_commit_card_never_adopts_or_overwrites_pr_description(self):
        thread=self.send({'action':'opened','pull_request':self.topic.metadata},'opened','pull_request')
        original=copy.deepcopy(self.api.posts[thread][0])
        start=len(self.api.calls)
        self.send(self.change(),'commits','pull_request')
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertEqual(self.api.posts[thread][0],original)
        self.assertIn('New work',self.api.posts[thread][1]['embeds'][0]['description'])
        self.assertFalse(any(method=='GET' and '/messages?' in path for method,path,_ in self.api.calls[start:]))

    def test_lost_response_recovers_card_even_with_another_delivery_id(self):
        self.api.lose_post=True
        with self.assertRaises(ApiError):self.send(self.change(),'one','pull_request')
        self.api.nonces.clear()  # Receipt recovery must work beyond Discord's nonce window.
        thread=self.send(self.change(),'redelivery','pull_request')
        self.assertEqual(len(self.api.posts[thread]),1)

    def test_identical_bundle_entries_and_event_variants_share_one_card(self):
        payload={'_events':[{'event':event,'payload':self.change()} for event in ('pull_request','pull_request_target')]}
        thread=self.send(payload,'bundle','threadle_bundle')
        self.assertEqual(len(self.api.posts[thread]),1)


class CommitServiceTests(unittest.TestCase):
    setUp=service_fixtures.ServiceTests.setUp
    tearDown=service_fixtures.ServiceTests.tearDown
    send=service_fixtures.ServiceTests.send

    def setup_change(self):
        self.binding=self.service.registry.get(self.a)
        self.change={'action':'synchronize','before':'a'*40,'after':'b'*40,
                     'pull_request':{'number':7,'title':'Work','head':{'sha':'b'*40},'state':'open'}}
        self.threads,self.feed,self.github=Mock(),Mock(),Mock()
        self.threads.deliver.return_value='thread-one'
        self.github.call.return_value={'id':11}
        self.service.github=lambda _:self.github

    def enqueue(self,identity):
        self.assertEqual(self.send(event='pull_request',extra=self.change,delivery=identity*32),202)
        self.service.dispatch()

    def test_duplicate_is_skipped_before_repeat_comparison_and_secondary_alert(self):
        self.setup_change();self.enqueue('a')
        self.service.process(self.binding,self.threads,self.feed)
        calls=self.github.call.call_count
        self.enqueue('b');self.service.process(self.binding,self.threads,self.feed)
        self.assertEqual(self.threads.deliver.call_count,1)
        self.assertEqual(self.feed.topic.call_count,1)
        self.assertEqual(self.github.call.call_count,calls)
        self.assertTrue(completed(Queue(self.service.queue(self.binding).path),'pull_request',self.change))

    def test_failed_alert_does_not_mark_transition_done_or_block_retry(self):
        self.setup_change();self.enqueue('a')
        self.feed.topic.side_effect=ApiError('Discord','network')
        self.service.process(self.binding,self.threads,self.feed)
        queue=self.service.queue(self.binding)
        self.assertFalse(completed(queue,'pull_request',self.change))
        with queue.connect() as db:db.execute('UPDATE jobs SET next=0')
        self.feed.topic.side_effect=None
        self.service.process(self.binding,self.threads,self.feed)
        self.assertTrue(completed(queue,'pull_request',self.change))
        self.assertEqual(self.feed.topic.call_args.args[-2],'pr-commits:7:'+('a'*40)+':'+('b'*40))

    def test_duplicate_filter_is_isolated_between_discord_servers(self):
        self.setup_change()
        other=self.service.registry.add('another',11,'one/repo',101,'app',service_fixtures.config(4000))
        self.enqueue('a')
        self.service.process(self.binding,self.threads,self.feed)
        self.service.process(self.service.registry.get(other),self.threads,self.feed)
        self.assertEqual(self.feed.topic.call_count,2)
