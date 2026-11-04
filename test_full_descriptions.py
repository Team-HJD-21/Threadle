from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from discord_threads import Topic,make_embed
from notification_layout import message_pages
from notification_text import split_description,units
from team_notifications import TeamNotifications
from test_team_features import binding,MessageApi
import test_message_edits as edits
from changelog import RELEASES,ADMIN_RELEASES,content

class FullTextTests(unittest.TestCase):
    def test_same_url_continuations_and_final_details_are_visible_in_one_message(self):
        from notification_markdown import adapt
        body='## First section\n'+('Ordinary description line.\n'*145)+'## Final section\nFINAL_LINE'
        for kind,event,key in [('pr','pull_request','pull_request'),('issue','issues','issue'),('discussion','discussion','discussion')]:
            obj={'number':1,'title':'Title','body':body,'labels':[{'name':'bug'}]}
            topic=Topic('owner/repo',kind,1,'Title','https://github.com/owner/repo',obj)
            pages=message_pages(make_embed(topic,event,{'action':'opened',key:obj}))
            self.assertEqual(len(pages),1)
            self.assertGreater(len(pages[0]),1)
            # Apply Discord's URL deduplication rule, rather than just checking
            # that the API payload contains the continuation.
            visible=[];seen=set()
            for embed in pages[0]:
                url=embed.get('url')
                if url and url in seen:continue
                if url:seen.add(url)
                visible.append(embed)
            self.assertEqual(''.join(e.get('description','') for e in visible),adapt(body))
            self.assertEqual(visible[0]['url'],topic.url)
            self.assertTrue(visible[-1]['fields'])
            self.assertIn('bug',str(visible[-1]['fields']))

    def test_link_deduplication_is_local_to_each_message_and_keeps_distinct_links(self):
        url='https://github.com/owner/repo/issues/1'
        original={'title':'Title','url':url,'description':'x'*12000}
        pages=message_pages(original)
        self.assertGreater(len(pages),1)
        self.assertTrue(all(page[0]['url']==url for page in pages))
        self.assertEqual(original['url'],url)
        other='https://github.com/owner/repo/issues/2'
        bundle={'_embeds':[{'url':value,'description':'Text'} for value in (url,url,other)]}
        self.assertEqual([e.get('url') for e in message_pages(bundle)[0]],[url,None,other])
        self.assertEqual(bundle['_embeds'][1]['url'],url)

    def test_every_description_kind_keeps_the_complete_text(self):
        body=''.join(f'Line {n:04d} text and details.\n' for n in range(1000))+'END_OF_DESCRIPTION'
        for kind,event,key in [('pr','pull_request','pull_request'),('issue','issues','issue'),('discussion','discussion','discussion')]:
            obj={'number':1,'title':'Title','body':body}
            topic=Topic('owner/repo',kind,1,'Title','https://github.com/owner/repo',obj)
            pages=message_pages(make_embed(topic,event,{'action':'opened',key:obj}))
            all_text=''.join(e.get('description','') for page in pages for e in page)
            self.assertEqual(all_text,body)
            self.assertGreater(len(pages),1)
            self.assertTrue(all(units(e.get('description',''))<=3500 for page in pages for e in page))
    def test_unicode_and_long_unbroken_lines_are_not_dropped(self):
        body='😀'*6000+'끝'*4000
        chunks=split_description(body)
        self.assertEqual(''.join(chunks),body)
        self.assertTrue(all(units(x)<=3500 for x in chunks))
    def test_code_fences_are_reopened_and_every_code_line_remains(self):
        body='```python\n'+''.join(f'print("code_{n:04d}")\n' for n in range(800))+'```'
        chunks=split_description(body)
        for n in range(800):self.assertEqual(sum(x.count(f'code_{n:04d}') for x in chunks),1)
        for chunk in chunks:
            self.assertTrue(chunk.startswith('```python\n'))
            self.assertTrue(chunk.rstrip().endswith('```'))
            self.assertLessEqual(units(chunk),3500)
    def test_images_after_old_cutoff_are_kept_and_text_is_complete(self):
        body='x'*12000+'\n![A](https://example.com/a.png)\n![B](https://example.com/b.gif)\nLAST_LINE'
        pages=message_pages({'title':'Title','description':body})
        self.assertEqual([e['image']['url'] for page in pages for e in page if e.get('image')],['https://example.com/a.png','https://example.com/b.gif'])
        text=''.join(e.get('description','') for page in pages for e in page)
        self.assertIn('x'*12000,text);self.assertTrue(text.endswith('LAST_LINE'))
    def test_images_and_captions_stay_in_source_order(self):
        body='Before first\n![First](https://example.com/a.png)\nCaption for first\n<img src="https://example.com/b.gif">\nAfter second'
        pages=message_pages({'title':'Title','description':body,'color':0x8957E5,
                             'fields':[{'name':'Details','value':'Final metadata'}]})
        embeds=[embed for page in pages for embed in page]
        self.assertEqual([e['image']['url'] for e in embeds if e.get('image')],
                         ['https://example.com/a.png','https://example.com/b.gif'])
        self.assertIn('Before first',embeds[0]['description'])
        self.assertNotIn('Caption for first',embeds[0]['description'])
        self.assertIn('Caption for first',embeds[1]['description'])
        self.assertEqual(embeds[-1]['description'],'After second')
        self.assertEqual(embeds[-1]['fields'],[{'name':'Details','value':'Final metadata'}])
        self.assertTrue(all(not e.get('fields') for e in embeds[:-1]))
        self.assertTrue(all(e['color']==0x8957E5 for e in embeds))
    def test_media_has_no_redundant_link_but_keeps_comment_and_captions(self):
        for image in ('![Image](https://example.com/a.gif)',
                      '[GIF](https://example.com/a.gif)',
                      '<img src="https://example.com/a.gif" alt="Image">',
                      'https://example.com/a.gif'):
            with self.subTest(image=image):
                pages=message_pages({'title':'New comment',
                                     'description':'LGTM!!\n'+image+'\nOriginal caption'})
                embeds=[e for page in pages for e in page]
                self.assertEqual(embeds[0]['description'],'LGTM!!')
                self.assertEqual(embeds[0]['image']['url'],'https://example.com/a.gif')
                self.assertEqual(embeds[-1]['description'],'Original caption')
                self.assertEqual(embeds[0]['title'],'New comment')
    def test_image_only_comment_stays_a_valid_image_embed(self):
        pages=message_pages({'title':'New comment','description':'![Image](https://example.com/a.gif)'})
        self.assertEqual(len(pages),1)
        self.assertNotIn('description',pages[0][0])
        self.assertEqual(pages[0][0]['image']['url'],'https://example.com/a.gif')
    def test_ordinary_links_and_literal_image_examples_remain(self):
        body='[GitHub](https://github.com/owner/repo)\n`![Image](https://example.com/code.gif)`'
        pages=message_pages({'description':body+'\n![Image](https://example.com/a.gif)'})
        self.assertEqual(pages[0][0]['description'],body)
        self.assertEqual(pages[0][0]['image']['url'],'https://example.com/a.gif')
    def test_repeated_image_occurrences_are_not_moved_or_deduplicated(self):
        body='Step one\n![A](https://example.com/a.png)\nStep two\n![A](https://example.com/a.png)\nDone'
        pages=message_pages({'description':body})
        embeds=[e for page in pages for e in page]
        self.assertEqual([e['image']['url'] for e in embeds if e.get('image')],
                         ['https://example.com/a.png','https://example.com/a.png'])
        self.assertIn('Step two',embeds[1]['description'])
        self.assertEqual(embeds[-1]['description'],'Done')
    def test_details_follow_final_image_without_trailing_body(self):
        pages=message_pages({'description':'Intro\n![A](https://example.com/a.png)',
                             'fields':[{'name':'Details','value':'Value'}],
                             'timestamp':'2026-11-05T00:00:00Z'})
        embeds=[e for page in pages for e in page]
        self.assertIn('image',embeds[0]);self.assertFalse(embeds[0].get('fields'))
        self.assertNotIn('image',embeds[-1]);self.assertIn('fields',embeds[-1])
        self.assertIn('timestamp',embeds[-1]);self.assertNotIn('timestamp',embeds[0])
    def test_long_text_between_images_respects_message_limits(self):
        body='Before\n![A](https://example.com/a.png)\n'+'middle '*1800+'\n![B](https://example.com/b.gif)\nLast'
        pages=message_pages({'title':'Title','description':body})
        flattened=[e for page in pages for e in page]
        first=next(i for i,e in enumerate(flattened) if e.get('image',{}).get('url')=='https://example.com/a.png')
        second=next(i for i,e in enumerate(flattened) if e.get('image',{}).get('url')=='https://example.com/b.gif')
        self.assertNotIn('middle',flattened[first].get('description',''))
        self.assertIn('middle',flattened[second]['description'])
        self.assertEqual(''.join(e.get('description','') for e in flattened).count('middle'),1800)
        self.assertTrue(all(units(e.get('description',''))<=3500 for e in flattened))
        for page in pages:
            self.assertLess(sum(units(e.get('description',''))+units(e.get('title','')) for e in page),6000)
    def test_long_pr_titles_all_have_individual_digest_links(self):
        with tempfile.TemporaryDirectory() as folder:
            from delivery import Queue
            queue=Queue(Path(folder)/'q.db');api=MessageApi()
            b=binding({'digest':'daily','digest_hour':0,'utc_offset':0,'enabled_at':1})
            notices=TeamNotifications(b,queue,api,clock=lambda:2000000000)
            prs=[{'number':n,'title':'[details] _'*12+' (mobile)','updated_at':''} for n in range(1,16)]
            issues=[{'number':42,'title':'Issue','url':'https://github.com/owner/repo/issues/42'}]
            with patch('team_notifications.open_prs',return_value=prs),patch('team_notifications.open_issue_digest',return_value=(1,issues)):
                notices.digest(Mock());notices.digest(Mock())
            text=''.join(e.get('description','') for _,m in api.messages for e in m['embeds'])
            for n in range(1,16):self.assertIn(f'https://github.com/owner/repo/pull/{n})',text)
            self.assertIn('https://github.com/owner/repo/issues/42',text)
            self.assertIn('일일',api.messages[0][1]['embeds'][0]['title'])
            for _,m in api.messages:self.assertLess(sum(units(e.get('description',''))+units(e.get('title',''))+units(e.get('footer',{}).get('text','')) for e in m['embeds']),6000)
    def test_every_release_has_short_localized_highlights(self):
        versions=[row[0] for row in RELEASES+ADMIN_RELEASES]
        for n in range(1,9):self.assertIn('v1.0.0-beta.'+str(n),versions)
        self.assertEqual([r[0] for r in RELEASES].count('Beta'),2)
        for _,ko,en,items in RELEASES+ADMIN_RELEASES:
            self.assertTrue(ko and en);self.assertTrue(1<=len(items)<=3)
            for ko,en in items:self.assertLessEqual(len(ko),150);self.assertLessEqual(len(en),260)
        for lang in (0,1):
            html=content(lambda ko,en:(ko,en)[lang])
            for word in ('SQLite','Pillow','nonce','Receiver','410 Gone','255'):self.assertNotIn(word,html)

