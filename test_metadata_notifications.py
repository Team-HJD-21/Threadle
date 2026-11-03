import json
import copy
import unittest
from unittest.mock import patch
import test_message_edits as fixtures
from delivery import Queue,CachedThreads
from discord_threads import ApiError

class MetadataEditTests(unittest.TestCase):
    setUp=fixtures.EditTests.setUp
    tearDown=fixtures.EditTests.tearDown
    send=fixtures.EditTests.send
    payload=fixtures.EditTests.payload
    def meta(self,action='labeled',second=0,labels=None):
        return {'repository':{'id':11,'full_name':'owner/repo'},'action':action,'pull_request':dict(self.topic.metadata,labels=labels or [],assignees=[],updated_at=f'2026-11-03T00:00:{second:02d}Z'),'sender':{'login':'author'},'label':{'name':'bug'}}
    def meta_send(self,value,event,now):
        with patch('time.time',return_value=now):return self.send(value,event,'pull_request')
    def test_first_sends_immediately_followup_edits_same_label_message(self):
        thread=self.meta_send(self.meta(labels=[{'name':'bug'}]),'first',100)
        self.assertEqual(len(self.api.posts[thread]),1);identity=self.api.posts[thread][0]['id']
        self.meta_send(self.meta(second=1,labels=[{'name':'bug'},{'name':'ui'}]),'second',101)
        self.assertEqual(len(self.api.posts[thread]),1);self.assertEqual(self.api.posts[thread][0]['id'],identity)
        self.assertIn('bug, ui',json.dumps(self.api.posts[thread][0]['embeds'][0],ensure_ascii=False))
    def test_later_metadata_still_edits_the_same_message(self):
        thread=self.meta_send(self.meta(),'one',100)
        self.meta_send(self.meta(second=20),'two',120)
        self.assertEqual(len(self.api.posts[thread]),1)
    def test_body_comments_and_lifecycle_history_are_not_overwritten(self):
        self.send({'action':'opened','pull_request':dict(self.topic.metadata)},'opened','pull_request')
        self.send(self.payload('A',identity=1),'a');self.send(self.payload('B',identity=2),'b')
        thread=self.meta_send(self.meta(),'labels',100)
        assigned=self.meta('assigned',1);assigned['assignee']={'login':'dev'};assigned['pull_request']['assignees']=[{'login':'dev'}]
        self.meta_send(assigned,'assignee',101)
        self.meta_send(self.meta(second=2),'label-edit',102)
        self.send({'action':'closed','pull_request':dict(self.topic.metadata)},'closed','pull_request')
        self.send({'action':'reopened','pull_request':dict(self.topic.metadata)},'reopened','pull_request')
        self.assertEqual(len(self.api.posts[thread]),5)
        self.assertIn('Original PR body',json.dumps(self.api.posts[thread][0]['embeds'][0],ensure_ascii=False))
        self.assertEqual([p['embeds'][0]['description'] for p in self.api.posts[thread][1:3]],['A','B'])
        self.assertIn('Closed',self.api.posts[thread][-2]['embeds'][0]['title']);self.assertIn('Reopened',self.api.posts[thread][-1]['embeds'][0]['title'])
    def test_restart_and_lost_response_recover_original_message(self):
        self.api.lose_post=True
        with self.assertRaises(ApiError):self.meta_send(self.meta(),'one',100)
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        thread=self.meta_send(self.meta(),'one',200)
        self.assertEqual(len(self.api.posts[thread]),1)
    def test_old_revision_does_not_overwrite_latest_metadata(self):
        thread=self.meta_send(self.meta(second=2,labels=[{'name':'new'}]),'new',100)
        self.meta_send(self.meta(second=1,labels=[{'name':'old'}]),'old',101)
        self.assertIn('new',json.dumps(self.api.posts[thread][0]['embeds'][0],ensure_ascii=False))
        self.assertNotIn('old',json.dumps(self.api.posts[thread][0]['embeds'][0],ensure_ascii=False))
    def test_dnd_metadata_replay_maintains_one_latest_state_card(self):
        first=self.meta();second=self.meta(second=20)
        with patch('delivery.time.time',return_value=100):self.queue.put('one','pull_request',first)
        with patch('delivery.time.time',return_value=500):self.queue.put('two','pull_request',second)
        thread=self.meta_send(first,'webhook:one',10000);self.meta_send(second,'webhook:two',10000)
        self.assertEqual(len(self.api.posts[thread]),1)

    def test_labels_assignee_type_and_milestone_share_one_card_without_timeout(self):
        first=self.meta();thread=self.meta_send(first,'labels',100);identity=self.api.posts[thread][0]['id']
        assigned=self.meta('assigned',1);assigned['pull_request']['assignees']=[{'login':'dev'}]
        self.meta_send(assigned,'assignee',1000)
        typed=self.meta('typed',2);typed['pull_request']['type']={'name':'Bug'};typed['pull_request']['assignees']=[{'login':'dev'}]
        self.meta_send(typed,'type',2000)
        milestone=self.meta('milestoned',3);milestone['pull_request']['assignees']=[{'login':'dev'}];milestone['pull_request']['milestone']={'title':'Release'}
        self.meta_send(milestone,'milestone',3000)
        self.assertEqual(len(self.api.posts[thread]),1);self.assertEqual(self.api.posts[thread][0]['id'],identity)
        text=json.dumps(self.api.posts[thread][0]['embeds'][0],ensure_ascii=False)
        for value in ('dev','Bug','Release'):self.assertIn(value,text)
    def test_existing_creation_is_reused_instead_of_legacy_metadata_card(self):
        self.send({'action':'opened','pull_request':dict(self.topic.metadata)},'opened','pull_request')
        thread=next(iter(self.api.posts));original=self.api.posts[thread][0]['id']
        from message_edits import MutableMessages
        old='metadata:pull_request:labels:old'
        MutableMessages(self.threads).update(self.topic,old,self.meta()['pull_request'],'pull_request',self.meta(),'old-event',{'title':'Old labels','description':'Old'},lambda:False)
        self.meta_send(self.meta('assigned',1),'new-assignee',1000)
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertEqual(self.api.posts[thread][0]['id'],original)
        self.assertNotIn('Details updated',self.api.posts[thread][0]['embeds'][0]['title'])

    def test_all_topic_kinds_update_original_fields_and_preserve_long_body(self):
        from discord_threads import Topic
        for kind,event,objkey in [('pr','pull_request','pull_request'),('issue','issues','issue'),('discussion','discussion','discussion')]:
            with self.subTest(kind=kind):
                self.threads.channels[kind]='c'
                original=dict(self.topic.metadata,body='Long original text\n'*500,labels=[{'name':'old'}],assignees=[],category={'name':'Q&A'})
                self.topic=Topic('owner/repo',kind,10+len(self.api.posts),'Title','https://github.com/owner/repo/'+kind,original)
                thread=self.send({'action':'created' if kind=='discussion' else 'opened',objkey:original},'opened-'+kind,event)
                identities=[m['id'] for m in self.api.posts[thread]]
                changed=dict(original,labels=[{'name':'new'}],milestone={'title':'Release'},updated_at='2026-11-03T00:01:00Z')
                self.send({'action':'labeled',objkey:changed},'label-'+kind,event)
                self.assertEqual([m['id'] for m in self.api.posts[thread]],identities)
                text=''.join(e.get('description','') for m in self.api.posts[thread] for e in m['embeds'])
                self.assertEqual(text,original['body'].strip())
                fields=[f for m in self.api.posts[thread] for e in m['embeds'] for f in e.get('fields',[])]
                self.assertIn('new',str(fields));self.assertNotIn('Details updated',str(self.api.posts[thread]))
                del changed['labels'];changed['body']='Edited description';changed['updated_at']='2026-11-03T00:02:00Z'
                self.send({'action':'edited',objkey:changed},'body-'+kind,event)
                self.assertIn('new',str(self.api.posts[thread][0]['embeds']))
