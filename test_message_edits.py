import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from delivery import Queue,CachedThreads
from discord_threads import Api,ApiError,Topic,make_embed
from message_edits import deliver_events,MutableMessages,before_source
from test_discord_threads import FakeDiscord


class EditingDiscord(FakeDiscord):
    def __init__(self):
        super().__init__();self.lose_post=False;self.lose_patch=False;self.fail_patch=False
    def call(self,method,path,data=None):
        if '/messages/' in path and path.startswith('/channels/') and path.split('/')[2]!='c':
            channel,message=path.split('/')[2],path.rsplit('/',1)[1]
            row=next((row for row in self.posts.get(channel,[]) if row['id']==message),None)
            self.calls.append((method,path,copy.deepcopy(data)))
            if row is None:raise ApiError('Discord',404,10008)
            if method=='GET':return copy.deepcopy(row)
            if method=='PATCH':
                if self.fail_patch:self.fail_patch=False;raise ApiError('Discord','network')
                row.update(copy.deepcopy(data))
                if self.lose_patch:self.lose_patch=False;raise ApiError('Discord','network')
                return copy.deepcopy(row)
            if method=='DELETE':self.posts[channel].remove(row);return None
        result=super().call(method,path,data)
        if method=='POST' and path.endswith('/messages') and path.split('/')[2]!='c' and self.lose_post:
            self.lose_post=False;raise ApiError('Discord','network')
        return result


class EditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.queue=Queue(Path(self.temp.name)/'queue.sqlite3')
        self.api=EditingDiscord();self.threads=CachedThreads(self.api,'g',{'pr':'c'},self.queue);self.threads.bot={'id':'bot'}
        self.topic=Topic('owner/repo','pr',1,'PR title','https://github.com/owner/repo/pull/1',{'number':1,'title':'PR title','body':'Original PR body','created_at':'2026-11-03T00:00:00Z','updated_at':'2026-11-03T00:00:00Z'})
    def tearDown(self):self.temp.cleanup()
    def payload(self,body='First',action='created',identity=7,time='2026-11-03T00:00:00Z'):
        return {'action':action,'comment':{'id':identity,'body':body,'html_url':f'https://github.com/owner/repo/pull/1#issuecomment-{identity}','updated_at':time},'sender':{'login':'author'}}
    def send(self,payload,event_key,event='issue_comment'):
        return deliver_events(self.threads,self.topic,event,payload,event_key,lambda:False,'ko')

    def edit_body(self,body,event='body-edit',time='2026-11-03T00:01:00Z'):
        return self.send({'action':'edited','pull_request':dict(self.topic.metadata,body=body,updated_at=time)},event,'pull_request')

    def test_edit_removes_only_old_bot_ping_line_and_preserves_other_text(self):
        thread=self.send(self.payload(),'one')
        row=self.api.posts[thread][0]
        row['content']='<@55> <@!66>'
        self.send(self.payload('Updated','edited',time='2026-11-03T00:01:00Z'),'two')
        self.assertEqual(row['content'],'')
        row['content']='Keep this bot guidance'
        self.send(self.payload('Again','edited',time='2026-11-03T00:02:00Z'),'three')
        self.assertEqual(row['content'],'Keep this bot guidance')

    def test_source_recovery_stops_before_comments_source_creation(self):
        import datetime as dt
        def snowflake(stamp):
            value=dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()
            return str(int((value-1420070400)*1000)<<22)
        old=snowflake('2026-11-01T00:00:00Z')
        inspected=[]
        def history(thread):
            inspected.append('first-page')
            yield {'id':old,'author':{'id':'someone'},'embeds':[]}
            inspected.append('second-page')
            raise AssertionError('Older history must not be read for a new source')
        self.threads.messages=history
        self.assertEqual(MutableMessages(self.threads).recover(self.topic,'source','thread','comment:issue_comment:7',
                         {'created_at':'2026-11-03T00:00:00Z'}),0)
        self.assertEqual(inspected,['first-page'])
        # A small clock skew, missing timestamps and synthetic IDs stay conservative.
        self.assertFalse(before_source({'id':snowflake('2026-11-02T23:57:00Z')},{'created_at':'2026-11-03T00:00:00Z'}))
        self.assertFalse(before_source({'id':old},{}))
        self.assertFalse(before_source({'id':'101'},{'created_at':'2026-11-03T00:00:00Z'}))
        self.assertFalse(before_source({'id':old},{'created_at':'2026-11-03T00:00:00'}))

    def test_growing_description_is_replaced_as_one_group_after_comments(self):
        thread=self.send({'action':'opened','pull_request':self.topic.metadata},'opened','pull_request')
        old=self.api.posts[thread][0]['id']
        self.send(self.payload('KEEP THIS COMMENT'),'comment')
        comment=copy.deepcopy(self.api.posts[thread][-1])
        body=('Description paragraph.\n'*550)+'FINAL_DESCRIPTION'
        self.edit_body(body)
        rows=self.api.posts[thread]
        self.assertNotIn(old,[row['id'] for row in rows])
        self.assertEqual(rows[0],comment)
        self.assertGreater(len(rows[1:]),1)
        self.assertEqual(''.join(e.get('description','') for row in rows[1:] for e in row['embeds']),body)
        self.assertTrue(all(row['flags']&4096 for row in rows[1:]))
        # Replay and restart keep the new IDs, and a regular content edit still PATCHes.
        identities=[row['id'] for row in rows]
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        self.edit_body(body)
        self.edit_body(body.replace('paragraph','PARAGRAPH'),'next-edit','2026-11-03T00:02:00Z')
        self.assertEqual([row['id'] for row in self.api.posts[thread]],identities)

    def test_body_replacement_keeps_old_group_until_lost_post_is_recovered(self):
        thread=self.edit_body('Original body','original','2026-11-03T00:00:00Z')
        old=self.api.posts[thread][0]['id']
        self.send(self.payload('Unrelated comment'),'comment')
        self.api.lose_post=True
        body='Before\n![image](https://example.com/a.png)\nAfter'
        with self.assertRaises(ApiError):self.edit_body(body)
        self.assertIn(old,[r['id'] for r in self.api.posts[thread]])
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        self.edit_body(body)
        rows=self.api.posts[thread]
        self.assertEqual(len(rows),3)
        self.assertEqual(rows[0]['embeds'][0]['description'],'Unrelated comment')
        self.assertEqual(rows[1]['embeds'][0]['description'],'Before')
        self.assertEqual(rows[2]['embeds'][0]['description'],'After')
        self.assertNotIn(old,[r['id'] for r in rows])

    def test_replacement_cleanup_retries_and_never_deletes_before_all_pages_exist(self):
        thread=self.edit_body('Original','original','2026-11-03T00:00:00Z')
        original=self.api.call
        failed=[False]
        def fail_delete(method,path,data=None):
            if method=='DELETE' and not failed[0]:
                failed[0]=True;raise ApiError('Discord',403,50013)
            return original(method,path,data)
        self.api.call=fail_delete
        body='![A](https://example.com/a.png)\n![B](https://example.com/b.png)'
        with self.assertRaises(ApiError):self.edit_body(body)
        self.assertEqual(len(self.api.posts[thread]),4)  # old + two images + final Details
        new=[r['id'] for r in self.api.posts[thread][1:]]
        self.edit_body(body)
        self.assertEqual([r['id'] for r in self.api.posts[thread]],new)

    def test_newer_edit_supersedes_an_incomplete_replacement(self):
        thread=self.edit_body('Original','original','2026-11-03T00:00:00Z')
        self.api.lose_post=True
        with self.assertRaises(ApiError):self.edit_body('![A](https://example.com/a.png)\nAfter')
        self.edit_body('Final body','newer','2026-11-03T00:02:00Z')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.assertEqual(self.api.posts[thread][0]['embeds'][0]['description'],'Final body')

    def test_issue_and_discussion_bodies_relocate_without_touching_comments(self):
        self.threads.channels.update(issue='c',discussion='c')
        for kind,event,key in [('issue','issues','issue'),('discussion','discussion','discussion')]:
            with self.subTest(kind=kind):
                self.topic=Topic('owner/repo',kind,1,'Title','https://github.com/owner/repo/'+kind+'/1',dict(self.topic.metadata))
                thread=self.send({'action':'opened' if kind=='issue' else 'created',key:self.topic.metadata},kind+'-opened',event)
                self.send(self.payload('Preserved '+kind),kind+'-comment')
                comment=copy.deepcopy(self.api.posts[thread][-1])
                obj=dict(self.topic.metadata,body='Before\n![A](https://example.com/a.png)\nAfter',updated_at='2026-11-03T00:01:00Z')
                self.send({'action':'edited',key:obj},kind+'-edited',event)
                rows=self.api.posts[thread]
                self.assertEqual(rows[0],comment)
                self.assertEqual(len(rows),3)
                self.assertEqual(rows[1]['embeds'][0]['description'],'Before')
                self.assertEqual(rows[2]['embeds'][0]['description'],'After')

    def test_image_layout_changes_replace_even_when_page_count_is_unchanged(self):
        thread=self.edit_body('Before\n![A](https://example.com/a.png)\nAfter','original','2026-11-03T00:00:00Z')
        originals=[r['id'] for r in self.api.posts[thread]]
        self.send(self.payload('Keep this comment'),'comment')
        comment=copy.deepcopy(self.api.posts[thread][-1])
        self.edit_body('Before\n![B](https://example.com/b.png)\nAfter')
        rows=self.api.posts[thread]
        self.assertEqual(rows[0],comment)
        self.assertFalse(set(originals)&{r['id'] for r in rows})
        self.assertEqual(rows[1]['embeds'][0]['image']['url'],'https://example.com/b.png')

    def test_second_page_failure_preserves_old_body_and_retries_only_missing_pages(self):
        thread=self.edit_body('Original','original','2026-11-03T00:00:00Z')
        old=self.api.posts[thread][0]['id'];original=self.api.call;posts=[0]
        def fail_second_post(method,path,data=None):
            if method=='POST' and path.endswith('/messages'):
                posts[0]+=1
                if posts[0]==2:raise ApiError('Discord',503)
            return original(method,path,data)
        self.api.call=fail_second_post
        body='Before\n![A](https://example.com/a.png)\nAfter'
        with self.assertRaises(ApiError):self.edit_body(body)
        self.assertEqual([r['id'] for r in self.api.posts[thread]][0],old)
        first_new=self.api.posts[thread][1]['id']
        self.edit_body(body)
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertEqual(self.api.posts[thread][0]['id'],first_new)

    def test_description_change_notice_links_to_the_new_group(self):
        thread=self.edit_body('Original','original','2026-11-03T00:00:00Z')
        self.send(self.payload('Comment'),'comment')
        obj=dict(self.topic.metadata,body='Before\n![A](https://example.com/a.png)\nAfter',updated_at='2026-11-03T00:01:00Z')
        self.send({'action':'edited','pull_request':obj,'changes':{'body':{'from':'Original'}}},'change','pull_request')
        rows=self.api.posts[thread]
        self.assertEqual(rows[0]['embeds'][0]['description'],'Comment')
        self.assertEqual(rows[-1]['embeds'][0]['url'],f"https://discord.com/channels/g/{thread}/{rows[1]['id']}")
        self.assertIn('Description updated',rows[-1]['embeds'][0]['title'])
    def test_comment_updates_same_message_after_restart(self):
        thread=self.send(self.payload(),'one');message=self.api.posts[thread][0]['id']
        self.threads=CachedThreads(self.api,'g',{'pr':'c'},Queue(self.queue.path));self.threads.bot={'id':'bot'}
        self.send(self.payload('Changed','edited',time='2026-11-03T00:01:00Z'),'two')
        self.assertEqual(len(self.api.posts[thread]),1);self.assertEqual(self.api.posts[thread][0]['id'],message)
        self.assertIn('Changed',self.api.posts[thread][0]['embeds'][0]['description'])
    def test_comments_in_same_bundle_are_independently_editable(self):
        payload={'_events':[{'event':'issue_comment','payload':self.payload('A',identity=1)},{'event':'issue_comment','payload':self.payload('B',identity=2)}]}
        thread=self.send(payload,'group','threadle_bundle')
        self.send(self.payload('A changed','edited',1,'2026-11-03T00:01:00Z'),'edit')
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertEqual([r['embeds'][0]['description'] for r in self.api.posts[thread]],['A changed','B'])
    def test_image_pages_shrink_without_removing_other_comments(self):
        thread=self.send(self.payload('![a](https://example.com/a.png) ![b](https://example.com/b.png)'),'images')
        self.send(self.payload('Other',identity=8),'other')
        first=self.api.posts[thread][0]['id']
        self.send(self.payload('![a](https://example.com/a.png)','edited',time='2026-11-03T00:01:00Z'),'shrink')
        self.assertEqual(len(self.api.posts[thread]),2);self.assertEqual(self.api.posts[thread][0]['id'],first)
        self.assertEqual(self.api.posts[thread][1]['embeds'][0]['description'],'Other')
    def test_unchanged_private_attachment_is_retained_and_removed_image_is_cleared(self):
        body='![a](https://github.com/user-attachments/assets/test)'
        thread=self.send(self.payload(body),'one');row=self.api.posts[thread][0]
        row['attachments']=[{'id':'900','filename':'image.png'}];row['embeds'][0]['image']={'url':'https://cdn.discordapp.com/attachments/test.png','width':500}
        self.send(self.payload('Changed '+body,'edited',time='2026-11-03T00:01:00Z'),'two')
        self.assertEqual(row['attachments'],[{'id':'900'}]);self.assertEqual(row['embeds'][0]['image'],{'url':'https://cdn.discordapp.com/attachments/test.png'})
        self.send(self.payload('No image','edited',time='2026-11-03T00:02:00Z'),'three')
        self.assertEqual(row['attachments'],[]);self.assertNotIn('image',row['embeds'][0])
    def test_lost_post_and_patch_responses_are_recovered_without_duplicates(self):
        self.api.lose_post=True
        with self.assertRaises(ApiError):self.send(self.payload(),'one')
        thread=self.send(self.payload(),'one');self.api.lose_patch=True
        updated=self.payload('Changed','edited',time='2026-11-03T00:01:00Z')
        with self.assertRaises(ApiError):self.send(updated,'two')
        self.send(updated,'two')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.assertEqual(len([c for c in self.api.calls if c[0]=='PATCH' and '/messages/' in c[1]]),1)
    def test_old_retries_do_not_overwrite_newer_edits(self):
        thread=self.send(self.payload(),'one');self.api.fail_patch=True
        old=self.payload('Older','edited',time='2026-11-03T00:01:00Z')
        with self.assertRaises(ApiError):self.send(old,'two')
        self.send(self.payload('Newest','edited',time='2026-11-03T00:02:00Z'),'three')
        self.send(old,'two')
        self.assertEqual(self.api.posts[thread][0]['embeds'][0]['description'],'Newest')
    def test_missing_discord_message_gets_one_replacement(self):
        thread=self.send(self.payload(),'one');self.api.posts[thread].clear()
        self.send(self.payload('Changed','edited',time='2026-11-03T00:01:00Z'),'two')
        self.assertEqual(len(self.api.posts[thread]),1)
    def test_legacy_single_comment_is_adopted_but_mixed_legacy_message_is_preserved(self):
        payload=self.payload()
        thread=self.threads.deliver(self.topic,make_embed(self.topic,'issue_comment',payload),'legacy',lambda:False)
        self.send(self.payload('Changed','edited',time='2026-11-03T00:01:00Z'),'edit')
        self.assertEqual(len(self.api.posts[thread]),1)
        mixed={'_embeds':[make_embed(self.topic,'issue_comment',self.payload('X',identity=9)),make_embed(self.topic,'issue_comment',self.payload('Y',identity=10))]}
        self.threads.deliver(self.topic,mixed,'mixed',lambda:False)
        self.send(self.payload('X changed','edited',9,'2026-11-03T00:01:00Z'),'edit-x')
        self.assertEqual(len(self.api.posts[thread]),3)
        self.assertEqual(len(self.api.posts[thread][1]['embeds']),2)
    def test_body_edits_after_closed_do_not_create_a_status_message(self):
        obj=dict(self.topic.metadata)
        self.send({'action':'opened','pull_request':obj},'opened','pull_request')
        thread=self.send({'action':'closed','pull_request':obj},'closed','pull_request')
        edited=dict(obj,body='Changed PR body',updated_at='2026-11-03T00:01:00Z')
        self.send({'action':'edited','pull_request':edited},'edited','pull_request')
        self.assertEqual(len(self.api.posts[thread]),1)
        self.assertIn('Changed PR body',self.api.posts[thread][0]['embeds'][0]['description'])
        self.assertNotIn('Closed',self.api.posts[thread][0]['embeds'][0]['title'])

    def test_body_bundle_out_of_order_keeps_latest_description(self):
        older=dict(self.topic.metadata,body='Older',updated_at='2026-11-03T00:01:00Z')
        newer=dict(self.topic.metadata,body='Newest',updated_at='2026-11-03T00:02:00Z')
        payload={'_events':[{'event':'pull_request','payload':{'action':'edited','pull_request':newer}}, {'event':'pull_request','payload':{'action':'edited','pull_request':older}}]}
        thread=self.send(payload,'bundle','threadle_bundle')
        self.assertIn('Newest',self.api.posts[thread][0]['embeds'][0]['description'])
        self.assertNotIn('Older',self.api.posts[thread][0]['embeds'][0]['description'])

    def test_pr_body_edit_updates_original_and_adds_only_a_short_notice(self):
        self.send({'action':'opened','pull_request':self.topic.metadata},'opened','pull_request')
        original=self.api.posts[next(iter(self.api.posts))][0]['id']
        changed=dict(self.topic.metadata,body='CHANGED BODY',updated_at='2026-11-03T00:01:00Z')
        payload={'action':'edited','pull_request':changed,'changes':{'body':{'from':'Original PR body'}},'sender':{'login':'editor'}}
        thread=self.send(payload,'edit-body','pull_request')
        messages=self.api.posts[thread]
        self.assertEqual(len(messages),2);self.assertEqual(messages[0]['id'],original)
        self.assertIn('CHANGED BODY',messages[0]['embeds'][0]['description'])
        notice=messages[1]['embeds'][0]
        self.assertNotIn('CHANGED BODY',notice['description'])
        self.assertIn('Description updated',notice['title'])
        self.assertEqual(notice['author']['name'],'editor')
        self.assertEqual(notice['url'],f'https://discord.com/channels/g/{thread}/{original}')
        self.send(payload,'edit-body','pull_request')
        self.assertEqual(len(self.api.posts[thread]),2)

    def test_title_only_noop_and_old_edits_do_not_add_body_notices(self):
        self.send({'action':'opened','pull_request':self.topic.metadata},'opened','pull_request')
        for changes,body in (({'title':{'from':'Old'}},'Original PR body'),({'body':{'from':'same'}},'same')):
            obj=dict(self.topic.metadata,body=body,updated_at='2026-11-03T00:02:00Z')
            self.send({'action':'edited','pull_request':obj,'changes':changes},str(changes),'pull_request')
        old=dict(self.topic.metadata,body='Old edit',updated_at='2026-11-03T00:01:00Z')
        thread=self.send({'action':'edited','pull_request':old,'changes':{'body':{'from':'older'}}},'stale','pull_request')
        self.assertEqual(len(self.api.posts[thread]),1)

    def test_pr_body_notice_retry_is_durable_and_silent_during_dnd(self):
        self.send({'action':'opened','pull_request':self.topic.metadata},'opened','pull_request')
        self.threads.silent_notifications=lambda:True
        body=dict(self.topic.metadata,body='Edited body',updated_at='2026-11-03T00:01:00Z')
        title=dict(body,title='Later title',updated_at='2026-11-03T00:02:00Z')
        payload={'_events':[
            {'event':'pull_request','payload':{'action':'edited','pull_request':body,'changes':{'body':{'from':'Original PR body'}}}},
            {'event':'pull_request','payload':{'action':'edited','pull_request':title,'changes':{'title':{'from':'Old title'}}}},
        ]}
        self.api.lose_post=True
        with self.assertRaises(ApiError):self.send(payload,'bundle-body','threadle_bundle')
        thread=self.send(payload,'bundle-body','threadle_bundle')
        messages=self.api.posts[thread]
        self.assertEqual(len(messages),2)
        self.assertTrue(messages[-1]['flags']&4096)

    def test_new_pr_with_bundled_description_edit_does_not_get_redundant_notice(self):
        body=dict(self.topic.metadata,body='Final body',updated_at='2026-11-03T00:01:00Z')
        payload={'_events':[
            {'event':'pull_request','payload':{'action':'opened','pull_request':self.topic.metadata}},
            {'event':'pull_request','payload':{'action':'edited','pull_request':body,'changes':{'body':{'from':'Original PR body'}}}},
        ]}
        thread=self.send(payload,'creation-bundle','threadle_bundle')
        self.assertEqual(len(self.api.posts[thread]),1)

    def test_stale_event_does_not_unarchive_a_closed_thread(self):
        thread=self.send(self.payload(),'one')
        self.send(self.payload('Newest','edited',time='2026-11-03T00:02:00Z'),'new')
        self.api.threads[thread]['thread_metadata']['archived']=True
        self.send(self.payload('Old','edited',time='2026-11-03T00:01:00Z'),'old')
        self.assertTrue(self.api.threads[thread]['thread_metadata']['archived'])
    def test_comment_types_with_same_numeric_id_do_not_collide(self):
        thread=self.send(self.payload('Conversation'),'one')
        payload=self.payload('Code comment');payload['comment']['html_url']='https://github.com/owner/repo/pull/1#discussion_r7'
        self.send(payload,'code','pull_request_review_comment')
        self.assertEqual(len(self.api.posts[thread]),2)
    def test_missing_comment_url_does_not_adopt_item_description(self):
        self.send({'action':'opened','pull_request':dict(self.topic.metadata)},'opened','pull_request')
        payload=self.payload('Comment');payload['comment'].pop('html_url')
        thread=self.send(payload,'comment')
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertIn('Original PR body',self.api.posts[thread][0]['embeds'][0]['description'])



class AttachmentTransportTests(unittest.TestCase):
    def test_patch_multipart_replaces_old_attachment_list(self):
        api=Api('Discord','https://discord.com/api/v10','test-only','Bot')
        response=MagicMock();response.__enter__.return_value=response;response.headers={};response.read.return_value=b'{"id":"456"}'
        with patch('discord_threads.urlopen',return_value=response) as request:
            api.call('PATCH','/channels/123/messages/456',{'embeds':[],'attachments':[],'_files':[('threadle-image-0.gif','image/gif',b'GIF89a')]})
        sent=request.call_args.args[0]
        self.assertEqual(sent.method,'PATCH');self.assertIn('multipart/form-data',sent.get_header('Content-type'))
        body=sent.data.split(b'Content-Type: application/json\r\n\r\n',1)[1].split(b'\r\n--',1)[0]
        self.assertEqual(json.loads(body)['attachments'],[{'id':0,'filename':'threadle-image-0.gif'}])
