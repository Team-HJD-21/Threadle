import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from delivery import Queue,CachedThreads
from discord_threads import ApiError,Topic,marker
from root_status import TopicRoots,starter_embed
from test_message_edits import EditingDiscord

class RootDiscord(EditingDiscord):
    def __init__(self):super().__init__();self.lose_delete=False
    def call(self,method,path,data=None):
        if path.startswith('/channels/c/messages/'):
            identity=path.split('/')[4]
            if not path.endswith('/threads'):
                row=self.roots.get(identity)
                if row is None:raise ApiError('Discord',404,10008)
                if method=='PATCH':self.calls.append((method,path,copy.deepcopy(data)));row.update(copy.deepcopy(data));return copy.deepcopy(row)
                if method=='DELETE':self.calls.append((method,path,data));del self.roots[identity];return None
        if path.startswith('/channels/') and len(path.split('/'))==3:
            identity=path.split('/')[2]
            if identity!='c' and identity not in self.threads:raise ApiError('Discord',404,10003)
            if method=='DELETE':
                self.calls.append((method,path,data));self.threads.pop(identity);self.posts.pop(identity,None)
                if self.lose_delete:self.lose_delete=False;raise ApiError('Discord','network')
                return None
        return super().call(method,path,data)

class RootStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.queue=Queue(Path(self.temp.name)/'queue.db');self.api=RootDiscord()
        self.threads=CachedThreads(self.api,'g',{'pr':'c','issue':'c','discussion':'c'},self.queue);self.threads.bot={'id':'bot'}
        self.topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1',{'state':'open','created_at':'2026-11-03T00:00:00Z','user':{'login':'author'},'head':{'ref':'feature'},'base':{'ref':'main'}})
        self.github=Mock()
    def tearDown(self):self.temp.cleanup()
    def root(self,topic=None):
        topic=topic or self.topic;thread=self.threads.ensure(topic);self.api.threads[thread['id']]['owner_id']='bot';return thread['id']
    def refresh(self,obj,topic=None):
        self.github.call.return_value=obj
        return TopicRoots(self.threads,self.github).closed(topic or self.topic)
    def test_pr_starter_transitions_keep_id_author_time_marker_and_branch_metadata(self):
        identity=self.root();old=copy.deepcopy(self.api.roots[identity]['embeds'][0])
        for obj,state,closed in [({'state':'open','draft':True},'Draft',False),({'state':'open','draft':False},'Open',False),({'state':'closed'},'Closed',True),({'state':'open'},'Open',False),({'state':'closed','merged':True},'Merged',True)]:
            self.assertEqual(self.refresh(obj),closed)
            embed=self.api.roots[identity]['embeds'][0]
            self.assertEqual(embed['title'],'PR #1 · Title')
            self.assertEqual(embed['fields'][-1]['value'],'`'+state+'`')
            for key in ('author','timestamp','footer','description'):self.assertEqual(embed[key],old[key])
        self.assertEqual(len(self.api.roots),1);self.assertEqual(self.api.starts,1)
    def test_discord_read_only_fields_are_not_reposted_and_do_not_cause_repeated_patches(self):
        identity=self.root();embed=self.api.roots[identity]['embeds'][0]
        embed['type']='rich';embed['author'].update(icon_url='https://example.com/icon.png',proxy_icon_url='https://proxy.example.com/icon.png')
        self.refresh({'state':'closed'})
        patched=[c[2]['embeds'][0] for c in self.api.calls if c[0]=='PATCH' and '/messages/' in c[1]][-1]
        self.assertNotIn('type',patched);self.assertNotIn('proxy_icon_url',patched['author'])
        self.assertEqual(patched['author']['icon_url'],'https://example.com/icon.png')
        before=len([c for c in self.api.calls if c[0]=='PATCH'])
        self.api.roots[identity]['embeds'][0]['type']='rich'
        self.refresh({'state':'closed'});self.assertEqual(len([c for c in self.api.calls if c[0]=='PATCH']),before)

    def test_same_state_retries_do_not_patch_again(self):
        self.root();self.refresh({'state':'closed'});before=len([c for c in self.api.calls if c[0]=='PATCH'])
        self.refresh({'state':'closed'});self.assertEqual(len([c for c in self.api.calls if c[0]=='PATCH']),before)
    def test_discussion_answered_unanswered_closed_and_reopened(self):
        topic=Topic('owner/repo','discussion',2,'Discuss','https://github.com/owner/repo/discussions/2')
        identity=self.root(topic)
        for value,state in [({'closed':False,'isAnswered':True},'Answered'),({'closed':False,'isAnswered':False},'Open'),({'closed':True,'isAnswered':True},'Answered'),({'closed':True,'isAnswered':False},'Closed'),({'closed':False,'isAnswered':False},'Open')]:
            self.github.call.return_value={'data':{'repository':{'discussion':value}}}
            TopicRoots(self.threads,self.github).closed(topic)
            self.assertEqual(self.api.roots[identity]['embeds'][0]['fields'][-1]['value'],'`'+state+'`')
    def test_existing_legacy_starter_with_renamed_thread_is_updated(self):
        identity=self.root();self.api.roots[identity]['embeds'][0]['title']='PR #1 · Old title';self.api.threads[identity]['name']='Custom thread name'
        self.refresh({'state':'closed','merged':True,'title':'Latest title'})
        self.assertEqual(self.api.roots[identity]['embeds'][0]['fields'][-1]['value'],'`Merged`')
        self.assertEqual(self.api.threads[identity]['name'],'Custom thread name')
        self.assertEqual(marker(self.api.roots[identity]),[self.topic.key])
    def deleted(self,topic,event):
        self.github.call.side_effect=ApiError('GitHub',404)
        return TopicRoots(self.threads,self.github).handle_deleted(topic,event,{'action':'deleted','issue':{},'discussion':{}})
    def test_issue_delete_removes_starter_thread_and_prevents_late_event_recreation(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic)
        self.assertTrue(self.deleted(topic,'issues'));self.assertNotIn(identity,self.api.roots);self.assertNotIn(identity,self.api.threads)
        self.assertTrue(TopicRoots(self.threads,self.github).handle_deleted(topic,'issue_comment',{'action':'created','issue':{}}))
        self.assertEqual(self.api.starts,1)
    def test_delete_lost_response_retries_without_touching_another_topic(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic);other=self.root()
        self.api.lose_delete=True
        with self.assertRaises(ApiError):self.deleted(topic,'issues')
        self.assertTrue(self.deleted(topic,'issues'));self.assertIn(other,self.api.roots);self.assertIn(other,self.api.threads)
        self.assertNotIn(identity,self.api.roots)
    def test_discussion_deletion_and_duplicate_event_are_idempotent(self):
        topic=Topic('owner/repo','discussion',4,'Discussion','https://github.com/owner/repo/discussions/4');identity=self.root(topic)
        self.github.call.return_value={'data':{'repository':{'discussion':None}}}
        for _ in range(2):self.assertTrue(TopicRoots(self.threads,self.github).handle_deleted(topic,'discussion',{'action':'deleted'}))
        self.assertNotIn(identity,self.api.roots);self.assertNotIn(identity,self.api.threads)
    def test_discussion_not_found_response_is_deleted_but_permission_error_is_not(self):
        topic=Topic('owner/repo','discussion',4,'Discussion','https://github.com/owner/repo/discussions/4');identity=self.root(topic)
        self.github.call.return_value={'data':{'repository':{'discussion':None}},'errors':[{'type':'FORBIDDEN','path':['repository','discussion']}]}
        with self.assertRaises(RuntimeError):TopicRoots(self.threads,self.github).handle_deleted(topic,'discussion',{'action':'deleted'})
        self.assertIn(identity,self.api.threads)
        self.github.call.return_value={'data':{'repository':{'discussion':None}},'errors':[{'type':'NOT_FOUND','path':['repository','discussion']}]}
        self.assertTrue(TopicRoots(self.threads,self.github).handle_deleted(topic,'discussion',{'action':'deleted'}))
        self.assertNotIn(identity,self.api.threads)

    def test_issue_410_gone_is_a_confirmed_missing_resource(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic)
        self.github.call.side_effect=ApiError('GitHub',410)
        self.assertTrue(TopicRoots(self.threads,self.github).handle_deleted(topic,'issues',{'action':'deleted'}))
        self.assertNotIn(identity,self.api.roots);self.assertNotIn(identity,self.api.threads)
    def test_issue_auth_and_server_errors_do_not_delete_discord_messages(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic)
        for status in (401,403,500):
            self.github.call.side_effect=ApiError('GitHub',status)
            with self.assertRaises(ApiError):TopicRoots(self.threads,self.github).handle_deleted(topic,'issues',{'action':'deleted'})
            self.assertIn(identity,self.api.roots);self.assertIn(identity,self.api.threads)
    def test_pr_410_is_not_treated_as_a_deleted_issue(self):
        self.root();self.github.call.side_effect=ApiError('GitHub',410)
        with self.assertRaises(ApiError):TopicRoots(self.threads,self.github).closed(self.topic)

    def test_comment_delete_never_deletes_its_issue(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic)
        self.assertFalse(self.deleted(topic,'issue_comment'));self.assertIn(identity,self.api.roots)
    def test_stale_deleted_event_uses_current_resource_and_does_not_remove_it(self):
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');identity=self.root(topic)
        self.github.call.return_value={'state':'open','title':'Restored'}
        self.assertTrue(TopicRoots(self.threads,self.github).handle_deleted(topic,'issues',{'action':'deleted'}))
        self.assertIn(identity,self.api.threads);self.assertEqual(self.api.roots[identity]['embeds'][0]['fields'][-1]['value'],'`Open`')
    def test_foreign_starter_is_not_edited_or_deleted(self):
        identity=self.root();self.api.roots[identity]['author']['id']='someone-else'
        with self.assertRaises(ValueError):self.refresh({'state':'closed'})
    def test_deleted_starter_is_not_recreated_by_status_refresh(self):
        identity=self.root();del self.api.roots[identity]
        self.assertTrue(self.refresh({'state':'closed'}));self.assertEqual(self.api.roots,{})
    def test_service_deletion_never_sends_a_deleted_notice_or_recreates_from_old_comment(self):
        from service import Service
        from test_service import config
        service=Service(Path(self.temp.name)/'service',{})
        identity=service.registry.add('tenant',11,'owner/repo',1,'app',config(1000))
        binding=service.registry.get(identity);service.queues[identity]=self.queue
        topic=Topic('owner/repo','issue',3,'Issue','https://github.com/owner/repo/issues/3');root=self.root(topic)
        def call(method,path,*args):
            if path=='/repositories/11':return {'id':11,'full_name':'owner/repo'}
            if path=='/repos/owner/repo/issues/3':raise ApiError('GitHub',410)
            raise AssertionError(path)
        self.github.call.side_effect=call;service.github=lambda _:self.github
        payload={'repository':{'id':11,'full_name':'owner/repo'},'installation':{'id':1},'action':'deleted','issue':{'number':3,'title':'Issue'}}
        self.queue.put('delete','issues',payload)
        feed=Mock();self.assertTrue(service.process(binding,self.threads,feed))
        self.assertNotIn(root,self.api.roots);self.assertNotIn(root,self.api.threads);feed.topic.assert_not_called()
        self.queue.put('old-comment','issue_comment',dict(payload,action='created',comment={'id':7,'body':'Old comment'}))
        self.assertTrue(service.process(binding,self.threads,feed));self.assertEqual(self.api.starts,1)
        self.assertIsNone(self.queue.next())

    def test_initial_draft_and_answered_are_visible(self):
        for kind,obj,state in [('pr',{'draft':True},'Draft'),('discussion',{'isAnswered':True},'Answered')]:
            topic=Topic('owner/repo',kind,1,'Title','https://github.com',obj)
            self.assertEqual(starter_embed(topic)['fields'][-1]['value'],'`'+state+'`')

    def test_answered_is_blue_and_regular_comments_are_gray(self):
        from discord_threads import make_embed
        from root_status import decorate
        topic=Topic('owner/repo','discussion',2,'Discuss','https://github.com/owner/repo/discussions/2')
        self.assertEqual(decorate(topic,{}, {'isAnswered':True})['color'],0x0969DA)
        for event in ('issue_comment','discussion_comment','pull_request_review_comment'):
            embed=make_embed(topic,event,{'action':'created','comment':{'id':1,'body':'Comment'},'sender':{'login':'dev'}})
            self.assertEqual(embed['color'],0x8B949E)
