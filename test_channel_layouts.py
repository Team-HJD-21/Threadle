import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from channel_layouts import CHANNELS, ChannelLayouts, category_name

CATEGORY = category_name('owner/repo')
from connect_auth import ConnectError
from discord_threads import Api, ApiError
from test_connect import FakeBot


class ChannelLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'layouts.sqlite3'
        self.layouts = ChannelLayouts(self.path, '9000')
        self.bot = FakeBot()

    def tearDown(self):
        self.temp.cleanup()

    def test_creates_category_and_five_ordered_channels_without_permission_overwrites(self):
        config = self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        category = next(c for c in self.bot.created.values() if c['type'] == 4)
        self.assertEqual(category['name'], CATEGORY)
        text = [c for c in self.bot.created.values() if c['type'] == 0]
        self.assertEqual([c['name'] for c in text], [name for _, name in CHANNELS])
        self.assertTrue(all(c['parent_id'] == category['id'] for c in text))
        self.assertTrue(all('permission_overwrites' not in p for p in self.bot.created_payloads))
        self.assertEqual(set(config['channels']), {'issue', 'pr', 'discussion'})
        self.assertTrue(config['alert_channel'] and config['ci_channel'])

    def test_repeated_setup_and_process_restart_reuse_the_same_ids(self):
        first = self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(self.layouts.ensure(self.bot, '10', 11, 'owner/repo'), first)
        restarted = ChannelLayouts(self.path, '9000')
        self.assertEqual(restarted.ensure(self.bot, '10', 11, 'owner/repo'), first)
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_two_servers_never_share_category_or_channel_ids(self):
        first = self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        second = self.layouts.ensure(self.bot, '20', 11, 'owner/repo')
        self.assertTrue(set(first['channels'].values()).isdisjoint(second['channels'].values()))
        self.assertEqual(len(self.bot.created_payloads), 12)

    def test_manual_rename_of_an_existing_managed_category_is_preserved(self):
        self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        category = next(c for c in self.bot.created.values() if c['type'] == 4)
        category['name'] = 'Team custom name'
        self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(category['name'], 'Team custom name')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_two_repositories_in_one_server_get_independent_categories_and_channels(self):
        first = self.layouts.ensure(self.bot, '10', 11, 'owner/first')
        second = self.layouts.ensure(self.bot, '10', 22, 'owner/second')
        self.assertTrue(set(first['channels'].values()).isdisjoint(second['channels'].values()))
        self.assertEqual({c['name'] for c in self.bot.created.values() if c['type'] == 4}, {'🔔 first', '🔔 second'})
        self.assertEqual(len(self.bot.created_payloads), 12)
        self.assertEqual(self.layouts.ensure(self.bot, '10', 11, 'owner/first'), first)
        self.assertEqual(len(self.bot.created_payloads), 12)

    def test_same_name_from_different_owners_is_disambiguated(self):
        self.layouts.ensure(self.bot, '10', 11, 'one/docs')
        self.layouts.ensure(self.bot, '10', 22, 'two/docs')
        self.assertEqual({c['name'] for c in self.bot.created.values() if c['type'] == 4}, {'🔔 docs', '🔔 two/docs'})

    def test_repository_rename_reuses_ids_and_updates_only_automatic_category_name(self):
        first = self.layouts.ensure(self.bot, '10', 11, 'owner/before')
        self.assertEqual(self.layouts.ensure(self.bot, '10', 11, 'owner/after'), first)
        self.assertEqual(next(c for c in self.bot.created.values() if c['type'] == 4)['name'], '🔔 after')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_partial_definite_failure_resumes_without_recreating_completed_channels(self):
        create = self.bot.create_channel
        failed = False

        def fail_once(guild, payload):
            nonlocal failed
            if len(self.bot.created_payloads) == 3 and not failed:
                failed = True
                raise ApiError('Discord', 403)
            return create(guild, payload)

        self.bot.create_channel = fail_once
        with self.assertRaises(ConnectError):
            self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(self.bot.created_payloads), 3)
        self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_lost_creation_response_is_reconciled_for_category_and_text_channel(self):
        create = self.bot.create_channel
        lost = set()

        def lose_response(guild, payload):
            result = create(guild, payload)
            if len(self.bot.created_payloads) in (1, 4) and len(self.bot.created_payloads) not in lost:
                lost.add(len(self.bot.created_payloads))
                raise ApiError('Discord', 'network')
            return result

        self.bot.create_channel = lose_response
        self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_unknown_creation_result_is_not_blindly_reposted(self):
        attempts = []

        def unavailable(guild, payload):
            attempts.append(payload)
            raise ApiError('Discord', 'network')

        self.bot.create_channel = unavailable
        for _ in range(2):
            with self.assertRaises(ConnectError):
                self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(attempts), 1)

    def test_no_channel_is_created_without_manage_channels_permission(self):
        self.bot.manage = False
        with self.assertRaises(ConnectError) as failure:
            self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(failure.exception.status, 403)
        self.assertEqual(self.bot.created_payloads, [])

    def test_unrelated_existing_category_is_never_modified_or_adopted(self):
        self.bot.created['42'] = {'id': '42', 'type': 4, 'guild_id': '10', 'name': CATEGORY,
                                  'permission_overwrites': [{'id': '10', 'type': 0, 'deny': '1024'}]}
        original = dict(self.bot.created['42'])
        self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(self.bot.created['42'], original)
        self.assertNotIn(('PATCH', '/channels/42'), self.bot.calls)

    def test_concurrent_request_is_rejected_before_discord_mutations(self):
        with self.layouts.claim('10:11'):
            with self.assertRaises(ConnectError) as failure:
                self.layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(failure.exception.status, 409)
        self.assertEqual(self.bot.created_payloads, [])

    def test_rate_limit_is_remembered_instead_of_reposting_before_retry_after(self):
        now = [1000]
        layouts = ChannelLayouts(self.path, '9000', clock=lambda: now[0])
        attempts = []
        create = self.bot.create_channel

        def rate_limit(guild, payload):
            attempts.append(payload)
            if len(attempts) == 1:
                raise ApiError('Discord', 429, retry_after=60)
            return create(guild, payload)

        self.bot.create_channel = rate_limit
        for _ in range(2):
            with self.assertRaises(ConnectError):
                layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(attempts), 1)
        now[0] += 61
        layouts.ensure(self.bot, '10', 11, 'owner/repo')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_channel_creation_transport_does_not_retry_an_uncertain_post(self):
        with patch('discord_threads.urlopen', side_effect=URLError('lost response')) as opener:
            with self.assertRaises(ApiError):
                Api('Discord', 'https://discord.com/api/v10', 'test-token', 'Bot').create_channel('10', {'name': 'test', 'type': 4})
        self.assertEqual(opener.call_count, 1)


if __name__ == '__main__':
    unittest.main()
