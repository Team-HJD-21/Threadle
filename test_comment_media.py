import unittest
from unittest.mock import Mock

from comment_media import allowed,CDN,prepare,render,Preview


class OutboundMediaTests(unittest.TestCase):
    def test_images_and_gifs_are_inline_nonimages_remain_links(self):
        payload={'attachments':[{'url':'https://cdn.discordapp.com/attachments/1/2/a.'+ext,'filename':'a.'+ext} for ext in ('png','gif','pdf')]}
        body=render('Hello',payload)
        self.assertIn('![a.png](<https://cdn.discordapp.com/',body)
        self.assertIn('![a.gif](<https://cdn.discordapp.com/',body)
        self.assertIn('[a.pdf](https://cdn.discordapp.com/',body)
        self.assertNotIn('![a.pdf]',body)

    def test_signature_refresh_never_imports_another_attachment(self):
        old='https://cdn.discordapp.com/attachments/1/2/a.png'
        payload={'content':'text','attachments':[{'url':old+'?sig=old','filename':'a.png'}]}
        current={'attachments':[{'url':old+'?sig=new','content_type':'image/png'},
                                {'url':'https://cdn.discordapp.com/attachments/1/3/secret.png','filename':'secret.png'}]}
        prepared=prepare(payload,current)
        self.assertEqual(prepared['attachments'][0]['url'],old+'?sig=new')
        self.assertEqual(len(prepared['attachments']),1)
        self.assertEqual(payload['attachments'][0]['url'],old+'?sig=old')

    def test_picker_gif_uses_real_media_url_in_its_original_text_position(self):
        page='https://tenor.com/view/test-gif-123'
        gif='https://media.tenor.com/real-id/test.gif'
        payload={'content':'Before\n'+page+'\nAfter','attachments':[]}
        current={'embeds':[{'type':'gifv','url':page,'thumbnail':{'url':gif}}]}
        resolve=Mock(side_effect=AssertionError('No page fetch needed'))
        prepared=prepare(payload,current,resolve)
        self.assertEqual(render(payload['content'],prepared),'Before\n![GIF](<'+gif+'>)\nAfter')

    def test_picker_mp4_is_not_mislabeled_as_gif_and_uses_provider_metadata(self):
        page='https://giphy.com/gifs/test';gif='https://media.giphy.com/media/abc/giphy.gif'
        payload={'content':page,'attachments':[]}
        current={'embeds':[{'type':'gifv','url':page,'video':{'url':'https://media.giphy.com/a.mp4'},'thumbnail':{'url':'https://media.giphy.com/a.png'}}]}
        resolve=Mock(return_value=gif)
        self.assertIn(gif,render(page,prepare(payload,current,resolve)))
        resolve.assert_called_once_with(page)
        self.assertEqual(render(page,prepare(payload,current,lambda _:None)),page)

    def test_code_and_unrelated_links_never_change_or_fetch(self):
        page='https://tenor.com/view/test';gif='https://media.tenor.com/a.gif'
        self.assertEqual(render('`'+page+'`',{'attachments':[],'picker_images':{page:gif}}),'`'+page+'`')
        payload={'content':'https://example.com','attachments':[]}
        resolver=Mock(side_effect=AssertionError('Unexpected fetch'))
        self.assertEqual(prepare(payload,{'embeds':[{'type':'gifv','url':'https://evil.test','thumbnail':{'url':gif}}]},resolver)['picker_images'],{})
        for url in ('https://cdn.discordapp.com:444/a','https://cdn.discordapp.com@evil.test/a','https://127.0.0.1/a','http://cdn.discordapp.com/a'):
            self.assertFalse(allowed(url,CDN))

    def test_provider_html_accepts_only_actual_gif_on_known_media_hosts(self):
        parser=Preview();parser.feed('<meta property="og:image" content="http://localhost/private.gif"><meta property="og:image" content="https://media.tenor.com/abc/a.gif">')
        self.assertEqual(parser.images,['https://media.tenor.com/abc/a.gif'])
