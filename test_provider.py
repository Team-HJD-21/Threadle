import io
import json
import unittest
from unittest.mock import Mock
from urllib.error import HTTPError, URLError

from connect_auth import ConnectError, UserApis
from test_connect import settings


class ProviderRecoveryTests(unittest.TestCase):
    url = 'https://discord.com/api/v10/users/@me/guilds?limit=200&after=0'

    def error(self, status, body=None, headers=None):
        return HTTPError(self.url, status, 'private-error-description', headers or {},
                         io.BytesIO(json.dumps(body or {}).encode()))

    def client(self, outcomes):
        opener, sleep = Mock(side_effect=outcomes), Mock()
        return UserApis(settings(), opener, sleep=sleep, clock=lambda: 0), opener, sleep

    def test_read_recovers_after_429_and_honours_retry_after(self):
        client, opener, sleep = self.client([
            self.error(429, {'retry_after': .75}), io.BytesIO(b'[]')])
        self.assertEqual(client.request(self.url, 'private-user-token'), [])
        sleep.assert_called_once_with(.75)
        self.assertEqual(opener.call_count, 2)

    def test_long_rate_limit_does_not_sleep_or_retry_early(self):
        client, opener, sleep = self.client([self.error(429, {'retry_after': 60})])
        with self.assertRaises(ConnectError) as failure:
            client.request(self.url, 'private-user-token')
        self.assertEqual(failure.exception.status, 429)
        self.assertIn('60', str(failure.exception))
        sleep.assert_not_called()
        self.assertEqual(opener.call_count, 1)

    def test_retry_budget_is_bounded_and_authentication_errors_are_not_retried(self):
        for status, expected in ((503, 3), (401, 1), (403, 1), (400, 1)):
            with self.subTest(status=status):
                client, opener, _ = self.client([self.error(status) for _ in range(3)])
                with self.assertRaises(ConnectError):
                    client.request(self.url, 'private-user-token')
                self.assertEqual(opener.call_count, expected)

    def test_network_read_retries_but_uncertain_token_exchange_does_not_replay_code(self):
        client, opener, _ = self.client([URLError('private-network-detail'), io.BytesIO(b'[]')])
        self.assertEqual(client.request(self.url, 'private-user-token'), [])
        self.assertEqual(opener.call_count, 2)
        for failure in (URLError('private-network-detail'), self.error(503)):
            client, opener, sleep = self.client([failure])
            with self.assertRaises(ConnectError):
                client.exchange('discord', 'private-authorization-code', 'private-verifier')
            self.assertEqual(opener.call_count, 1)
            sleep.assert_not_called()

    def test_log_and_error_page_never_include_tokens_codes_or_provider_error_bodies(self):
        client, _, _ = self.client([self.error(400, {'error': 'private-response-body'})])
        with self.assertLogs('connect.provider', level='WARNING') as logs:
            with self.assertRaises(ConnectError) as failure:
                client.exchange('discord', 'private-authorization-code', 'private-verifier')
        combined = str(failure.exception) + '\n'.join(logs.output)
        self.assertIn('status=400', combined)
        self.assertIn('operation=token-exchange', combined)
        for private in ('private-response-body', 'private-authorization-code', 'private-verifier',
                        'private-error-description', 'discord-secret'):
            self.assertNotIn(private, combined)


if __name__ == '__main__':
    unittest.main()
