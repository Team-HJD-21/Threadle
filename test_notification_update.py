import copy
import datetime as dt
import json
import sqlite3
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from connect_store import Sessions
from delivery import Queue, CachedThreads
from discord_connect import DiscordConnect
from discord_threads import Topic, make_embed, topic_from, silent_review, timestamp
from repository_summary import open_items, summary_page, issue_digest
from service import Service
from team_notifications import TeamNotifications
from test_discord_threads import FakeDiscord
from test_service import config
from test_team_features import MessageApi, binding


class NotificationUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.queue = Queue(self.root / 'queue.sqlite3')

    def test_metadata_events_keep_changed_person_label_or_milestone_without_body(self):
        topic = Topic('team/repo', 'pr', 7, 'PR', 'https://github.com/team/repo/pull/7')
        for action, key, item in [('assigned','assignee',{'login':'dev'}), ('unassigned','assignee',{'login':'dev'}),
                                  ('milestoned','milestone',{'title':'Launch'}), ('demilestoned','milestone',{'title':'Launch'}),
                                  ('labeled','label',{'name':'backend'}), ('unlabeled','label',{'name':'backend'})]:
            payload = {'action':action,'pull_request':{'body':'DO NOT REPEAT'},key:item}
            result = make_embed(topic,'pull_request',payload,'en')
            self.assertNotIn('DO NOT REPEAT',result['description'])
            self.assertIn(next(iter(item.values())),result['description'])
        payload = {'action':'converted_to_draft','pull_request':{'body':'DO NOT REPEAT'}}
        self.assertNotIn('DO NOT REPEAT',make_embed(topic,'pull_request',payload)['description'])
        payload['action'] = 'edited'
        self.assertIn('DO NOT REPEAT',make_embed(topic,'pull_request',payload)['description'])

    def test_commented_review_keeps_body_and_time_without_review_result(self):
        topic = Topic('team/repo','pr',1,'Title','https://github.com/team/repo/pull/1')
        payload = {'action':'submitted','review':{'state':'commented','body':'Please keep this comment.', 'submitted_at':'2026-11-01T01:00:00Z'}}
        for language in ('ko','en'):
            result = make_embed(topic,'pull_request_review',payload,language)
            self.assertIn('Please keep this comment.',result['description'])
            self.assertNotIn('commented',result['description'])
            self.assertEqual(result['timestamp'],'2026-11-01T01:00:00+00:00')
        self.assertNotIn('<t:',result['description'])
        self.assertFalse(silent_review('pull_request_review',payload))
        payload['review']['body'] = '  '
        self.assertTrue(silent_review('pull_request_review',payload))
        payload['review']['state'] = 'approved'
        self.assertFalse(silent_review('pull_request_review',payload))

    def test_new_root_shows_original_author_branches_and_viewer_local_timestamp(self):
        topic = topic_from('team/repo','pr',{'number':1,'title':'Title','user':{'login':'original-author'},
            'head':{'ref':'feature/one'},'base':{'ref':'main'},'created_at':'2026-11-01T01:00:00Z'})
        api = FakeDiscord()
        threads = CachedThreads(api,'g',{'pr':'c'},self.queue,'en')
        threads.bot = {'id':'bot'}
        threads.deliver(topic,{'description':'body'},'first',lambda:False)
        text = next(iter(api.roots.values()))['embeds'][0]['description']
        self.assertEqual(next(iter(api.roots.values()))['embeds'][0]['author']['name'],'original-author')
        self.assertIn('`feature/one` → `main`',text)
        self.assertEqual(next(iter(api.roots.values()))['embeds'][0]['timestamp'],'2026-11-01T01:00:00+00:00')
        self.assertNotIn('Opened by',text)
        self.assertNotIn('stay in this thread',text)
        self.assertEqual(timestamp('not-a-date'),'')

    def test_normal_delivery_cost_does_not_grow_with_old_thread_history(self):
        api = FakeDiscord()
        threads = CachedThreads(api,'g',{'pr':'c'},self.queue)
        threads.bot = {'id':'bot'}
        topic = Topic('team/repo','pr',1,'Title','https://github.com/team/repo/pull/1')
        thread = threads.deliver(topic,{'description':'first'},'one',lambda:False)
        api.posts[thread] += [{'id':str(n),'embeds':[]} for n in range(1000)]
        api.calls.clear()
        restarted = CachedThreads(api,'g',{'pr':'c'},self.queue)
        restarted.bot = {'id':'bot'}
        restarted.deliver(topic,{'description':'second'},'two',lambda:False)
        self.assertFalse(any('/messages?' in path for method,path,_ in api.calls))
        api.calls.clear()
        restarted.deliver(topic,{'description':'second'},'two',lambda:False)
        self.assertFalse(any(method=='POST' and path.endswith('/messages') for method,path,_ in api.calls))

    def test_lost_message_ack_is_recovered_after_restart_without_duplicate(self):
        api = FakeDiscord()
        threads = CachedThreads(api,'g',{'pr':'c'},self.queue)
        threads.bot = {'id':'bot'}
        topic = Topic('team/repo','pr',1,'Title','https://github.com/team/repo/pull/1')
        threads.deliver(topic,{'description':'first'},'one',lambda:False)
        call = api.call
        def lost(method,path,data=None):
            result = call(method,path,data)
            if method=='POST' and path.endswith('/messages'):
                raise RuntimeError('response lost after accepted send')
            return result
        api.call = lost
        with self.assertRaises(RuntimeError):
            threads.deliver(topic,{'description':'second'},'two',lambda:False)
        api.call = call
        restarted = CachedThreads(api,'g',{'pr':'c'},self.queue)
        restarted.bot = {'id':'bot'}
        restarted.deliver(topic,{'description':'second'},'two',lambda:False)
        self.assertEqual(sum(len(v) for v in api.posts.values()),2)

    def test_root_index_recovers_manually_renamed_thread_and_interrupted_start(self):
        api = FakeDiscord()
        topic = Topic('team/repo','pr',1,'Title','https://github.com/team/repo/pull/1')
        root = api.call('POST','/channels/c/messages',{'nonce':'old-root','embeds':[{'footer':{'text':topic.key}}]})
        original = api.call('POST',f'/channels/c/messages/{root["id"]}/threads',{'name':'manually renamed'})
        threads = CachedThreads(api,'g',{'pr':'c'},self.queue)
        threads.bot = {'id':'bot'}
        threads.deliver(topic,{'description':'new'},'one',lambda:False)
        self.assertEqual(len(api.roots),1)
        self.assertEqual(api.starts,1)
        self.assertEqual(original['id'],next(iter(api.posts)))

    def test_open_summary_lists_titles_for_every_kind_and_supports_next_page(self):
        api = Mock()
        for kind, field in [('pr','pullRequests'),('issue','issues'),('discussion','discussions')]:
            nodes = [{'number':i,'title':f'Remaining title {i}','url':'https://github.com/team/repo/issues/'+str(i)} for i in range(1,18)]
            if kind=='discussion':
                nodes.append({'number':18,'title':'closed','closed':True})
            api.call.return_value = {'data':{'repository':{field:{'totalCount':len(nodes),'nodes':nodes,'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
            items = open_items(api,'team/repo',kind)
            first, second = summary_page('team/repo',kind,items), summary_page('team/repo',kind,items,2)
            self.assertIn('17 open',first)
            self.assertIn('Remaining title 1',first)
            self.assertIn('Remaining title 17',second)
            self.assertNotIn('closed',first)
            self.assertLess(len(first.encode('utf-16-le'))//2,1900)

    def test_completed_issues_are_sorted_by_closed_time_and_not_last_comment(self):
        api = Mock()
        def response(nodes, more=False, after=None):
            return {'data':{'repository':{'oldest':{'totalCount':11,'nodes':[{'number':1,'title':'Old','url':'https://github.com/team/repo/issues/1'}]},
                'completed':{'nodes':nodes,'pageInfo':{'hasNextPage':more,'endCursor':after}}}}}
        def item(number,closed,updated,reason='COMPLETED'):
            return {'number':number,'title':str(number),'url':'https://github.com/team/repo/issues/'+str(number),'closedAt':closed,'updatedAt':updated,'stateReason':reason}
        api.call.side_effect = [response([item(2,'2020-01-01','2026-11-01'),item(3,'2026-10-31','2026-10-31','NOT_PLANNED')],True,'next'),
                                 response([item(n,f'2026-10-{n:02}',f'2026-10-{n:02}') for n in range(10,16)])]
        count, oldest, completed = issue_digest(api,'team/repo')
        self.assertEqual(count,11)
        self.assertEqual([i['number'] for i in completed],[15,14,13,12,11])
        self.assertEqual(api.call.call_count,2)

    def test_autocomplete_is_signed_and_scoped_to_the_current_discord_server(self):
        service = Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        identity = service.registry.add('one',11,'team/one',101,'app',config(1000))
        service.registry.add('two',22,'team/private',202,'app',config(2000))
        sessions = Sessions(self.root/'connect.sqlite3',Fernet.generate_key().decode())
        private = Ed25519PrivateKey.generate()
        endpoint = DiscordConnect(service,SimpleNamespace(discord_id='9000'),private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),sessions)
        payload = {'type':4,'application_id':'9000','guild_id':'1000','member':{'permissions':'32','user':{'id':'1'}},
                   'data':{'name':'summary','options':[{'name':'repository','focused':True,'value':''}]}}
        def receive():
            body = json.dumps(payload).encode()
            stamp = str(int(time.time()))
            return endpoint.receive(body,private.sign(stamp.encode()+body).hex(),stamp)
        self.assertEqual(receive()[1],{'type':8,'data':{'choices':[{'name':'team/one','value':identity}]}})
        payload['member']['permissions']='0'
        self.assertEqual(receive()[1]['data']['choices'],[])

    def test_daily_digest_includes_unassigned_pr_and_issue_sections_once(self):
        now = dt.datetime(2026,11,1,0,0,tzinfo=dt.timezone.utc).timestamp()
        notices = TeamNotifications(binding({'digest':'daily','digest_hour':9,'utc_offset':9,'enabled_at':now-1}),self.queue,MessageApi(),clock=lambda:now)
        api = Mock()
        api.pages.return_value = [{'number':1,'title':'Unassigned open PR'}]
        api.call.return_value = {'data':{'repository':{'oldest':{'totalCount':11,'nodes':[{'number':2,'title':'Oldest issue','url':'https://github.com/owner/repo/issues/2'}]},
            'completed':{'nodes':[{'number':3,'title':'Completed issue','url':'https://github.com/owner/repo/issues/3','stateReason':'COMPLETED','closedAt':'2026-10-31','updatedAt':'2026-10-31'}],
                         'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
        notices.digest(api)
        notices.digest(api)
        self.assertEqual(len(notices.api.messages),1)
        embeds = notices.api.messages[0][1]['embeds']
        self.assertIn('Unassigned open PR',embeds[0]['description'])
        self.assertIn('Oldest issue',embeds[1]['description'])
        self.assertIn('11 remaining issues',embeds[1]['footer']['text'])
        self.assertEqual(len(embeds),2)
        self.assertNotIn('Completed issue',str(embeds))

    def test_full_first_server_queue_does_not_block_other_server_fanout(self):
        service = Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        first = service.registry.add('one',11,'team/repo',101,'app',config(1000))
        second = service.registry.add('two',11,'team/repo',101,'app',config(2000))
        payload = {'repository':{'id':11,'full_name':'team/repo'},'installation':{'id':101},'issue':{'number':1}}
        service.registry.receive('app','event-one','issues',payload)
        first_queue = service.queue(service.registry.get(first))
        original = first_queue.put
        first_queue.put = Mock(side_effect=sqlite3.OperationalError('queue full'))
        service.dispatch()
        second_queue = service.queue(service.registry.get(second))
        self.assertIsNotNone(second_queue.next())
        self.assertTrue(service.registry.pending())
        first_queue.put = original
        service.dispatch()
        self.assertIsNotNone(first_queue.next())
        self.assertFalse(service.registry.pending())
        with second_queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],1)

    def test_admin_separates_current_and_disconnected_connections(self):
        service = Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        service.registry.add('one',11,'team/current',101,'app',config(1000))
        past = service.registry.add('two',22,'team/old',202,'app',config(2000))
        service.registry.disable(past)
        body = service.operator.dashboard(SimpleNamespace(data={'csrf':'test'}),{'email':'owner@example.com'},'')
        current, disconnected = body.split('연결 해제 기록',1)
        self.assertIn('team/current',current)
        self.assertNotIn('team/old',current)
        self.assertIn('team/old',disconnected)
