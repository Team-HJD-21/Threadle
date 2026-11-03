import io
import json
import unittest
import random
import sys
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from discord_threads import Api, make_embed, topic_from
from notification_layout import group_payload
from notification_media import attach_public_images, public_image, installation_image, multipart_message, MAX_IMAGE_BYTES
import test_readable_notifications as readable
import test_team_features as team


class CardTests(unittest.TestCase):
    payload = readable.ReadableNotificationsTests.payload

    def test_closed_and_merged_prs_keep_actor_and_status_without_description(self):
        for merged in (True,False):
            p=self.payload(action='closed');p['pull_request']['merged']=merged
            topic=topic_from('one/repo','pr',p['pull_request'])
            card=make_embed(topic,'pull_request',p,'en')
            self.assertEqual(card['author']['name'],'editor')
            self.assertNotIn('original description',str(card))
            self.assertNotIn('fields',card)
            self.assertIn('Merged' if merged else 'Closed',card['title'])
            bundle=group_payload([('pull_request',p)])
            self.assertNotIn('original description',str(make_embed(topic,'threadle_bundle',bundle,'en')))

    def test_creation_long_labels_and_metadata_use_full_width_rows(self):
        p=self.payload('issue');p['issue']['labels']=[{'name':'long-label-'+str(n)} for n in range(12)]
        p['issue']['issue_fields']={'Priority':'P0','Effort':'Low'}
        card=make_embed(topic_from('one/repo','issue',p['issue']),'issues',p,'en')
        self.assertEqual(card['author']['name'],'author')
        self.assertTrue(all(not entry['inline'] for entry in card['fields']))
        self.assertIn('Assignees',next(f['value'] for f in card['fields'] if f['name']=='Details'))
        self.assertIn('Labels',[f['name'] for f in card['fields']])
        self.assertIn('Priority: P0',next(f['value'] for f in card['fields'] if f['name']=='Issue fields'))


class ReminderRoundupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=team.NotificationTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)

    def test_twenty_due_prs_share_one_card_and_repeat_on_next_interval(self):
        f=self.fixture
        notices=f.notices(review_hours=4,review_since=f.now[0])
        api=Mock();api.pages.return_value=[{'number':n,'title':'Open PR '+str(n)} for n in range(1,21)]
        notices.reminders(api);f.now[0]+=4*3600;notices.reminders(api)
        # One list, unless Discord's text limit requires a second page.
        self.assertLessEqual(len(f.api.messages),2)
        text=str(f.api.messages)
        self.assertIn('Open PR 20',text);self.assertIn('/pull/1',text)
        count=len(f.api.messages)
        f.notices(review_hours=4,review_since=f.now[0]-4*3600).reminders(api)
        self.assertEqual(len(f.api.messages),count)
        f.now[0]+=4*3600;notices.reminders(api)
        self.assertEqual(len(f.api.messages),2*count)

    def test_failed_second_channel_recovers_first_without_duplicate_after_restart(self):
        f=self.fixture;notices=f.notices(review_hours=4,review_since=f.now[0])
        notices.review_targets=lambda _:['201','202']
        api=Mock();api.pages.return_value=[{'number':7,'title':'Retry safely'}]
        notices.reminders(api);f.now[0]+=4*3600
        call=f.api.call
        def fail(method,path,data=None):
            if method=='POST' and path=='/channels/202/messages':raise RuntimeError('offline')
            return call(method,path,data)
        f.api.call=fail
        with self.assertRaises(RuntimeError):notices.reminders(api)
        self.assertEqual(len(f.api.messages),1)
        f.api.call=call
        f.notices(review_hours=4,review_since=f.now[0]-4*3600).reminders(api)
        self.assertEqual(len(f.api.messages),2)

    def test_pending_reminder_does_not_notify_a_pr_closed_during_failure(self):
        f=self.fixture;notices=f.notices(review_hours=4,review_since=f.now[0])
        api=Mock();api.pages.return_value=[{'number':7,'title':'Now closed'}]
        notices.reminders(api);f.now[0]+=4*3600
        call=f.api.call
        f.api.call=Mock(side_effect=RuntimeError('offline'))
        with self.assertRaises(RuntimeError):notices.reminders(api)
        api.pages.return_value=[];f.api.call=call
        f.notices(review_hours=4,review_since=f.now[0]-4*3600).reminders(api)
        self.assertEqual(f.api.messages,[])
        with f.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM pr_reminder_batches').fetchone()[0],0)