class LongEditTests(unittest.TestCase):
    setUp=edits.EditTests.setUp
    tearDown=edits.EditTests.tearDown
    payload=edits.EditTests.payload
    send=edits.EditTests.send
    def test_long_comment_is_complete_and_shrinking_edit_retains_first_id(self):
        body=''.join(f'Long comment {n:04d}\n' for n in range(900))+'END_COMMENT'
        thread=self.send(self.payload(body),'long');first=self.api.posts[thread][0]['id']
        self.assertGreater(len(self.api.posts[thread]),1)
        self.assertEqual(''.join(e.get('description','') for m in self.api.posts[thread] for e in m['embeds']),body)
        self.send(self.payload('Short','edited',time='2026-11-03T00:01:00Z'),'short')
        self.assertEqual(len(self.api.posts[thread]),1);self.assertEqual(self.api.posts[thread][0]['id'],first)
        self.assertEqual(self.api.posts[thread][0]['embeds'][0]['description'],'Short')

    def test_description_fields_are_last_and_all_continuations_keep_color(self):
        from notification_text import description_embeds
        original={'title':'Title','description':'Paragraph\n'*800,'color':0x8B949E,'fields':[{'name':'Details','value':'Value'}]}
        parts=description_embeds(original)
        self.assertGreater(len(parts),1)
        self.assertTrue(all(not part.get('fields') for part in parts[:-1]))
        self.assertEqual(parts[-1]['fields'],original['fields'])
        self.assertTrue(all(part['color']==original['color'] for part in parts))
