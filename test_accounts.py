import copy
import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from account_links import Accounts, command_definition
from account_mentions import MentionPolicy, names, replace
from account_web import AccountWeb
from comment_bridge import CommentBridge, UserApi
from connect_auth import ConnectError
from connect_store import Sessions
from discord_connect import DiscordConnect
from discord_threads import ApiError
from service import Service
from test_connect import settings
from test_service import config


class AccountTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = Service(self.root, {'DISCORD_BOT_TOKEN': 'bot', 'GITHUB_APP_WEBHOOK_SECRET': 'app'})
        self.settings = settings()
        self.sessions = Sessions(self.root / 'connect.sqlite3', self.settings.session_key)
        self.clock = Mock(return_value=1000.)
        self.accounts = Accounts(self.service, self.settings, self.sessions.cipher, self.clock)
        self.service.accounts = self.accounts
        self.profile = {'id': 91, 'login': 'alice'}
        self.credential = {'access_token': 'access-secret', 'token_type': 'bearer', 'expires_in': 28800,
                           'refresh_token': 'refresh-secret', 'refresh_token_expires_in': 100000}
        self.binding = self.service.registry.get(self.service.registry.add('team', 11, 'one/repo', 101, 'app', config(1000)))
        self.topic = 'github-thread:one/repo:pr:7'
        with self.service.queue(self.binding).connect() as db:
            db.execute('INSERT INTO links VALUES(?,?,?)', (self.topic, '1002', '7777'))
        self.api = Mock()
        self.api.call.side_effect = self.discord_call
        self.service.discord = lambda: self.api
        self.github = Mock()
        self.github.call.side_effect = self.github_call
        self.bridge = CommentBridge(self.service, self.accounts, self.clock, lambda service, token: self.github)
        self.service.comment_bridge = self.bridge
        self.writes, self.comments = [], []
        self.message = {'id': '8000', 'guild_id': '1000', 'channel_id': '7777', 'author_id': '55',
                        'author': {'id': '55', 'bot': False}, 'content': 'Hello from Discord', 'attachments': []}

    def link(self, discord='55', guild='1000', profile=None, mentions=True, comments=True):
        self.accounts.link(discord, guild, profile or self.profile, self.credential, mentions, comments)

    def discord_call(self, method, path, data=None):
        if method in ('PUT', 'POST'):
            return {'id': '9000'}
        if path == '/oauth2/applications/@me':
            return {'flags': 1 << 19}
        if path.startswith('/guilds/1000/members/'):
            return {'user': {'id': path.rsplit('/', 1)[1]}, 'roles': []}
        if path == '/guilds/1000/roles':
            return [{'id': '1000', 'permissions': str((1 << 10) | (1 << 38))}]
        if path == '/channels/7777':
            return {'id': '7777', 'type': 11, 'guild_id': '1000', 'parent_id': '1002', 'thread_metadata': {}}
        if path == '/channels/1002':
            return {'id': '1002', 'type': 0, 'guild_id': '1000', 'permission_overwrites': []}
        if '/messages/' in path:
            return {'id': path.rsplit('/', 1)[1], 'author': {'id': '55'}}
        raise AssertionError(path)

    def github_call(self, method, path, data=None):
        if path == '/user' or path == '/users/alice':
            return self.profile
        if path == '/repositories/11':
            return {'id': 11, 'full_name': 'one/repo'}
        if path.startswith('/repos/one/repo/issues/7/comments?'):
            return self.comments
        if method == 'GET' and path == '/repos/one/repo/issues/7':
            return {'number': 7, 'pull_request': {'url': 'pr'}, 'locked': False}
        if method == 'POST' and path == '/repos/one/repo/issues/7/comments':
            self.writes.append(data)
            return {'id': 44, 'html_url': 'https://github.com/one/repo/pull/7#issuecomment-44'}
        if path == '/graphql':
            if 'mutation' in data['query']:
                self.writes.append(data)
                return {'data': {'addDiscussionComment': {'comment': {'databaseId': 44, 'url': 'https://github.com/one/repo/discussions/7#discussioncomment-44'}}}}
            return {'data': {'repository': {'databaseId': 11, 'discussion': {'id': 'D_123', 'number': 7, 'locked': False,
                'comments': {'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}
        raise AssertionError(path)

    def job(self):
        with self.accounts.db() as db:
            db.row_factory = __import__('state_db').Row
            return dict(db.execute('SELECT * FROM comment_outbox').fetchone())

    def echo(self, body, **values):
        payload = {'action': 'created', 'issue': {'number': 7, 'pull_request': {}},
                   'repository': {'id': 11}, 'comment': {'id': 44, 'body': body, 'html_url': 'url', 'user': {'id': 91}}}
        payload.update(values)
        return payload

    def test_tokens_and_pending_bodies_are_encrypted_in_the_backed_up_database(self):
        self.link()
        self.bridge.ingest(self.message)
        with self.accounts.db() as db:
            token = db.execute('SELECT credential FROM personal_accounts').fetchone()[0]
            body = db.execute('SELECT payload FROM comment_outbox').fetchone()[0]
        self.assertNotIn(b'access-secret', token)
        self.assertNotIn(b'refresh-secret', token)
        self.assertNotIn(b'Hello from Discord', body)
        self.assertEqual(self.accounts.decode(token)['access_token'], 'access-secret')
        self.assertEqual(self.accounts.decode(body)['content'], 'Hello from Discord')

    def test_guild_consent_is_separate_and_mentions_only_discards_credentials(self):
        self.link(comments=False)
        self.assertFalse(self.accounts.get('55', '1000')['authorized'])
        self.assertFalse(self.accounts.get('55', '2000')['mentions'])
        self.assertFalse(self.bridge.ingest(self.message))
        self.link()
        self.link(guild='2000', comments=False)
        self.assertTrue(self.accounts.get('55', '1000')['comments'])
        self.assertFalse(self.accounts.get('55', '2000')['comments'])
        self.assertTrue(self.accounts.get('55', '1000')['authorized'])

    def test_cannot_bind_someone_elses_github_or_silently_replace_identity(self):
        self.link()
        with self.assertRaises(ConnectError):
            self.link(discord='56')
        with self.assertRaises(ConnectError):
            self.link(profile={'id': 92, 'login': 'bob'})
        self.assertEqual(self.accounts.get('55')['github'], '91')

    def test_disconnect_removes_tokens_and_cancels_pending_comments(self):
        self.link()
        self.bridge.ingest(self.message)
        self.accounts.disconnect(discord='55')
        self.assertIsNone(self.accounts.get('55'))
        self.assertEqual(self.job()['state'], 'failed')
        self.assertIsNone(self.job()['payload'])
        self.assertFalse(self.bridge.tick())
        self.assertFalse(self.writes)

    def test_refresh_rotates_once_and_removes_old_refresh_token_before_exchange(self):
        self.link()
        generation = self.accounts.get('55')['generation']
        self.clock.return_value = 30000
        refreshed = dict(self.credential, access_token='next-access', refresh_token='next-refresh')
        def exchange(*args, **kwargs):
            with self.accounts.db() as db:
                self.assertIsNone(db.execute('SELECT credential FROM personal_accounts').fetchone()[0])
            return refreshed
        with patch('account_links.UserApis.request', side_effect=exchange) as request:
            with self.accounts.lock:
                self.assertEqual(self.accounts.token('55', generation), 'next-access')
                self.assertEqual(self.accounts.token('55', generation), 'next-access')
            self.assertEqual(request.call_count, 1)
        with self.accounts.db() as db:
            raw = db.execute('SELECT credential FROM personal_accounts').fetchone()[0]
        self.assertEqual(self.accounts.decode(raw)['refresh_token'], 'next-refresh')

    def test_refresh_failure_requires_reconnect_without_reusing_refresh_token(self):
        self.link()
        generation = self.accounts.get('55')['generation']
        self.clock.return_value = 30000
        with patch('account_links.UserApis.request', side_effect=ConnectError('expired', 403)) as request:
            for _ in range(2):
                with self.assertRaises(ConnectError):
                    self.accounts.token('55', generation)
            self.assertEqual(request.call_count, 1)

    def test_normal_members_can_use_account_commands_and_replies_are_private(self):
        payload = {'id': '9999', 'guild_id': '1000', 'channel_id': '1002', 'member': {'permissions': '0', 'user': {'id': '55'}},
                   'data': {'name': 'account', 'options': [{'name': 'connect'}]}}
        status, response = self.accounts.interaction(payload, self.sessions, DiscordConnect.message)
        self.assertEqual(status, 200)
        self.assertEqual(response['data']['flags'], 64)
        url = response['data']['components'][0]['components'][0]['url']
        code = parse_qs(urlsplit(url).query)['ticket'][0]
        with self.assertRaises(ValueError):
            self.sessions.consume_ticket(code)
        self.assertEqual(self.sessions.consume_ticket(code, purpose='account')['user'], '55')
        self.assertIsNone(command_definition()['default_member_permissions'])

    def test_only_opted_in_human_messages_in_a_known_thread_are_enqueued(self):
        self.link()
        for updates in ({'webhook_id': '99'}, {'author': {'bot': True}}, {'type': 21},
                        {'channel_id': '1002'}, {'guild_id': '2000'}, {'author_id': '56'}, {'content': ''}):
            with self.subTest(updates=updates):
                self.assertFalse(self.bridge.ingest(dict(self.message, **updates)))
        self.assertTrue(self.bridge.ingest(self.message))
        self.assertFalse(self.bridge.ingest(self.message))

    def test_comment_write_uses_user_identity_and_success_feedback_is_not_another_comment(self):
        self.link()
        self.bridge.ingest(self.message)
        self.assertTrue(self.bridge.tick())
        self.assertEqual(len(self.writes), 1)
        self.assertIn('Hello from Discord\n<!-- threadle-comment:', self.writes[0]['body'])
        self.assertEqual(self.job()['state'], 'done')
        self.assertIsNone(self.job()['payload'])
        self.assertFalse(self.bridge.tick())
        self.assertEqual(len(self.writes), 1)
        self.assertTrue(any(call.args[0] == 'PUT' for call in self.api.call.call_args_list))

    def test_unlinked_users_cannot_trigger_installation_token_fallback(self):
        self.assertFalse(self.bridge.ingest(self.message))
        self.github.call.assert_not_called()
        self.link()
        self.bridge.ingest(self.message)
        self.accounts.disconnect(discord='55')
        self.bridge.tick()
        self.assertFalse(self.writes)

    def test_changed_discord_access_fails_closed_before_github_write(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.discord_call
        self.api.call.side_effect = lambda method, path, data=None: ({'id': '7777', 'type': 11, 'guild_id': '2000', 'parent_id': '1002'} if path == '/channels/7777' else original(method, path, data))
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'failed')
        self.assertFalse(self.writes)

    def test_deleted_discord_message_and_missing_repository_access_do_not_post(self):
        self.link()
        self.bridge.ingest(self.message)
        self.github.call.side_effect = ApiError('GitHub', 404)
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'failed')
        self.assertFalse(self.writes)

    def test_early_webhook_matches_persisted_sending_intent_and_stops_echo(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.github_call
        def call(method, path, data=None):
            if method == 'POST' and path.endswith('/comments'):
                self.assertEqual(self.job()['state'], 'sending')
                self.assertTrue(self.bridge.echo(self.binding, 'issue_comment', self.echo(data['body'])))
            return original(method, path, data)
        self.github.call.side_effect = call
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'done')
        self.assertTrue(self.bridge.echo(self.binding, 'issue_comment', self.echo(self.writes[0]['body'])))

    def test_echo_does_not_hide_unrelated_comments_or_other_server_notifications(self):
        self.link()
        self.bridge.ingest(self.message)
        self.bridge.tick()
        body = self.writes[0]['body']
        other = self.service.registry.get(self.service.registry.add('other', 11, 'one/repo', 101, 'app', config(2000)))
        payload = self.echo(body)
        self.assertFalse(self.bridge.echo(other, 'issue_comment', payload))
        self.assertNotIn('threadle-comment:', payload['comment']['body'])
        for payload in (self.echo(body.replace('threadle-comment:', 'threadle-comment:f')), self.echo(body, issue={'number': 8}),
                        self.echo(body, comment={'id': 44, 'user': {'id': 92}, 'body': body}),
                        self.echo(body, comment={'id': 45, 'user': {'id': 91}, 'body': body}),
                        self.echo(body, action='edited')):
            self.assertFalse(self.bridge.echo(self.binding, 'issue_comment', payload))

    def test_late_echo_after_disconnect_does_not_duplicate_or_reauthorize_the_account(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.github_call
        def call(method, path, data=None):
            if method == 'POST' and path.endswith('/comments'):
                self.writes.append(data)
                raise ApiError('GitHub', 'network')
            return original(method, path, data)
        self.github.call.side_effect = call
        self.bridge.tick()
        self.accounts.disconnect(discord='55')
        self.assertEqual(self.job()['state'], 'failed')
        self.assertTrue(self.bridge.echo(self.binding, 'issue_comment', self.echo(self.writes[0]['body'])))
        self.assertEqual(self.job()['state'], 'done')
        self.assertIsNone(self.accounts.get('55'))

    def test_ambiguous_write_recovers_receipt_without_posting_again(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.github_call
        def call(method, path, data=None):
            if method == 'POST' and path.endswith('/comments'):
                self.writes.append(data)
                self.comments = [{'id': 44, 'html_url': 'url', 'body': data['body'], 'user': {'id': 91}}]
                raise ApiError('GitHub', 'network')
            return original(method, path, data)
        self.github.call.side_effect = call
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'uncertain')
        self.clock.return_value += 20
        restarted = CommentBridge(self.service, self.accounts, self.clock, lambda service, token: self.github)
        restarted.tick()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(len(self.writes), 1)

    def test_missing_ambiguous_receipt_never_blindly_reposts(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.github_call
        def call(method, path, data=None):
            if method == 'POST' and path.endswith('/comments'):
                self.writes.append(data)
                raise ApiError('GitHub', 'network')
            return original(method, path, data)
        self.github.call.side_effect = call
        for _ in range(8):
            self.bridge.tick()
            self.clock.return_value += 400
        self.assertEqual(self.job()['state'], 'failed')
        self.assertEqual(self.job()['error'], 'delivery-uncertain')
        self.assertEqual(len(self.writes), 1)

    def test_rate_limited_reconciliation_keeps_ambiguous_state_and_obeys_retry_after(self):
        self.link()
        self.bridge.ingest(self.message)
        original = self.github_call
        def call(method, path, data=None):
            if method == 'POST' and path.endswith('/comments'):
                self.writes.append(data)
                raise ApiError('GitHub', 'network')
            if '/comments?' in path and self.writes:
                raise ApiError('GitHub', 429, retry_after=3600)
            return original(method, path, data)
        self.github.call.side_effect = call
        self.bridge.tick()
        self.clock.return_value += 20
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'uncertain')
        self.assertGreaterEqual(self.job()['next'], self.clock() + 3600)
        self.clock.return_value += 4000
        self.bridge.tick()
        self.assertEqual(len(self.writes), 1)

    def test_restored_presend_backup_reconciles_before_writing(self):
        self.link()
        self.bridge.ingest(self.message)
        self.comments = [{'id': 44, 'html_url': 'url', 'body': 'old text' + self.bridge.marker(self.job()['operation']), 'user': {'id': 91}}]
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'done')
        self.assertFalse(self.writes)

    def test_discussion_comments_use_user_graphql_mutation_and_are_individually_scoped(self):
        self.link()
        with self.service.queue(self.binding).connect() as db:
            db.execute('DELETE FROM links')
            db.execute('INSERT INTO links VALUES(?,?,?)', ('github-thread:one/repo:discussion:7', '1002', '7777'))
        self.bridge.ingest(self.message)
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(self.writes[0]['variables']['input']['discussionId'], 'D_123')
        self.assertIn('Hello from Discord', self.writes[0]['variables']['input']['body'])

    def test_attachment_links_are_bounded_and_never_fetch_arbitrary_urls(self):
        self.link()
        self.assertFalse(self.bridge.ingest(dict(self.message, attachments=[{'url': 'http://127.0.0.1/private'}])))
        message = dict(self.message, content='', attachments=[{'url': 'https://cdn.discordapp.com/attachments/1/2/image.png', 'filename': 'screenshot.png'}])
        self.assertTrue(self.bridge.ingest(message))
        self.bridge.tick()
        self.assertIn('![screenshot.png](<https://cdn.discordapp.com/', self.writes[0]['body'])

    def test_revocation_requires_a_verified_webhook_and_cancels_pending_writes(self):
        self.link()
        self.bridge.ingest(self.message)
        body = json.dumps({'action': 'revoked', 'sender': {'id': 91}}).encode()
        signature = 'sha256=' + hmac.new(b'app', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.receive('/github/app-webhook', 'bad', 'github_app_authorization', 'a' * 32, body), 403)
        self.assertTrue(self.accounts.get('55'))
        self.assertEqual(self.service.receive('/github/app-webhook', signature, 'github_app_authorization', 'a' * 32, body), 200)
        self.assertIsNone(self.accounts.get('55'))
        self.assertEqual(self.job()['state'], 'failed')

    def test_missed_global_revocation_is_recovered_without_an_installation_or_repository_id(self):
        from webhook_recovery import WebhookRecovery
        self.link()
        guid = 'a' * 32
        delivery = {'id': 10, 'guid': guid, 'delivered_at': '2026-11-05T00:00:00Z', 'status_code': 0,
                    'installation_id': None, 'repository_id': None, 'event': 'github_app_authorization'}
        api = Mock(last_link='')
        api.call.return_value = [delivery]
        self.service.tokens = SimpleNamespace(app_api=lambda: api)
        recovery = WebhookRecovery(self.service, clock=lambda: 1793836860.)
        self.assertEqual(recovery.check(), 1)
        api.call.assert_any_call('POST', '/app/hook/deliveries/10/attempts')
        body = json.dumps({'action': 'revoked', 'sender': {'id': 91}}).encode()
        signature = 'sha256=' + hmac.new(b'app', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.receive('/github/app-webhook', signature, 'github_app_authorization', guid, body), 200)
        recovery.clock = lambda: 1793840860.
        self.assertEqual(recovery.check(), 0)
        self.assertIsNone(self.accounts.get('55'))

    def test_invalid_user_token_clears_credentials_and_other_pending_comments(self):
        self.link()
        self.bridge.ingest(self.message)
        self.bridge.ingest(dict(self.message, id='8001'))
        self.github.call.side_effect = ApiError('GitHub', 401)
        self.bridge.tick()
        self.assertIsNone(self.accounts.get('55'))
        with self.accounts.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM comment_outbox WHERE state IN ('queued','sending','uncertain')").fetchone()[0], 0)
        self.assertFalse(self.writes)

    def test_gateway_intent_is_detected_without_breaking_notification_clients(self):
        self.assertTrue(self.bridge.discover_intent())
        self.api.call.side_effect = ApiError('Discord', 403)
        self.assertFalse(self.bridge.discover_intent())

    def test_user_write_transport_never_retries_a_post_after_network_failure(self):
        from urllib.error import URLError
        with patch('comment_bridge.urlopen', side_effect=URLError('timeout')) as request:
            with self.assertRaises(ApiError):
                UserApi(self.service, 'user-token').call('POST', '/repos/one/repo/issues/7/comments', {'body': 'hello'})
            self.assertEqual(request.call_count, 1)

    @unittest.skipUnless(__import__('os').name == 'nt', 'Windows production key protection')
    def test_accounts_and_pending_comments_survive_encrypted_migration_and_managed_backup(self):
        from state_encryption import migrate, finalize
        from state_backups import create, verify
        self.link()
        self.bridge.ingest(self.message)
        conversion = self.root / 'cd-deployments' / 'account-encryption'
        migrate(self.root, conversion)
        finalize(self.root, conversion)
        self.assertFalse(self.service.registry.path.read_bytes().startswith(b'SQLite format 3'))
        snapshot = create(self.root)
        self.assertTrue(verify(snapshot)['encrypted'])
        self.assertTrue(self.accounts.get('55', '1000')['comments'])
        self.assertTrue(self.bridge.tick())
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(len(self.writes), 1)

    def policy(self, body='Hi @alice'):
        self.service.tokens = SimpleNamespace(token=lambda *args: 'installation')
        self.service.api_factory = lambda *args: self.github
        return MentionPolicy(self.service, self.accounts, self.binding, 'issue_comment', {'comment': {'body': body}}, 'delivery-1')

    def test_mentions_exclude_code_email_urls_and_team_references(self):
        self.assertEqual(names('Hi @alice `@bob` ```py\n@chris\n``` https://host/@dave mail@eve.com @org/team'), {'alice'})
        self.assertEqual(replace('@alice `@alice`', {'alice': '55'}), '<@55> `@alice`')

    def test_mentions_only_ping_visible_opted_in_members_once_and_never_parse_everyone(self):
        self.link()
        policy = self.policy()
        data = {'embeds': [{'description': 'Hi @alice and @everyone'}], 'allowed_mentions': {'parse': []}}
        value, receipt = policy.prepare('POST', '/channels/7777/messages', data)
        self.assertEqual(value['content'], '<@55>')
        self.assertEqual(value['allowed_mentions'], {'parse': [], 'users': ['55'], 'replied_user': False})
        self.assertIn('@everyone', value['embeds'][0]['description'])
        policy.remember(receipt)
        value, _ = policy.prepare('POST', '/channels/7777/messages', data)
        self.assertFalse(value['allowed_mentions']['users'])
        self.assertNotIn('content', value)
        self.assertEqual(data['embeds'][0]['description'], 'Hi @alice and @everyone')

    def test_silent_messages_and_edits_render_mentions_without_notifying(self):
        self.link()
        policy = self.policy()
        for method, path, flags in (('POST', '/channels/7777/messages', 4096), ('PATCH', '/channels/7777/messages/88', 0)):
            value, _ = policy.prepare(method, path, {'embeds': [{'description': '@alice'}], 'flags': flags})
            self.assertIn('<@55>', value['embeds'][0]['description'])
            self.assertFalse(value['allowed_mentions']['users'])
            self.assertNotIn('content', value)

    def test_scheduled_notifications_cannot_inherit_another_threads_mention_policy(self):
        import threading
        from discord_threads import Api
        api = Api('Discord', 'https://discord.com/api/v10', 'bot', 'Bot')
        policy = object()
        api.mention_policy = policy
        observed = []
        def scheduled():
            observed.append(api.mention_policy)
            api.mention_policy = None
        worker = threading.Thread(target=scheduled)
        worker.start()
        worker.join(timeout=2)
        self.assertEqual(observed, [None])
        self.assertIs(api.mention_policy, policy)

    def test_nonmember_hidden_channel_and_recycled_login_never_ping(self):
        self.link()
        policy = self.policy()
        self.api.call.side_effect = ApiError('Discord', 404)
        data = {'embeds': [{'description': '@alice'}]}
        self.assertEqual(policy.prepare('POST', '/channels/7777/messages', data)[0], data)
        self.api.call.side_effect = self.discord_call
        self.profile = {'id': 99, 'login': 'alice'}
        self.assertEqual(self.policy().prepare('POST', '/channels/7777/messages', data)[0], data)

    def test_github_renames_resolve_by_numeric_identity_and_legacy_delivery_is_unchanged(self):
        from dataclasses import replace as binding_replace
        self.link()
        self.profile = {'id': 91, 'login': 'new-login'}
        original = self.github_call
        self.github.call.side_effect = lambda method, path, data=None: self.profile if path == '/users/new-login' else original(method, path, data)
        policy = self.policy('Hi @new-login')
        data = {'embeds': [{'description': '@new-login'}]}
        value, _ = policy.prepare('POST', '/channels/7777/messages', data)
        self.assertEqual(value['content'], '<@55>')
        self.assertEqual(self.accounts.get('55')['login'], 'new-login')
        policy.binding = binding_replace(self.binding, auth='legacy')
        self.assertEqual(policy.prepare('POST', '/channels/7777/messages', data)[0], data)

    def test_echo_is_consumed_by_delivery_before_thread_cards_or_alerts(self):
        self.link()
        self.bridge.ingest(self.message)
        self.bridge.tick()
        payload = self.echo(self.writes[0]['body'])
        payload['installation'] = {'id': 101}
        payload['repository']['full_name'] = 'one/repo'
        queue = self.service.queue(self.binding)
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new(b'app', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.receive('/github/app-webhook', signature, 'issue_comment', 'a' * 32, body), 202)
        self.service.dispatch()
        threads, feed = Mock(), Mock()
        self.assertTrue(self.service.process(self.binding, threads, feed))
        threads.deliver.assert_not_called()
        feed.topic.assert_not_called()
        self.assertIsNone(queue.next())
        with queue.connect() as db:
            self.assertEqual(db.execute('SELECT done,attempts FROM jobs').fetchone(), (1, 0))


class AccountWebTests(unittest.TestCase):
    setUp = AccountTests.setUp
    discord_call = AccountTests.discord_call
    github_call = AccountTests.github_call
    def web(self, discord='55'):
        web = SimpleNamespace(sessions=self.sessions, settings=self.settings, service=self.service,
                              users=Mock(), redirect=Mock(), send=Mock())
        web.users.exchange.return_value = self.credential
        web.users.discord.return_value = {'id': discord}
        web.users.github.return_value = self.profile
        web.users.guilds.return_value = [{'id': '1000'}]
        return web, AccountWeb(web)

    def pending(self):
        return self.sessions.create({'account_pending': {'user': '55', 'guild': '1000', 'channel': '1002', 'expires': time.time() + 300}})

    def test_account_oauth_verifies_same_discord_identity_before_github_authorization(self):
        web, flow = self.web(discord='56')
        session = self.pending()
        state, _ = self.sessions.state(session, 'account:discord')
        with self.assertRaises(ConnectError):
            flow.callback(None, 'discord', session, {'state': state, 'code': 'code'})
        self.assertIsNone(self.accounts.get('55'))

    def test_github_state_cannot_be_used_before_explicit_consent(self):
        web, flow = self.web()
        session = self.pending()
        state, _ = self.sessions.state(session, 'account:github')
        with self.assertRaises(ConnectError):
            flow.callback(None, 'github', session, {'state': state, 'code': 'code'})
        web.users.exchange.assert_not_called()

    def test_complete_oauth_uses_pkce_expiring_tokens_and_keeps_no_session_credentials(self):
        web, flow = self.web()
        session = self.pending()
        session.data['account_discord_verified'] = True
        self.sessions.save(session)
        flow.post(None, '/connect/account/github', session, {'mentions': '1', 'comments': '1', 'terms': '1', 'age': '1'})
        url = web.redirect.call_args.args[1]
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertEqual(query['scope'], ['offline_access'])
        flow.callback(None, 'github', session, {'state': query['state'][0], 'code': 'code'})
        self.assertTrue(self.accounts.get('55', '1000')['comments'])
        self.assertIsNone(self.sessions.get(session.key))
        with self.assertRaises(ConnectError):
            flow.callback(None, 'github', session, {'state': query['state'][0], 'code': 'code'})

    def test_account_ticket_cannot_be_reused_for_repository_setup(self):
        web, flow = self.web()
        code = self.sessions.ticket('account:123', '55', '1000', '1002')
        flow.get(None, None, {'ticket': code})
        with self.assertRaises(ConnectError):
            flow.get(None, None, {'ticket': code})

    def test_http_account_page_is_private_and_checks_csrf_before_oauth(self):
        import http.client
        import threading
        from http.server import ThreadingHTTPServer
        from service import handler
        from connect_web import Onboarding, COOKIE
        from urllib.parse import urlencode
        web, _ = self.web()
        self.service.web = Onboarding(self.service, self.settings, users=web.users, sessions=self.sessions)
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.service))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        session = self.pending()
        session.data['account_discord_verified'] = True
        self.sessions.save(session)
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        self.addCleanup(connection.close)
        headers = {'Host': 'notify.example', 'Cookie': COOKIE + '=' + session.key}
        connection.request('GET', '/connect/account', headers=headers)
        response = connection.getresponse()
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn('noindex,nofollow', body)
        self.assertIn('no-store', response.getheader('Cache-Control'))
        self.assertIn('name="comments"', body)
        self.assertIn('href="/connect/account?lang=en"', body)
        self.assertNotIn('access-secret', body)
        headers.update({'Origin': self.settings.base, 'Content-Type': 'application/x-www-form-urlencoded'})
        connection.request('POST', '/connect/account/github', urlencode({'csrf': 'wrong', 'comments': '1', 'terms': '1', 'age': '1'}), headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        connection.request('POST', '/connect/account/github', urlencode({'csrf': session.data['csrf'], 'comments': '1', 'terms': '1', 'age': '1'}), headers)
        response = connection.getresponse()
        response.read()
        self.assertEqual(response.status, 303)
        self.assertEqual(urlsplit(response.getheader('Location')).hostname, 'github.com')


if __name__ == '__main__':
    unittest.main()
