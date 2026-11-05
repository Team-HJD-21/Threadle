import copy
import re
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from delivery import Queue,CachedThreads
from discord_threads import Topic,make_embed,route
from message_edits import deliver_events
from review_conversations import QUERY,MORE,snapshot
from test_message_edits import EditingDiscord
from topic_state import RED,GREEN


def comment(identity,body='Please clarify this.',review=70):
    return {'id':'C'+str(identity),'databaseId':identity,'body':body,
            'url':f'https://github.com/owner/repo/pull/1#discussion_r{identity}',
            'createdAt':f'2026-11-05T00:{identity%60:02d}:00Z','updatedAt':'2026-11-05T00:59:00Z',
            'author':{'login':'reviewer' if identity==1 else 'developer'},
            'replyTo':None if identity==1 else {'databaseId':1},
            'review':{'databaseId':review,'state':'CHANGES_REQUESTED','body':'Overall review',
                      'submittedAt':'2026-11-05T00:45:00Z','author':{'login':'reviewer'},
                      'url':f'https://github.com/owner/repo/pull/1#pullrequestreview-{review}'}}


def conversation(identity='T1',comments=None,resolved=False):
    return {'id':identity,'path':'src/Example.cs','line':7,'startLine':4,'isResolved':resolved,
            'comments':{'nodes':comments or [comment(1)],'pageInfo':{'hasNextPage':False,'endCursor':None}}}


