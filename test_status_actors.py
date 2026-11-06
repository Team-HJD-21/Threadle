import copy
import unittest
from discord_threads import Topic
from message_edits import deliver_events
from root_status import TopicRoots
from topic_state import status_field
import test_root_status as roots_fixture


class StatusActorTests(unittest.TestCase):
    setUp=roots_fixture.RootStatusTests.setUp
    tearDown=roots_fixture.RootStatusTests.tearDown

    def send(self,kind,action,actor,at,**state):
        key={'pr':'pull_request','issue':'issue','discussion':'discussion'}[kind]
        event={'pr':'pull_request','issue':'issues','discussion':'discussion'}[kind]
        obj=dict(number=1,title='Title',body='Original description',user={'login':'original-author'},
                 updated_at=f'2026-11-06T10:{at:02d}:00Z',**state)
        payload={'action':action,key:obj,'sender':{'login':actor}}
        topic=Topic('owner/repo',kind,1,'Title','https://github.com/owner/repo/1',obj)
        roots=TopicRoots(self.threads,self.github)
        topic=roots.observe(topic,event,payload)
        self.github.call.return_value={'data':{'repository':{'discussion':obj}}} if kind=='discussion' else obj
        thread=deliver_events(self.threads,topic,event,payload,str(at),lambda:roots.closed(topic),'en')
        root=next(iter(self.api.roots.values()))['embeds'][0]
        return topic,roots,thread,root

    def test_pr_lifecycle_records_changer_without_replacing_original_author(self):
        for action,actor,at,state,label in [
            ('opened','opener',1,{'state':'open'},'Open'),
            ('converted_to_draft','draft-editor',2,{'state':'open','draft':True},'Draft'),
            ('ready_for_review','ready-editor',3,{'state':'open','draft':False},'Open'),
            ('closed','delivery-bot',4,{'state':'closed','merged':True,'merged_by':{'login':'actual-merger'}},'Merged')]:
            topic,roots,thread,root=self.send('pr',action,actor,at,**state)
            expected='actual-merger' if label=='Merged' else actor
            self.assertEqual(root['fields'][-1]['value'],f'`{label}` by {expected}')
            self.assertEqual(root['author']['name'],'original-author')
            for message in self.api.posts[thread]:
                for embed in message['embeds']:
                    for field in embed.get('fields',[]):
                        if field['name']=='Current Status':self.assertEqual(field['value'],f'`{label}` by {expected}')
        self.assertEqual(self.api.starts,1)

    def test_comment_and_metadata_edit_do_not_become_state_actor_after_restart(self):
        topic,_,thread,_=self.send('issue','opened','opener',1,state='open')
        for event in ('issue_comment','issues'):
            roots=TopicRoots(self.threads,self.github)
            obj=dict(topic.metadata,updated_at='2026-11-06T11:00:00Z')
            value=roots.observe(Topic(topic.repo,topic.kind,topic.number,topic.title,topic.url,obj),event,
                {'action':'labeled' if event=='issues' else 'created','sender':{'login':'other-editor'},'issue':obj})
            self.assertEqual(status_field('issue',value.metadata)['value'],'`Open` by opener')

    def test_same_open_state_refreshes_actor_on_reopen_and_ignores_old_replay(self):
        self.send('issue','opened','opener',1,state='open')
        self.send('issue','closed','closer',2,state='closed')
        _,_,thread,root=self.send('issue','reopened','reopener',3,state='open')
        self.assertEqual(root['fields'][-1]['value'],'`Open` by reopener')
        _,_,_,root=self.send('issue','opened','opener',1,state='open')
        self.assertEqual(root['fields'][-1]['value'],'`Open` by reopener')

    def test_answered_discussion_keeps_chooser_when_closed_then_restores_closer(self):
        self.send('discussion','created','opener',1,closed=False,isAnswered=False)
        _,_,_,root=self.send('discussion','answered','chooser',2,closed=False,isAnswered=True)
        self.assertEqual(root['fields'][-1]['value'],'`Answered` by chooser')
        _,_,_,root=self.send('discussion','closed','closer',3,closed=True,isAnswered=True)
        self.assertEqual(root['fields'][-1]['value'],'`Answered` by chooser')
        _,_,_,root=self.send('discussion','unanswered','unmarker',4,closed=True,isAnswered=False)
        self.assertEqual(root['fields'][-1]['value'],'`Closed` by closer')

    def test_authoritative_fields_backfill_existing_cards_without_guessing(self):
        self.assertEqual(status_field('pr',{'merged':True,'merged_by':{'login':'merger'}})['value'],'`Merged` by merger')
        self.assertEqual(status_field('issue',{'state':'closed','closed_by':{'login':'closer'}})['value'],'`Closed` by closer')
        self.assertEqual(status_field('discussion',{'isAnswered':True,'answerChosenBy':{'login':'chooser'}})['value'],'`Answered` by chooser')
        self.assertEqual(status_field('issue',{'state':'closed','user':{'login':'author'}})['value'],'`Closed`')

    def test_deletion_purges_actor_cache_only_for_deleted_topic(self):
        topic,roots,_,_=self.send('issue','opened','opener',1,state='open')
        with self.queue.connect() as db:
            db.execute('INSERT INTO topic_status_actors VALUES(?,?,?,?)',('other','Open','keep',1))
        roots.purge(topic)
        with self.queue.connect() as db:self.assertEqual(db.execute('SELECT topic,actor FROM topic_status_actors').fetchall(),[('other','keep')])
