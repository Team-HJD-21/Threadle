import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from delivery import CachedThreads
from discord_threads import ApiError
from service import Service
from test_message_edits import EditingDiscord
from test_team_features import binding
from deferred_threads import wake

class DeferredApi(EditingDiscord):
    def call(self,method,path,data=None):
        if method=='GET' and path.startswith('/channels/') and '/messages' not in path and path.rsplit('/',1)[-1] not in ('c','g') and '/threads/' not in path:
            if path.rsplit('/',1)[-1] not in self.threads:raise ApiError('Discord',404,10003)
        if method=='PATCH' and path.startswith('/channels/c/messages/'):
            row=self.roots[path.rsplit('/',1)[-1]];row.update(copy.deepcopy(data));return copy.deepcopy(row)
        if method=='DELETE' and path.startswith('/channels/c/messages/'):
            self.roots.pop(path.rsplit('/',1)[-1],None);return None
        return super().call(method,path,data)

class DeferredThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Path(self.temp.name),{})
        self.now=dt.datetime(2026,11,3,23,tzinfo=dt.timezone.utc).timestamp()
        options={'quiet':True,'quiet_start':22,'quiet_end':8,'utc_offset':0}
        config=dict(binding(options).config,guild_id='g',channels={'pr':'c','issue':'c','discussion':'c'},routing_groups=[])
        identity=self.service.registry.add('tenant',11,'owner/repo',1,'app',binding(options).config)
        self.b=self.service.registry.get(identity);self.b.config.update(config);self.service.registry.get=lambda *args,**kwargs:self.b
        self.q=self.service.queue(self.b)
        self.api=DeferredApi();self.threads=CachedThreads(self.api,'g',config['channels'],self.q);self.threads.bot={'id':'bot'}
        self.current={'number':1,'title':'Title','state':'open','body':'Body','head':{'ref':'feature'},'base':{'ref':'main'}}
        self.github=Mock();self.github.pages.return_value=[];self.github.call.side_effect=lambda method,path,data=None: {'id':11} if path.startswith('/repositories/') else dict(self.current)
        self.service.github=lambda _:self.github
        self.notices=Mock()
    def tearDown(self):self.temp.cleanup()
    def value(self,action='opened',number=1):
        return {'repository':{'id':11,'full_name':'owner/repo'},'installation':{'id':1},'action':action,'pull_request':dict(self.current,number=number),'sender':{'login':'author'}}
    def process(self,window=0):
        with patch('service.time.time',side_effect=lambda:self.now):return self.service.process(self.b,self.threads,None,self.notices,window_seconds=window)
    def test_root_only_then_release_after_restart(self):
        self.q.put('one','pull_request',self.value());self.assertTrue(self.process())
        self.assertEqual(len(self.api.roots),1);self.assertFalse(self.api.threads);self.assertFalse(self.api.posts)
        self.assertEqual(next(iter(self.api.roots.values()))['flags'],4096)
        self.assertFalse(self.process())
        self.threads=CachedThreads(self.api,'g',self.b.config['channels'],self.q);self.threads.bot={'id':'bot'}
        self.now+=9*3600;self.assertTrue(self.process())
        self.assertEqual(self.api.starts,1);self.assertEqual(len(self.api.roots),1)
        self.assertTrue(self.api.posts);self.assertFalse(self.process())
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0],1)
    def test_existing_threads_continue_immediately_silent(self):
        self.now-=12*3600;self.q.put('one','pull_request',self.value());self.process();self.now+=12*3600
        value=self.value('created');value['comment']={'id':7,'body':'Hello','html_url':'https://github.com/comment/7'}
        self.q.put('comment','pull_request_review_comment',value);self.process()
        self.assertEqual(self.api.starts,1)
        self.assertEqual(next(iter(self.api.posts.values()))[-1]['flags'],4096)
        with self.q.connect() as db:self.assertEqual(db.execute("SELECT done FROM jobs WHERE id='comment'").fetchone()[0],1)
    def test_current_state_updates_root_without_thread(self):
        self.q.put('one','pull_request',self.value());self.process()
        self.current.update(state='closed',merged=True)
        self.q.put('closed','pull_request',self.value('closed'));self.process()
        self.assertFalse(self.api.threads)
        self.assertIn('PR Merged',next(iter(self.api.roots.values()))['embeds'][0]['title'])
    def test_release_pace_does_not_block_fresh_jobs(self):
        self.q.put('one','pull_request',self.value());self.process()
        self.q.put('two','pull_request',self.value(number=2));self.process()
        self.now+=9*3600;self.process()
        self.q.put('ci','workflow_run',{'repository':{'id':11}})
        with patch('delivery.time.time',return_value=self.now):self.assertEqual(self.q.next()[0],'ci')
        self.now+=1
        with patch('delivery.time.time',return_value=self.now):self.assertEqual(self.q.next()[0],'two')
    def test_deleted_comment_is_not_replayed(self):
        value=self.value('created');value['comment']={'id':7,'body':'Hello','html_url':'https://github.com/comment/7'}
        self.q.put('comment','pull_request_review_comment',value);self.process()
        deleted=copy.deepcopy(value);deleted['action']='deleted'
        self.q.put('delete','pull_request_review_comment',deleted);self.process()
        self.now+=9*3600;self.process()
        self.assertFalse(self.api.threads);self.assertFalse(self.api.posts)
    def test_disabling_dnd_wakes_pending_jobs(self):
        self.q.put('one','pull_request',self.value());self.process()
        self.b.config['team_notifications']['quiet']=False
        wake(self.q);self.process()
        self.assertEqual(self.api.starts,1)
    def test_live_missing_issue_is_removed_without_thread(self):
        value=self.value();value['issue']=value.pop('pull_request')
        def call(method,path,data=None):
            if path.startswith('/repositories/'):return {'id':11}
            raise ApiError('GitHub',410)
        self.github.call.side_effect=call
        self.q.put('one','issues',value);self.process()
        self.assertFalse(self.api.roots);self.assertFalse(self.api.threads)
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0],1)
    def test_new_issue_and_discussion_wait_and_deliver(self):
        for kind,event in [('issue','issues'),('discussion','discussion')]:
            with self.subTest(kind=kind):
                value=self.value(number=2 if kind=='issue' else 3);obj=value.pop('pull_request');value[kind]=obj
                def call(method,path,data=None):
                    if path.startswith('/repositories/'):return {'id':11}
                    if path=='/graphql':return {'data':{'repository':{'discussion':{'title':'Title','closed':False}}}}
                    return dict(obj)
                self.github.call.side_effect=call
                self.q.put(kind,event,value);self.process()
        self.assertEqual(len(self.api.roots),2);self.assertFalse(self.api.threads)
        self.now+=9*3600;self.process();self.now+=1;self.process()
        self.assertEqual(self.api.starts,2)

    def test_deleted_waiting_issue_does_not_create_thread_at_release(self):
        value=self.value();value['issue']=value.pop('pull_request')
        self.q.put('one','issues',value);self.process();self.assertEqual(len(self.api.roots),1)
        def call(method,path,data=None):
            if path.startswith('/repositories/'):return {'id':11}
            raise ApiError('GitHub',410)
        self.github.call.side_effect=call;self.now+=9*3600;self.process()
        self.assertFalse(self.api.roots);self.assertFalse(self.api.threads)

    def test_roundup_waits_until_deferred_jobs_are_finished(self):
        from team_notifications import TeamNotifications
        from test_team_features import MessageApi
        from discord_threads import Topic
        api=MessageApi();notices=TeamNotifications(self.b,self.q,api,clock=lambda:self.now)
        self.q.put('one','pull_request',self.value());self.process()
        notices.topic(Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1'),'pull_request',self.value(),'webhook:one',next(iter(self.api.roots)),['104'])
        self.now+=9*3600;notices.flush();self.assertFalse(api.messages)
        self.process();notices.flush();self.assertEqual(len(api.messages),1)

    def test_creation_boundary_defers_without_failed_attempt(self):
        self.now-=12*3600;original=self.api.call
        def call(method,path,data=None):
            result=original(method,path,data)
            if method=='POST' and path=='/channels/c/messages':self.now+=12*3600
            return result
        self.api.call=call;self.q.put('one','pull_request',self.value());self.process()
        self.assertEqual(len(self.api.roots),1);self.assertFalse(self.api.threads)
        with self.q.connect() as db:
            done,attempts,due=db.execute('SELECT done,attempts,next FROM jobs').fetchone()
        self.assertEqual((done,attempts),(0,0));self.assertGreater(due,self.now)

    def test_workflows_bypass_deferral(self):
        from deferred_threads import defer
        self.assertFalse(defer(self.threads,None,None,'workflow_run',{},'ci',self.b.config['team_notifications'],self.now))

    def test_deleted_only_pending_comment_is_removed_from_roundup(self):
        from team_notifications import TeamNotifications
        from test_team_features import MessageApi
        from discord_threads import Topic
        api=MessageApi();notices=TeamNotifications(self.b,self.q,api,clock=lambda:self.now)
        value=self.value('created');value['comment']={'id':7,'body':'Hello','html_url':'https://github.com/comment/7'}
        self.q.put('comment','pull_request_review_comment',value);self.process()
        notices.topic(Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1'),'pull_request_review_comment',value,'webhook:comment',next(iter(self.api.roots)),['104'])
        deleted=copy.deepcopy(value);deleted['action']='deleted'
        self.q.put('delete','pull_request_review_comment',deleted);self.process()
        self.now+=9*3600;self.process();notices.flush()
        self.assertFalse(api.messages);self.assertFalse(self.api.threads)

    def test_waiting_parent_keeps_its_original_channel(self):
        self.q.put('one','pull_request',self.value());self.process()
        self.threads.channels=dict(self.threads.channels,pr='other')
        self.now+=9*3600;self.process()
        self.assertEqual(self.api.starts,1);self.assertEqual(len(self.api.roots),1)
        self.assertFalse(any(method=='POST' and path=='/channels/other/messages' for method,path,data in self.api.calls))

    def test_coalesced_jobs_resume_as_one_durable_group(self):
        value=self.value('created');value['comment']={'id':7,'body':'Hello','html_url':'https://github.com/comment/7'}
        with patch('delivery.time.time',return_value=self.now-10):
            self.q.put('one','pull_request',self.value())
            self.q.put('comment','pull_request_review_comment',value)
        self.process(window=8)
        self.assertEqual(len(self.api.roots),1);self.assertFalse(self.api.threads)
        self.assertFalse(self.process(window=8))
        self.now+=9*3600;self.process(window=8)
        self.assertEqual(self.api.starts,1)
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT sum(done) FROM jobs').fetchone()[0],2)
        self.assertEqual(len(next(iter(self.api.posts.values()))),2)