def response(threads,more=False,cursor=None):
    threads=copy.deepcopy(threads)
    counts={}
    for thread in threads:
        for value in thread['comments']['nodes']:
            identity=(value.get('review') or {}).get('databaseId')
            counts.setdefault(identity,set()).add(value['databaseId'])
    for thread in threads:
        for value in thread['comments']['nodes']:
            review=value.get('review') or {}
            review['comments']={'totalCount':len(counts.get(review.get('databaseId'),[]))}
    return {'data':{'repository':{'pullRequest':{'id':'PR1','reviewThreads':{
        'nodes':threads,'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}}}


class ReviewConversationTests(unittest.TestCase):
    def test_newest_root_webhook_first_still_posts_same_review_in_creation_order(self):
        self.current=[conversation(node,[comment(n,body='Review point '+str(n))])
                      for n,node in [(4,'A'),(2,'Z'),(5,'C'),(1,'Y'),(3,'B')]]
        self.send(5)
        self.assertEqual([p['embeds'][0]['url'].rsplit('r',1)[-1] for p in self.posts()],['1','2','3','4','5'])
        original=[p['id'] for p in self.posts()]
        for n in (3,2,1,4):self.send(n,key='late-'+str(n))
        self.assertEqual([p['id'] for p in self.posts()],original)
        self.assertIn('Changes requested',self.all_posts()[0]['embeds'][0]['title'])

    def test_incomplete_submitted_review_retries_before_any_new_card(self):
        partial=response(self.current)
        partial['data']['repository']['pullRequest']['reviewThreads']['nodes'][0]['comments']['nodes'][0]['review']['comments']['totalCount']=3
        self.github.call.side_effect=None;self.github.call.return_value=partial
        with self.assertRaisesRegex(RuntimeError,'complete'):
            self.send()
        self.assertEqual(self.api.posts,{})
        self.current=[conversation('Z',[comment(1)]),conversation('A',[comment(2)]),conversation('B',[comment(3)])]
        self.github.call.side_effect=lambda *args:response(self.current)
        self.send()
        self.assertEqual(len(self.posts()),3)

    def test_deleted_reply_stays_removed_even_in_stale_snapshots_and_late_creates(self):
        self.send();self.current[0]['comments']['nodes'].append(comment(2,'REMOVE THIS REPLY'))
        self.send(2)
        self.send(2,'deleted')
        self.assertNotIn('REMOVE THIS REPLY',self.posts()[0]['embeds'][0]['description'])
        self.send(2,key='late-deleted-create')
        self.assertNotIn('REMOVE THIS REPLY',self.posts()[0]['embeds'][0]['description'])
        self.current[0]['comments']['nodes'].pop()
        self.send(2,'deleted')
        self.assertNotIn('REMOVE THIS REPLY',self.posts()[0]['embeds'][0]['description'])

    def test_stale_edited_snapshot_retries_and_later_edit_wins_over_older_event(self):
        self.send()
        payload={'action':'edited','comment':{'id':1,'body':'New text','updated_at':'2026-11-05T01:01:00Z'}}
        def deliver():return deliver_events(self.threads,self.topic,'pull_request_review_comment',payload,'edit',lambda:False,'ko',github=self.github)
        with self.assertRaises(RuntimeError):deliver()
        self.current[0]['comments']['nodes'][0].update(body='Even newer text',updatedAt='2026-11-05T01:02:00Z')
        deliver()
        self.assertIn('Even newer text',self.posts()[0]['embeds'][0]['description'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.queue=Queue(Path(self.temp.name)/'q.db')
        self.api=EditingDiscord();self.threads=CachedThreads(self.api,'g',{'pr':'c'},self.queue)
        self.threads.bot={'id':'bot'}
        self.topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1',{'number':1,'title':'Title'})
        self.github=Mock();self.current=[conversation()]
        self.github.call.side_effect=lambda *args:response(self.current)

    def tearDown(self):self.temp.cleanup()

    def send(self,identity=1,action='created',event='pull_request_review_comment',key=None):
        payload={'action':action,'pull_request':self.topic.metadata,
                 'repository':{'id':99,'full_name':self.topic.repo},
                 'comment':{'id':identity,'node_id':'C'+str(identity),'in_reply_to_id':1 if identity!=1 else None},
                 'thread':{'node_id':'T1'}}
        return deliver_events(self.threads,self.topic,event,payload,key or action+str(identity),lambda:False,'ko',github=self.github)

    def all_posts(self):return next(iter(self.api.posts.values()))

    def posts(self):
        return [row for row in self.all_posts() if not re.search(r' · (Approved|Review comment|Changes requested)(?: \(\d+/\d+\))?$',row['embeds'][0]['title'])]

    def test_four_roots_create_four_red_cards_with_parent_review(self):
        self.current=[conversation('T'+str(n),[comment(n)]) for n in range(1,5)]
        for n in range(1,5):self.send(n)
        self.assertEqual(len(self.posts()),4)
        for row in self.posts():
            embed=row['embeds'][0]
            self.assertEqual(embed['color'],RED)
            self.assertIn('Review #70 · reviewer · Changes requested',embed['description'])
            self.assertIn('#pullrequestreview-70',embed['description'])
            self.assertIn('`Unresolved`',embed['description'])
            self.assertIn('`src/Example.cs:4–7`',embed['description'])

    def test_reply_edits_same_card_with_author_and_local_timestamp(self):
        self.send();identity=self.posts()[0]['id']
        self.current[0]['comments']['nodes'].append(comment(2,'I have fixed it.'))
        self.send(2)
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(self.posts()[0]['id'],identity)
        text=self.posts()[0]['embeds'][0]['description']
        self.assertLess(text.index('Please clarify'),text.index('I have fixed'))
        self.assertIn('**developer** · <t:',text)
        self.assertEqual(self.posts()[0]['allowed_mentions'],{'parse':[]})

    def test_resolve_then_old_comment_event_preserves_green_current_state(self):
        self.send();self.current[0]['isResolved']=True
        self.send(action='resolved',event='pull_request_review_thread')
        self.send(key='late-comment')
        self.assertEqual(len(self.posts()),1)
        self.assertEqual(self.posts()[0]['embeds'][0]['color'],GREEN)
        self.assertIn('`Resolved`',self.posts()[0]['embeds'][0]['description'])
        self.current[0]['isResolved']=False
        self.send(action='unresolved',event='pull_request_review_thread')
        self.assertEqual(self.posts()[0]['embeds'][0]['color'],RED)

    def test_reply_edit_and_delete_update_conversation_after_restart(self):
        self.send();self.current[0]['comments']['nodes'].append(comment(2,'First reply'))
        self.send(2)
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        self.current[0]['comments']['nodes'][1]['body']='Edited reply'
        self.send(2,'edited')
        self.assertNotIn('First reply',self.posts()[0]['embeds'][0]['description'])
        self.current[0]['comments']['nodes'].pop()
        self.send(2,'deleted')
        self.assertEqual(len(self.posts()),1)
        self.assertNotIn('Edited reply',self.posts()[0]['embeds'][0]['description'])

    def test_removed_conversation_deletes_owned_card(self):
        self.send();self.current=[]
        self.send(1,'deleted')
        self.assertEqual(self.posts(),[])

    def test_existing_root_card_is_reused_and_old_reply_card_removed(self):
        def old(identity,body):
            return {'action':'created','comment':{'id':identity,'body':body,
                    'html_url':f'https://github.com/owner/repo/pull/1#discussion_r{identity}'}}
        for n in (1,2):
            deliver_events(self.threads,self.topic,'pull_request_review_comment',old(n,'Old'),str(n),lambda:False,'ko')
        root=self.posts()[0]['id']
        self.current[0]['comments']['nodes'].append(comment(2,'Reply'))
        self.send(2,key='upgrade')
        self.assertEqual(len(self.posts()),1);self.assertEqual(self.posts()[0]['id'],root)
        self.assertIn('Reply',self.posts()[0]['embeds'][0]['description'])

    def test_pending_review_never_leaks_to_discord(self):
        self.current[0]['comments']['nodes'][0]['review']['state']='PENDING'
        with self.assertRaises(RuntimeError):self.send()
        self.assertEqual(self.api.posts,{})

    def test_api_error_does_not_replace_card_or_remove_replies(self):
        self.send();original=copy.deepcopy(self.posts())
        self.github.call.side_effect=None;self.github.call.return_value={'errors':[{'message':'Not accessible'}]}
        with self.assertRaises(RuntimeError):self.send(2)
        self.assertEqual(self.posts(),original)

    def test_long_conversation_keeps_all_text_and_reuses_continuation_pages(self):
        self.current[0]['comments']['nodes']=[comment(1,'start '+('long text '*1500))]
        self.send();ids=[r['id'] for r in self.posts()]
        self.current[0]['comments']['nodes'].append(comment(2,'END OF REPLY'))
        self.send(2)
        text=''.join(e.get('description','') for r in self.posts() for e in r['embeds'])
        self.assertEqual(text.count('long text'),1500);self.assertIn('END OF REPLY',text)
        self.assertEqual([r['id'] for r in self.posts()][:len(ids)],ids)
        self.assertTrue(all(e['color']==RED for r in self.posts() for e in r['embeds']))

    def test_file_review_without_line_is_supported(self):
        self.current[0]['line']=None;self.current[0]['startLine']=None
        self.send()
        self.assertIn('`src/Example.cs`',self.posts()[0]['embeds'][0]['description'])

    def test_retry_recovers_lost_post_without_duplicate_card(self):
        self.api.lose_post=True
        with self.assertRaises(Exception):self.send()
        self.send()
        self.assertEqual(len(self.posts()),1)

    def test_resolution_is_not_a_secondary_alert_and_routes_to_pr(self):
        from alert_policy import should_alert
        for action in ('resolved','unresolved'):
            self.assertFalse(should_alert('pull_request_review_thread',{'action':action}))
        topic=route('pull_request_review_thread',{'repository':{'full_name':'owner/repo'},
                    'pull_request':{'number':1,'title':'Title','html_url':self.topic.url}},None)[0]
        self.assertEqual(topic.kind,'pr')

    def test_summary_card_and_line_card_share_review_id(self):
        summary=make_embed(self.topic,'pull_request_review',{'action':'submitted',
                           'review':{'id':70,'body':'Please check.','state':'changes_requested'}})
        self.assertIn('**Review #70**',summary['description'])
        self.send()
        self.assertIn('Review #70',self.posts()[0]['embeds'][0]['description'])

    def test_review_author_does_not_require_a_linked_discord_account_or_sender(self):
        for event,obj in [('pull_request_review',{'id':70,'state':'commented','user':{'login':'unlinked-reviewer'}}),
                          ('issue_comment',{'id':10,'user':{'login':'unlinked-commenter'}}),
                          ('discussion_comment',{'id':11,'author':{'login':'discussion-author'}})]:
            key='review' if event=='pull_request_review' else 'comment'
            embed=make_embed(self.topic,event,{'action':'submitted' if key=='review' else 'created',key:obj})
            self.assertEqual(embed['author']['name'],(obj.get('user') or obj['author'])['login'])

    def test_existing_synthetic_parent_author_is_repaired_without_reposting(self):
        self.send();row=self.all_posts()[0];identity=row['id']
        row['embeds'][0]['author']['name']='GitHub'
        with self.queue.connect() as db:
            db.execute('DELETE FROM review_parent_authors')
            db.execute('UPDATE message_sources SET revision=revision+86400')
        self.send(key='fix-existing-author')
        self.assertEqual(self.all_posts()[0]['id'],identity)
        self.assertEqual(self.all_posts()[0]['embeds'][0]['author']['name'],'reviewer')
        self.assertEqual(len(self.all_posts()),2)

    def test_all_three_parent_review_types_precede_children_when_child_webhook_arrives_first(self):
        states={'COMMENTED':'Review comment','APPROVED':'Approved','CHANGES_REQUESTED':'Changes requested'}
        for index,(state,label) in enumerate(states.items(),1):
            with self.subTest(state=state):
                self.topic=Topic('owner/repo','pr',index,'Title',f'https://github.com/owner/repo/pull/{index}',{'number':index,'title':'Title'})
                self.current=[conversation(comments=[comment(1,review=70+index)])]
                self.current[0]['comments']['nodes'][0]['review'].update(state=state,body='Overall '+label)
                self.send(key='first-child-'+state)
                rows=list(self.api.posts.values())[-1]
                self.assertEqual(len(rows),2)
                self.assertEqual(rows[0]['embeds'][0]['title'],f'PR #{index} · {label}')
                self.assertIn('Overall '+label,rows[0]['embeds'][0]['description'])
                self.assertEqual(rows[0]['embeds'][0]['author']['name'],'reviewer')
                self.assertEqual(rows[1]['embeds'][0]['title'],f'PR #{index} · Review thread')
                before=[row['id'] for row in rows]
                deliver_events(self.threads,self.topic,'pull_request_review',{'action':'submitted','review':{
                    'id':70+index,'state':state.lower(),'body':'Overall '+label,'submitted_at':'2026-11-05T00:45:00Z'}},
                    'late-parent-'+state,lambda:False,'ko',github=self.github)
                self.assertEqual([row['id'] for row in rows],before)

    def test_child_first_bundle_also_posts_parent_first_and_later_comments_do_not_duplicate_it(self):
        for order in (0,1):
            self.topic=Topic('owner/repo','pr',order+10,'Title','https://github.com/owner/repo/pull/10',{'number':order+10,'title':'Title'})
            entries=[{'event':'pull_request_review_comment','payload':{'action':'created','comment':{'id':1}}},
                     {'event':'pull_request_review','payload':{'action':'submitted','review':{'id':70,'state':'approved','body':'LGTM'}}}]
            if order:entries.reverse()
            deliver_events(self.threads,self.topic,'threadle_bundle',{'_events':entries},'ordered-bundle',lambda:False,'ko',github=self.github)
            rows=list(self.api.posts.values())[-1]
            self.assertEqual(len(rows),2)
            self.assertIn('Approved',rows[0]['embeds'][0]['title'])
            self.assertIn('LGTM',rows[0]['embeds'][0]['description'])
            self.assertIn('Review thread',rows[1]['embeds'][0]['title'])

    def test_missing_parent_body_retries_without_publishing_child_or_placeholder(self):
        del self.current[0]['comments']['nodes'][0]['review']['body']
        with self.assertRaises(RuntimeError):self.send()
        self.assertEqual(self.api.posts,{})

    def test_parent_post_failure_retries_same_card_before_child_and_keeps_dnd(self):
        self.threads.silent_notifications=lambda:True
        self.api.lose_post=True
        with self.assertRaises(Exception):self.send()
        self.send()
        self.assertEqual(len(self.all_posts()),2)
        self.assertIn('Changes requested',self.all_posts()[0]['embeds'][0]['title'])
        self.assertIn('Review thread',self.all_posts()[1]['embeds'][0]['title'])
        self.assertTrue(all(row['flags'] & 4096 for row in self.all_posts()))

    def test_comment_dates_use_discord_short_local_date_and_time(self):
        self.current[0]['comments']['nodes'][0]['createdAt']='2026-11-05T09:00:00+09:00'
        self.send()
        import datetime as dt
        stamp=int(dt.datetime(2026,11,5,tzinfo=dt.timezone.utc).timestamp())
        description=self.posts()[0]['embeds'][0]['description']
        self.assertIn('**reviewer** · <t:'+str(stamp)+':s>',description)
        self.assertNotIn(':f>',description)
        self.assertNotIn('Yesterday',description)

    def test_mixed_bundle_keeps_summary_and_folds_replies(self):
        self.current[0]['comments']['nodes'].append(comment(2,'Reply'))
        payload={'_events':[
            {'event':'pull_request_review','payload':{'action':'submitted','review':{'id':70,'state':'changes_requested','body':'Overall review'}}},
            {'event':'pull_request_review_comment','payload':{'action':'created','comment':{'id':1}}},
            {'event':'pull_request_review_comment','payload':{'action':'created','comment':{'id':2}}},
        ]}
        deliver_events(self.threads,self.topic,'threadle_bundle',payload,'bundle',lambda:False,'ko',github=self.github)
        self.assertEqual(len(self.all_posts()),2)
        descriptions=[r['embeds'][0]['description'] for r in self.all_posts()]
        self.assertEqual(sum('Overall review' in t for t in descriptions),1)
        self.assertEqual(sum('Reply' in t for t in descriptions),1)

    def test_initial_card_honors_dnd_and_new_reply_uses_edit(self):
        self.threads.silent_notifications=lambda:True
        self.send()
        self.assertTrue(self.posts()[0]['flags'] & 4096)
        sent=len([c for c in self.api.calls if c[0]=='POST' and c[1].endswith('/messages')])
        self.current[0]['comments']['nodes'].append(comment(2,'Quiet reply'))
        self.send(2)
        self.assertEqual(len([c for c in self.api.calls if c[0]=='POST' and c[1].endswith('/messages')]),sent)

    def test_deleted_first_comment_removes_entire_card_even_with_remaining_replies(self):
        self.send();identity=self.posts()[0]['id']
        self.current[0]['comments']['nodes']=[comment(2,'Remaining reply')]
        self.send(1,'deleted')
        self.assertEqual(self.posts(),[])
        self.assertIn('Changes requested',self.all_posts()[0]['embeds'][0]['title'])

    def test_different_reviews_are_not_grouped_by_reviewer_or_filename(self):
        self.current=[conversation('T1',[comment(1,review=70)]),conversation('T2',[comment(2,review=71)])]
        self.send(1);self.send(2)
        self.assertEqual(len(self.posts()),2)
        self.assertIn('Review #70',self.posts()[0]['embeds'][0]['description'])
        self.assertIn('Review #71',self.posts()[1]['embeds'][0]['description'])

    def test_existing_card_with_future_timestamp_still_accepts_live_resolution(self):
        self.send()
        with self.queue.connect() as db:db.execute('UPDATE message_sources SET revision=revision+86400')
        self.current[0]['isResolved']=True
        self.send(action='resolved',event='pull_request_review_thread')
        self.assertEqual(self.posts()[0]['embeds'][0]['color'],GREEN)


class SnapshotTests(unittest.TestCase):
    def test_both_connections_are_paginated_and_scope_checked(self):
        first=conversation();first['comments']['pageInfo']={'hasNextPage':True,'endCursor':'reply-page'}
        extra={'data':{'repository':{'pullRequest':{'id':'PR1'}},'node':{
            'pullRequest':{'id':'PR1'},'comments':{'nodes':[comment(2)],'pageInfo':{'hasNextPage':False}}}}}
        github=Mock();github.call.side_effect=[response([first],True,'thread-page'),extra,response([conversation('T2',[comment(3)])])]
        topic=Topic('owner/repo','pr',1,'Title','url')
        threads=snapshot(github,topic)
        self.assertEqual(len(threads),2);self.assertEqual(len(threads[0]['comments']),2)
        self.assertEqual(github.call.call_args_list[1].args[2]['query'],MORE)
        self.assertEqual(github.call.call_args_list[2].args[2]['variables']['cursor'],'thread-page')
        extra['data']['node']['pullRequest']['id']='OTHER_PR'
        github.call.side_effect=[response([first]),extra]
        with self.assertRaises(ValueError):snapshot(github,topic)

    def test_non_advancing_cursor_fails_instead_of_truncating(self):
        github=Mock();github.call.return_value=response([],True,'same')
        with self.assertRaises(RuntimeError):snapshot(github,Topic('owner/repo','pr',1,'Title','url'))
