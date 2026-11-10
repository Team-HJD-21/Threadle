import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from delivery import Queue,CachedThreads
from discord_threads import Topic,Threads
from dnd_notifications import delivery_mode
from message_edits import deliver_events
from service import Service
from team_notifications import TeamNotifications,allowed_time
from test_team_features import binding,MessageApi
from test_message_edits import EditingDiscord

class DndTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.queue=Queue(Path(self.temp.name)/'queue.db')
        self.now=dt.datetime(2026,11,3,23,tzinfo=dt.timezone.utc).timestamp()
        self.options={'quiet':True,'quiet_start':22,'quiet_end':8,'utc_offset':0}
        self.api=MessageApi();self.b=binding(self.options)
        self.topic=Topic('owner/repo','pr',7,'Fix (mobile)','https://github.com/owner/repo/pull/7')
    def tearDown(self):self.temp.cleanup()
    def notices(self):return TeamNotifications(self.b,self.queue,self.api,clock=lambda:self.now)
    def test_quiet_boundaries_use_local_time_and_send_once_across_restarts(self):
        self.options['utc_offset'] = 9
        self.now = dt.datetime(2026, 11, 3, 13, tzinfo=dt.timezone.utc).timestamp()
        with patch('app_emojis.thumbnail', side_effect=lambda api, name: {'url': name}):
            self.notices().quiet_notice(self.now - 60)
            self.assertFalse(self.api.messages)
            self.notices().quiet_notice(self.now)
            self.notices().quiet_notice(self.now + 60)
            self.notices().quiet_notice(self.now + 10 * 3600)
        messages = [msg for _, msg in self.api.messages]
        self.assertEqual([msg['embeds'][0]['thumbnail']['url'] for msg in messages],
                         ['threadle_sleep', 'threadle_wake'])
        self.assertEqual(messages[0]['flags'], 4096)
        self.assertNotIn('flags', messages[1])
        self.options['quiet'] = False
        self.notices().quiet_notice(self.now + 86400)
        self.assertEqual(len(self.api.messages), 2)

    def test_all_three_kinds_are_grouped_once_after_restart(self):
        n=self.notices()
        for kind,number in [('pr',7),('issue',8),('discussion',9)]:
            topic=Topic('owner/repo',kind,number,'Title','https://github.com/owner/repo')
            for _ in range(2):n.topic(topic,'issue_comment',{},kind,'thread-'+kind,['201','104'])
        self.assertEqual(self.api.messages,[]);self.assertTrue(n.has_pending())
        self.now+=9*3600
        self.notices().flush();self.notices().flush()
        self.assertEqual(len(self.api.messages),1)
        path,msg=self.api.messages[0];self.assertEqual(path,'/channels/104/messages')
        for text in ('PR #7','Issue #8','Discussion #9'):self.assertIn(text,msg['embeds'][0]['description'])
        self.assertNotIn('flags',msg);self.assertFalse(self.notices().has_pending())
    def test_bundle_counts_activity_and_links_to_each_topic(self):
        n=self.notices();n.topic(self.topic,'threadle_bundle',{'_events':[{},{}]},'bundle','777',['104'])
        self.now+=9*3600;n.flush()
        text=self.api.messages[0][1]['embeds'][0]['description']
        self.assertIn('2건',text);self.assertIn('/10/777',text);self.assertIn('Fix (mobile)',text)
    def test_lost_roundup_response_recovers_without_repeating(self):
        n=self.notices();n.topic(self.topic,'issue_comment',{},'one','thread',['104']);self.now+=9*3600
        original=self.api.call;lost=[True]
        def call(method,path,data=None):
            value=original(method,path,data)
            if method=='POST' and lost[0]:lost[0]=False;raise RuntimeError('Lost response')
            return value
        self.api.call=call
        with self.assertRaises(RuntimeError):n.flush()
        self.notices().flush();self.assertEqual(len(self.api.messages),1)
    def test_large_roundup_is_paged_and_retry_safe(self):
        n=self.notices()
        for number in range(19):n.topic(Topic('owner/repo','issue',number,'x'*140,'https://github.com/owner/repo'),'issues',{},str(number),'thread',['104'])
        self.now+=9*3600;n.flush();n.flush()
        self.assertEqual(len(self.api.messages),3)
        self.assertTrue(all(len(m['embeds'][0]['description'])<3500 for _,m in self.api.messages))
    def test_delivery_mode_survives_retry_after_dnd_ends_but_workflows_bypass_it(self):
        until=delivery_mode(self.queue,'one','issue_comment',self.options,self.now)
        self.assertGreater(until,self.now)
        self.assertEqual(delivery_mode(self.queue,'one','issue_comment',self.options,self.now+9*3600),until)
        self.assertIsNone(delivery_mode(self.queue,'ci','workflow_run',self.options,self.now))
        self.assertIsNone(delivery_mode(self.queue,'outside','issues',self.options,self.now+9*3600))
    def test_root_detail_and_mutable_comment_are_silent(self):
        api=EditingDiscord();threads=CachedThreads(api,'g',{'pr':'c'},self.queue);threads.bot={'id':'bot'};threads.silent_notifications=True
        topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1')
        value={'action':'created','comment':{'id':7,'body':'Comment','html_url':topic.url+'#issuecomment-7'}}
        thread=deliver_events(threads,topic,'issue_comment',value,'one',lambda:False,'ko')
        self.assertEqual(next(iter(api.roots.values()))['flags'],4096)
        self.assertEqual(api.posts[thread][0]['flags'],4096)
        threads.deliver(topic,{'description':'State update'},'state',lambda:False)
        self.assertEqual(api.posts[thread][-1]['flags'],4096)
        threads.silent_notifications=False
        threads.deliver(topic,{'description':'Normal update'},'normal',lambda:False)
        self.assertNotIn('flags',api.posts[thread][-1])
    def test_mode_is_checked_for_each_post_at_dnd_boundary(self):
        api=EditingDiscord();threads=CachedThreads(api,'g',{'pr':'c'},self.queue);threads.bot={'id':'bot'}
        mode=Mock(side_effect=[False,True,True]);threads.silent_notifications=mode
        topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1')
        thread=threads.deliver(topic,{'description':'Body'},'boundary',lambda:False)
        self.assertNotIn('flags',next(iter(api.roots.values())))
        self.assertEqual(api.posts[thread][0]['flags'],4096)

    def test_service_sets_silent_before_detail_delivery(self):
        service=Service(Path(self.temp.name)/'service',{})
        config=dict(self.b.config,routing_groups=[])
        identity=service.registry.add('tenant',11,'owner/repo',1,'app',config)
        b=service.registry.get(identity);q=service.queue(b)
        value={'repository':{'id':11,'full_name':'owner/repo'},'installation':{'id':1},'action':'created','issue':{'number':7,'title':'Title'},'comment':{'id':7,'body':'Hello'}}
        q.put('one','issue_comment',value)
        github=Mock();github.call.return_value={'id':11};service.github=lambda _:github
        threads=Mock();threads.deliver.return_value='thread';notices=Mock()
        with patch('service.time.time',return_value=self.now):self.assertTrue(service.process(b,threads,None,notices))
        self.assertTrue(threads.silent_notifications)
        self.assertEqual(notices.topic.call_args.kwargs['dnd_until'],allowed_time(self.now,self.options))
    def test_pending_legacy_alerts_become_one_roundup_and_ci_is_released(self):
        n=self.notices();until=allowed_time(self.now,self.options)
        with self.queue.connect() as db:
            db.execute('DELETE FROM dnd_migrations')
            for event,key,title in [('a',self.topic.key,'PR #7 · Updated'),('b',self.topic.key,'PR #7 · Closed'),('ci','owner/repo:ci:1','CI failed · Build')]:
                db.execute('INSERT INTO notice_events(channel,event_key,topic,title,description,url,color,due,bucket) VALUES(?,?,?,?,?,?,?,?,?)',('104',event,key,title,'detail','https://discord.com/channels/10/777',1,until,str(int(until))))
        n=self.notices();n.flush()
        self.assertEqual(len(self.api.messages),1);self.assertIn('CI failed',self.api.messages[0][1]['embeds'][0]['title'])
        self.now=until;n.flush();n.flush()
        self.assertEqual(len(self.api.messages),2)
        self.assertIn('방해금지',self.api.messages[-1][1]['embeds'][0]['title'])
    def test_english_dnd_summary_and_ci_topic_bypass(self):
        self.b.config['language']='en';n=self.notices()
        n.topic(self.topic,'workflow_run',{},'ci-pr','777',['104'])
        self.assertEqual(len(self.api.messages),1)
        n.topic(self.topic,'issue_comment',{},'one','777',['104']);self.now+=9*3600;n.flush()
        self.assertIn('Do Not Disturb',self.api.messages[-1][1]['embeds'][0]['title'])
