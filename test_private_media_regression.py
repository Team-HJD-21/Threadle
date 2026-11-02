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
        binding = Mock(auth='app', installation=123, repo_id=456)
        tokens = Mock()
        tokens.token.return_value = 'repository-scoped-token'
        client = RepositoryApi(binding, tokens)
        with patch('notification_media.installation_image', return_value=None) as fetch:
            client.fetch_media(self.url)
        tokens.token.assert_called_once_with(123, 456)
        fetch.assert_called_once_with(self.url, 'repository-scoped-token')
