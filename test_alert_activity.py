import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alert_activity import ActivityCards
from alert_feed import AlertFeed
from discord_threads import ApiError
from delivery import Queue
from test_alert_feed import FakeAlertApi


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name) / 'queue.sqlite3')
        self.api = FakeAlertApi()

    def tearDown(self):
        self.temp.cleanup()

    def cards(self, channel='alerts'):
        return ActivityCards(AlertFeed(self.api, {'alert_channel': channel}, self.queue), 'owner/repo')

    def item(self, number, event='a', activity='New commits'):
        return (event, 'pr:' + str(number), f'PR #{number} · {activity}', '',
                'https://discord.com/channels/10/' + str(number), 1)

    def test_first_event_is_immediate_later_topics_and_activities_edit_one_card(self):
        cards = self.cards()
        cards.update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        cards.update([self.item(2), self.item(1, 'b', 'New comment')], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        body = self.api.posts[0]['embeds'][0]['description']
        self.assertIn('[PR #1](https://discord.com/channels/10/1) · New commits, New comment', body)
        self.assertIn('[PR #2](https://discord.com/channels/10/2)', body)
        self.assertEqual(self.api.posts[0]['content'], '')
        self.assertEqual(self.api.posts[0]['allowed_mentions'], {'parse': []})
        self.assertEqual(sum(method == 'PATCH' for method, _, _ in self.api.calls), 1)

    def test_duplicate_types_and_replays_after_restart_do_not_add_lines_or_cards(self):
        self.cards().update([self.item(1), self.item(1, 'duplicate-delivery')], 'live:10')
        self.cards().update([self.item(1)], 'live:11')
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.api.posts[0]['embeds'][0]['description'].count('New commits'), 1)
        self.assertFalse(any(method == 'PATCH' for method, _, _ in self.api.calls))
        self.cards().update([self.item(2)], 'live:11')
        self.assertEqual(len(self.api.posts), 2)

    def test_lost_post_then_new_event_recovers_and_updates_the_original_card(self):
        self.api.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            self.cards().update([self.item(1)], 'live:10')
        self.cards().update([self.item(2)], 'live:10')
        self.cards().update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #1', self.api.posts[0]['embeds'][0]['description'])
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_failed_patch_is_retried_without_posting_a_duplicate(self):
        self.cards().update([self.item(1)], 'live:10')
        call = self.api.call
        def fail(method, path, data=None):
            if method == 'PATCH':
                raise RuntimeError('network unavailable')
            return call(method, path, data)
        with patch.object(self.api, 'call', side_effect=fail), self.assertRaises(RuntimeError):
            self.cards().update([self.item(2)], 'live:10')
        self.cards().update([self.item(2)], 'live:11')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_deleted_activity_card_is_recreated_only_for_new_activity(self):
        self.cards().update([self.item(1)], 'live:10')
        self.api.posts.clear()
        call = self.api.call
        def gone(method, path, data=None):
            if method == 'PATCH':
                raise ApiError('Discord', 404, 10008)
            return call(method, path, data)
        with patch.object(self.api, 'call', side_effect=gone):
            self.cards().update([self.item(2)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #1', self.api.posts[0]['embeds'][0]['description'])
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_many_topics_have_bounded_pages_and_all_thread_links(self):
        self.cards().update([self.item(n) for n in range(1, 26)], 'batch:10')
        self.assertEqual(len(self.api.posts), 3)
        combined = '\n'.join(row['embeds'][0]['description'] for row in self.api.posts)
        for n in range(1, 26):
            self.assertIn('[' + f'PR #{n}' + ']', combined)
        for row in self.api.posts:
            self.assertLess(len(row['embeds'][0]['description'].encode('utf-16-le')) // 2, 4096)

    def test_same_events_are_independent_in_different_channels(self):
        for channel in ('alerts', 'ci'):
            self.cards(channel).update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 2)
        self.assertNotEqual(self.api.posts[0]['nonce'], self.api.posts[1]['nonce'])

    def test_parallel_topics_cannot_overwrite_each_other(self):
        cards = self.cards()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: cards.update([self.item(n)], 'live:10'), range(1, 6)))
        self.assertEqual(len(self.api.posts), 1)
        for n in range(1, 6):
            self.assertIn(f'PR #{n}', self.api.posts[0]['embeds'][0]['description'])
