import unittest
from unittest.mock import Mock, patch
from auth import RepositoryApi
from notification_media import attach_public_images


class MediaRegression(unittest.TestCase):
    url = 'https://github.com/user-attachments/assets/test-image'

    def test_missing_media_permissions_do_not_break_text_notification(self):
        embeds = [{'description': 'Keep this comment', 'image': {'url': self.url}}]
        fetch = Mock(side_effect=RuntimeError('Token unavailable'))
        with patch('notification_media.public_image', return_value=None):
            self.assertEqual(attach_public_images(embeds, fetch), [])
        self.assertEqual(embeds[0]['description'], 'Keep this comment')
        self.assertEqual(embeds[0]['image']['url'], self.url)

    def test_public_image_does_not_require_installation_token(self):
        fetch = Mock(side_effect=AssertionError('Public image requested a token'))
        with patch('notification_media.public_image', return_value=(b'GIF89a', 'gif', 'image/gif')):
            result = attach_public_images([{'image': {'url': self.url}}], fetch)
        self.assertEqual(len(result), 1)
        fetch.assert_not_called()

    def test_repository_api_uses_this_binding_token_for_private_image(self):
        binding = Mock(auth='app', installation=123, repo_id=456, repo='owner/repo')
        tokens = Mock()
        tokens.token.return_value = 'repository-scoped-token'
        client = RepositoryApi(binding, tokens)
        with patch('notification_media.installation_image', return_value=None) as fetch, patch('notification_media.rendered_installation_image', return_value=None) as render:
            client.fetch_media(self.url)
        tokens.token.assert_called_once_with(123, 456)
        fetch.assert_called_once_with(self.url, 'repository-scoped-token')
        render.assert_called_once_with(self.url, 'repository-scoped-token', 'owner/repo')


    def test_private_markdown_download_never_forwards_token_to_cdn(self):
        from notification_media import rendered_installation_image
        from io import BytesIO
        signed = 'https://private-user-images.githubusercontent.com/123/image.gif?jwt=signed'
        response = BytesIO(('<img src="' + signed + '">').encode())
        opener = Mock()
        opener.open.return_value.__enter__ = Mock(return_value=response)
        opener.open.return_value.__exit__ = Mock(return_value=False)
        with patch('notification_media.build_opener', return_value=opener), patch('notification_media.public_image', return_value=(b'GIF89a', 'gif', 'image/gif')) as public:
            result = rendered_installation_image(self.url, 'scoped-token', 'owner/repo')
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, 'https://api.github.com/markdown')
        self.assertEqual(request.get_header('Authorization'), 'Bearer scoped-token')
        public.assert_called_once_with(signed)
        self.assertEqual(result[1], 'gif')

    def test_two_images_use_separate_messages_without_shared_gallery_url(self):
        from notification_layout import message_pages
        pages = message_pages({'url':'https://github.com/owner/repo/issues/1', 'description':'![a](https://example.com/a.png) ![b](https://example.com/b.gif)'})
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(len(page) == 1 for page in pages))
        self.assertNotIn('url', pages[1][0])


    def test_private_render_rejects_external_source_without_request(self):
        from notification_media import rendered_installation_image
        with patch('notification_media.build_opener') as opener:
            self.assertIsNone(rendered_installation_image('https://evil.example/a.png','token','owner/repo'))
        opener.assert_not_called()
