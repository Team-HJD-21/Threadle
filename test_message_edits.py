import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock,patch

from delivery import Queue,CachedThreads
from discord_threads import Api,ApiError,Topic,make_embed
from message_edits import deliver_events
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
    def test_body_edits_do_not_replace_closed_history(self):
        obj=dict(self.topic.metadata)
        self.send({'action':'opened','pull_request':obj},'opened','pull_request')
        thread=self.send({'action':'closed','pull_request':obj},'closed','pull_request')
        edited=dict(obj,body='Changed PR body',updated_at='2026-11-03T00:01:00Z')
        self.send({'action':'edited','pull_request':edited},'edited','pull_request')
        self.assertEqual(len(self.api.posts[thread]),2)
        self.assertIn('Changed PR body',self.api.posts[thread][0]['embeds'][0]['description'])
        self.assertIn('Closed',self.api.posts[thread][1]['embeds'][0]['title'])

    def test_body_bundle_out_of_order_keeps_latest_description(self):
        older=dict(self.topic.metadata,body='Older',updated_at='2026-11-03T00:01:00Z')
        newer=dict(self.topic.metadata,body='Newest',updated_at='2026-11-03T00:02:00Z')
        payload={'_events':[{'event':'pull_request','payload':{'action':'edited','pull_request':newer}}, {'event':'pull_request','payload':{'action':'edited','pull_request':older}}]}
        thread=self.send(payload,'bundle','threadle_bundle')
        self.assertIn('Newest',self.api.posts[thread][0]['embeds'][0]['description'])
        self.assertNotIn('Older',self.api.posts[thread][0]['embeds'][0]['description'])

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
