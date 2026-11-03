import copy
import unittest
import test_root_status as roots
import test_deferred_threads as dnd
from discord_threads import Topic,make_embed
from message_edits import deliver_events
from root_status import TopicRoots
from topic_state import GREEN,GRAY,BLUE,PURPLE,RED,ORANGE

class CurrentTopicTests(unittest.TestCase):
    setUp=roots.RootStatusTests.setUp
    tearDown=roots.RootStatusTests.tearDown

    def test_current_state_colors_all_original_description_pages_without_altering_body(self):
        for kind,live,state,color in [('pr',{'state':'open','draft':True},'Draft',GRAY),('pr',{'state':'closed','merged':True},'Merged',PURPLE),('issue',{'state':'closed'},'Closed',PURPLE),('discussion',{'closed':True,'isAnswered':True},'Answered',BLUE),('discussion',{'closed':True,'isAnswered':False},'Closed',PURPLE),('discussion',{'closed':False,'isAnswered':False},'Open',GREEN)]:
            with self.subTest(kind=kind,state=state):
                number=10+len(self.api.roots)
                obj={'title':'Title','body':'Full original description\n'*400,'state':'open','user':{'login':'author'},'updated_at':'2026-11-04T00:00:00Z'}
                topic=Topic('owner/repo',kind,number,'Title','https://github.com/owner/repo/'+str(number),obj)
                event,key={'pr':('pull_request','pull_request'),'issue':('issues','issue'),'discussion':('discussion','discussion')}[kind]
                thread=deliver_events(self.threads,topic,event,{'action':'opened',key:obj},'open-'+str(number),lambda:False,'ko')
                before=copy.deepcopy(self.api.posts[thread]);ids=[m['id'] for m in before]
                self.github.call.return_value={'data':{'repository':{'discussion':dict(title='Title',**live)}}} if kind=='discussion' else dict(title='Title',**live)
                TopicRoots(self.threads,self.github).closed(topic)
                root=self.api.roots[thread]['embeds'][0]
                self.assertEqual(root['title'],kind.upper()+' #'+str(number)+' · Title')
                self.assertEqual(root['fields'][-1]['value'],'`'+state+'`');self.assertEqual(root['color'],color)
                self.assertEqual([m['id'] for m in self.api.posts[thread]],ids)
                self.assertEqual(''.join(e.get('description','') for m in before for e in m['embeds']),''.join(e.get('description','') for m in self.api.posts[thread] for e in m['embeds']))
                for message in self.api.posts[thread]:
                    for embed in message['embeds']:self.assertEqual(embed['color'],color)

    def test_thread_titles_transition_and_reuse_existing_thread_after_cache_loss(self):
        from discord_threads import Threads
        from topic_state import thread_title
        for kind,obj in [('pr',{'merged':True}),('issue',{'state':'closed'}),('discussion',{'closed':True,'isAnswered':True})]:
            with self.subTest(kind=kind):
                topic=Topic('owner/repo',kind,20+len(self.api.roots),'Title','https://github.com/owner/repo/1',{})
                thread=self.threads.ensure(topic)
                self.github.call.return_value={'data':{'repository':{'discussion':dict(title='Title',**obj)}}} if kind=='discussion' else dict(title='Title',**obj)
                TopicRoots(self.threads,self.github).closed(topic)
                desired=thread_title(topic,dict(title='Title',**obj))
                self.assertEqual(self.api.threads[thread['id']]['name'],desired)
                finder=Threads(self.api,'g',self.threads.channels);finder.bot={'id':'bot'}
                self.assertEqual(finder.ensure(topic)['id'],thread['id'])
        self.assertEqual(self.api.starts,3)

    def test_status_stays_visible_when_title_is_long_and_utf16_limited(self):
        from topic_state import thread_title
        topic=Topic('owner/repo','pr',123,'😀'*200,'',{'draft':True})
        title=thread_title(topic)
        self.assertTrue(title.startswith('[PR #123 · Draft]'))
        self.assertLessEqual(len(title.encode('utf-16-le'))//2,100)

    def test_lifecycle_updates_reuse_cards_and_title_without_new_thread_messages(self):
        for kind,event,key,changes in [
            ('pr','pull_request','pull_request',[('converted_to_draft',{'draft':True,'state':'open'},'Draft'),('ready_for_review',{'draft':False,'state':'open'},'Open'),('closed',{'merged':True,'state':'closed'},'Merged')]),
            ('issue','issues','issue',[('closed',{'state':'closed'},'Closed'),('reopened',{'state':'open'},'Open')]),
            ('discussion','discussion','discussion',[('answered',{'closed':True,'isAnswered':True},'Answered'),('unanswered',{'closed':True,'isAnswered':False},'Closed'),('reopened',{'closed':False,'isAnswered':False},'Open')])]:
            with self.subTest(kind=kind):
                topic=Topic('owner/repo',kind,30+len(self.api.roots),'Title','https://github.com/owner/repo/1',{'body':'Original body','title':'Title'})
                thread=deliver_events(self.threads,topic,event,{'action':'opened',key:topic.metadata},'opened-'+kind,lambda:False,'ko')
                original=[m['id'] for m in self.api.posts[thread]]
                for action,obj,label in changes:
                    live=dict(title='Title',**obj)
                    self.github.call.return_value={'data':{'repository':{'discussion':live}}} if kind=='discussion' else live
                    roots_now=TopicRoots(self.threads,self.github)
                    deliver_events(self.threads,topic,event,{'action':action,key:dict(topic.metadata,**obj)},action+'-'+kind,lambda:roots_now.closed(topic),'ko')
                    self.assertEqual([m['id'] for m in self.api.posts[thread]],original)
                    self.assertIn(' · '+label+']',self.api.threads[thread]['name'])
                    self.assertEqual(self.api.roots[thread]['embeds'][0]['fields'][-1]['value'],'`'+label+'`')

    def test_unchanged_state_does_not_reread_description_messages(self):
        obj={'title':'Title','body':'Description','state':'open'}
        topic=Topic('owner/repo','issue',1,'Title','https://github.com/owner/repo/issues/1',obj)
        thread=deliver_events(self.threads,topic,'issues',{'action':'opened','issue':obj},'opened',lambda:False,'ko')
        self.github.call.return_value=dict(obj,state='closed')
        TopicRoots(self.threads,self.github).closed(topic)
        before=len([c for c in self.api.calls if c[0]=='GET' and c[1].startswith('/channels/'+thread+'/messages/')])
        TopicRoots(self.threads,self.github).closed(topic)
        after=len([c for c in self.api.calls if c[0]=='GET' and c[1].startswith('/channels/'+thread+'/messages/')])
        self.assertEqual(before,after)

    def test_historical_event_colors_are_independent_of_current_card_state(self):
        cases=[('pr','pull_request_review',{'review':{'state':'approved'}},GREEN),('pr','pull_request_review',{'review':{'state':'changes_requested'}},ORANGE),('pr','pull_request_review',{'review':{'state':'commented'}},GRAY),('issue','issues',{'action':'closed'},RED),('issue','issues',{'action':'reopened'},GREEN),('discussion','discussion',{'action':'answered'},BLUE),('discussion','discussion',{'action':'unanswered'},RED),('discussion','discussion',{'action':'closed'},PURPLE)]
        for kind,event,payload,color in cases:
            topic=Topic('owner/repo',kind,1,'Title','https://github.com/owner/repo/1')
            objkey={'pr':'pull_request','issue':'issue','discussion':'discussion'}[kind]
            payload=dict(payload,**{objkey:{'title':'Title','body':'Body','html_url':topic.url}})
            self.assertEqual(make_embed(topic,event,payload)['color'],color)

class IgnoredDiscussionEvents(unittest.TestCase):
    setUp=dnd.DeferredThreadTests.setUp
    tearDown=dnd.DeferredThreadTests.tearDown
    value=dnd.DeferredThreadTests.value
    process=dnd.DeferredThreadTests.process
    def test_pin_and_unpin_complete_without_discord_messages(self):
        for action in ('pinned','unpinned'):
            payload=self.value(action);payload['discussion']=payload.pop('pull_request')
            self.q.put(action,'discussion',payload);self.process()
        self.assertFalse(self.api.roots);self.assertFalse(self.api.posts)
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT sum(done) FROM jobs').fetchone()[0],2)