class PublicMediaTests(unittest.TestCase):
    url='https://github.com/user-attachments/assets/example'
    gif=b'GIF89a\x01\x00\x01\x00'+b'animated fixture'

    def response(self,raw,headers=None):
        result=io.BytesIO(raw);result.headers=headers or {};return result

    def test_gif_bytes_survive_public_redirect_and_multipart_upload(self):
        opener=Mock()
        opener.open.side_effect=[HTTPError(self.url,302,'Found',{'Location':'https://user-images.githubusercontent.com/1/demo.gif'},None),self.response(self.gif)]
        with patch('notification_media.build_opener',return_value=opener):
            embeds=[{'image':{'url':self.url},'description':'[Image]('+self.url+')'}]
            files=attach_public_images(embeds)
        self.assertEqual(files[0][2],self.gif)
        self.assertEqual(embeds[0]['image']['url'],'attachment://threadle-image-0.gif')
        raw,content_type=multipart_message({'embeds':embeds,'nonce':'same','_files':files})
        self.assertIn(self.gif,raw);self.assertIn(b'name="files[0]"',raw)
        self.assertIn('multipart/form-data',content_type)
        for call in opener.open.call_args_list:
            self.assertNotIn('Authorization',call.args[0].headers)

    def test_private_unreachable_oversized_and_non_image_keep_original_link(self):
        cases=[HTTPError(self.url,401,'Private',{},None),self.response(b'<html>login</html>'),self.response(b'',{'Content-Length':str(21*1024*1024)})]
        for case in cases:
            opener=Mock()
            if isinstance(case,Exception):opener.open.side_effect=case
            else:opener.open.return_value=case
            with patch('notification_media.build_opener',return_value=opener):
                embeds=[{'image':{'url':self.url}}];self.assertEqual(attach_public_images(embeds),[])
                self.assertEqual(embeds[0]['image']['url'],self.url)

    def test_redirect_cannot_contact_local_or_unknown_hosts(self):
        for destination in ['http://127.0.0.1/','https://127.0.0.1/','https://github.com.evil.test/image','https://github.com:9999/user-attachments/assets/x']:
            opener=Mock();opener.open.side_effect=HTTPError(self.url,302,'Found',{'Location':destination},None)
            with patch('notification_media.build_opener',return_value=opener):self.assertIsNone(public_image(self.url))
            self.assertEqual(opener.open.call_count,1)

    def test_private_attachment_uses_repo_token_only_on_github_request(self):
        opener=Mock()
        destination='https://private-user-images.githubusercontent.com/1/private.gif'
        opener.open.side_effect=HTTPError(self.url,302,'Found',{'Location':destination},None)
        with patch('notification_media.build_opener',return_value=opener), \
             patch('notification_media.public_image',return_value=(self.gif,'gif','image/gif')) as public:
            result=installation_image(self.url,'repo-scoped-token')
        request=opener.open.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'),'Bearer repo-scoped-token')
        self.assertEqual(result,(self.gif,'gif','image/gif'))
        public.assert_called_once_with(destination)
        self.assertEqual(opener.open.call_count,1)

    def test_private_attachment_rejects_untrusted_url_and_oversized_body(self):
        self.assertIsNone(installation_image('https://example.com/user-attachments/assets/x','token'))
        self.assertIsNone(installation_image(self.url,''))
        opener=Mock()
        opener.open.return_value=self.response(b'',{'Content-Length':str(21*1024*1024)})
        with patch('notification_media.build_opener',return_value=opener):
            self.assertIsNone(installation_image(self.url,'repo-scoped-token'))
        self.assertEqual(opener.open.call_count,1)

    def test_private_attachment_rejects_redirect_back_to_github(self):
        opener=Mock()
        opener.open.side_effect=HTTPError(self.url,302,'Found',{'Location':'https://github.com/other/path'},None)
        with patch('notification_media.build_opener',return_value=opener), \
             patch('notification_media.public_image') as public:
            self.assertIsNone(installation_image(self.url,'repo-scoped-token'))
        public.assert_not_called()
        self.assertEqual(opener.open.call_count,1)

    def test_api_sends_multipart_without_json_serializing_binary_data(self):
        client=Api('Discord','https://discord.com/api/v10','fake','Bot')
        with patch('discord_threads.urlopen',return_value=self.response(b'{"id":"sent"}')) as send:
            result=client.call('POST','/channels/123/messages',{'nonce':'same','_files':[('threadle-image-0.gif','image/gif',self.gif)]})
        self.assertEqual(result['id'],'sent')
        request=send.call_args.args[0]
        self.assertIn('multipart/form-data',request.headers['Content-type'])
        self.assertIn(self.gif,request.data)

    def test_large_photo_is_reduced_below_limit_with_aspect_ratio_preserved(self):
        from PIL import Image
        from notification_media import fit_image
        original=Image.frombytes('RGB',(2400,1800),random.Random(7).randbytes(2400*1800*3))
        data=io.BytesIO();original.save(data,format='PNG')
        self.assertGreater(len(data.getvalue()),MAX_IMAGE_BYTES)
        result,extension,mime=fit_image(data.getvalue(),'png','image/png')
        self.assertLessEqual(len(result),MAX_IMAGE_BYTES)
        with Image.open(io.BytesIO(result)) as preview:
            self.assertEqual(preview.size,(1600,1200))
        self.assertEqual((extension,mime),('jpg','image/jpeg'))

    def test_large_transparent_image_keeps_alpha(self):
        from PIL import Image
        from notification_media import fit_image
        image=Image.new('RGBA',(800,600),(20,80,140,90))
        raw=io.BytesIO();image.save(raw,format='PNG')
        result,extension,_=fit_image(raw.getvalue()+b'\0'*MAX_IMAGE_BYTES,'png','image/png')
        self.assertEqual(extension,'png')
        with Image.open(io.BytesIO(result)) as preview:
            self.assertEqual(preview.mode,'RGBA');self.assertEqual(preview.getpixel((0,0))[3],90)

    def test_large_gif_is_reduced_without_discarding_animation(self):
        from PIL import Image
        from notification_media import fit_image
        red=Image.new('RGB',(900,600),'red');blue=Image.new('RGB',(900,600),'blue')
        raw=io.BytesIO();red.save(raw,format='GIF',save_all=True,append_images=[blue],duration=[80,120],loop=0)
        result,extension,_=fit_image(raw.getvalue()+b'\0'*MAX_IMAGE_BYTES,'gif','image/gif')
        self.assertLessEqual(len(result),MAX_IMAGE_BYTES);self.assertEqual(extension,'gif')
        with Image.open(io.BytesIO(result)) as preview:
            self.assertEqual(preview.n_frames,2);self.assertEqual(preview.size,(640,427))
            self.assertEqual(preview.info['duration'],80);preview.seek(1)
            self.assertEqual(preview.info['duration'],120)
