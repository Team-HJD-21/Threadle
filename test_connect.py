import http.client
import hashlib
import hmac
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import threading
import time
import unittest
from urllib.parse import parse_qs, urlencode, urlsplit
from unittest.mock import Mock
from http.server import ThreadingHTTPServer

from cryptography.fernet import Fernet
from connect_auth import Settings, UserApis, channel_permissions, manages_guild
from connect_store import Sessions
from connect_web import COOKIE, DETAIL, Onboarding
from service import Service, handler
from discord_threads import ApiError


def settings():
    return Settings('https://notify.example', '9000', 'discord-secret', 7, 'client-id', 'github-secret',
                    'example-app', Fernet.generate_key().decode())


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Mock(return_value=1000)
        self.path = Path(self.temp.name) / 'sessions.sqlite3'
        self.store = Sessions(self.path, Fernet.generate_key().decode(), self.clock)

    def tearDown(self):
        self.temp.cleanup()

    def test_tokens_are_encrypted_cookie_only_contains_an_opaque_session_key(self):
        session = self.store.create({'discord_token': 'private-login-token'})
        self.assertNotIn(b'private-login-token', self.path.read_bytes())
        with self.store.db() as db:
            row = db.execute('SELECT id,data FROM sessions').fetchone()
        self.assertNotEqual(row[0], session.key)
        self.assertNotIn(b'private-login-token', row[1])
        self.assertEqual(self.store.get(session.key).data['discord_token'], 'private-login-token')

    def test_oauth_state_is_single_use_provider_bound_and_browser_bound(self):
        first, other = self.store.create(), self.store.create()
        state, verifier = self.store.state(first, 'github')
        with self.assertRaises(ValueError):
            self.store.consume(other, 'github', state)
        with self.assertRaises(ValueError):
            self.store.consume(first, 'discord', state)
        self.assertEqual(self.store.consume(first, 'github', state), verifier)
        with self.assertRaises(ValueError):
            self.store.consume(first, 'github', state)

    def test_session_and_oauth_state_expire(self):
        session = self.store.create()
        state, _ = self.store.state(session, 'github')
        self.clock.return_value = 1601
        with self.assertRaises(ValueError):
            self.store.consume(session, 'github', state)
        self.clock.return_value = 2801
        self.assertIsNone(self.store.get(session.key))

    def test_login_rotation_invalidates_original_cookie_and_pending_states(self):
        session = self.store.create({'discord_user': '123'})
        state, _ = self.store.state(session, 'github')
        fresh = self.store.rotate(session)
        self.assertIsNone(self.store.get(session.key))
        self.assertEqual(self.store.get(fresh.key).data['discord_user'], '123')
        self.assertNotEqual(session.data['csrf'], fresh.data['csrf'])
        with self.assertRaises(ValueError):
            self.store.consume(session, 'github', state)


class StartupTests(unittest.TestCase):
    def test_empty_service_starts_connection_page_without_personal_token(self):
        import socket
        with tempfile.TemporaryDirectory() as root:
            with socket.socket() as reservation:
                reservation.bind(('127.0.0.1', 0))
                port = reservation.getsockname()[1]
            env = dict(os.environ, ONBOARDING_ENABLED='1', PUBLIC_BASE_URL='https://notify.example',
                       DISCORD_CLIENT_ID='9000', DISCORD_CLIENT_SECRET='unused-discord-secret',
                       GITHUB_APP_ID='7', GITHUB_APP_CLIENT_ID='client-id', GITHUB_APP_CLIENT_SECRET='unused-github-secret',
                       GITHUB_APP_SLUG='example-app', GITHUB_APP_PRIVATE_KEY_FILE='unused-private-key.pem',
                       CONNECT_SESSION_KEY=Fernet.generate_key().decode(), DISCORD_BOT_TOKEN='unused',
                       GITHUB_APP_WEBHOOK_SECRET='unused')
            env.pop('GITHUB_TOKEN', None)
            with open(Path(root) / 'stderr.log', 'w') as errors:
                child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).parent / 'service.py'),
                                          '--state-dir', root, '--port', str(port)],
                                         env=env, stdout=subprocess.DEVNULL, stderr=errors)
                try:
                    deadline = time.monotonic() + 8
                    while True:
                        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=.5)
                        try:
                            connection.request('GET', '/connect', headers={'Host': 'notify.example'})
                            response = connection.getresponse()
                            self.assertEqual(response.status, 200)
                            self.assertIn('Discord', response.read().decode())
                            command = (f"$r=Invoke-WebRequest 'http://127.0.0.1:{port}/connect' "
                                       "-Headers @{Host='notify.example'} -UseBasicParsing -TimeoutSec 3; "
                                       "if ($r.StatusCode -ne 200 -or -not $r.Content.Contains('Discord')) { exit 1 }; 'ready'")
                            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                                    capture_output=True, text=True, timeout=8)
                            self.assertEqual(result.returncode, 0, result.stderr)
                            self.assertEqual(result.stdout.strip(), 'ready')
                            break
                        except OSError:
                            if child.poll() is not None or time.monotonic() >= deadline:
                                self.fail((Path(root) / 'stderr.log').read_text())
                            time.sleep(.1)
                        finally:
                            connection.close()
                finally:
                    child.kill()
                    child.wait(timeout=5)


class PermissionTests(unittest.TestCase):
    def test_discord_requires_manage_server_owner_or_administrator(self):
        self.assertFalse(manages_guild({'permissions': str(1 << 11)}))
        self.assertTrue(manages_guild({'permissions': str(1 << 5)}))
        self.assertTrue(manages_guild({'permissions': str(1 << 3)}))
        self.assertTrue(manages_guild({'owner': True}))

    def test_channel_overwrites_apply_everyone_roles_then_member(self):
        member = {'user': {'id': '9000'}, 'roles': ['20']}
        roles = [{'id': '10', 'permissions': str(DETAIL)}, {'id': '20', 'permissions': '0'}]
        channel = {'permission_overwrites': [{'id': '10', 'type': 0, 'deny': str(1 << 11), 'allow': '0'},
                                            {'id': '20', 'type': 0, 'deny': '0', 'allow': str(1 << 11)},
                                            {'id': '9000', 'type': 1, 'deny': str(1 << 35), 'allow': '0'}]}
        perms = channel_permissions('10', member, roles, channel)
        self.assertTrue(perms & (1 << 11))
        self.assertFalse(perms & (1 << 35))
        roles[1]['permissions'] = str(1 << 3)
        self.assertEqual(channel_permissions('10', member, roles, channel) & DETAIL, DETAIL)

    def test_configuration_rejects_non_https_origin_and_invalid_slug(self):
        env = dict(ONBOARDING_ENABLED='1', PUBLIC_BASE_URL='http://example.com', DISCORD_CLIENT_ID='9000',
                   DISCORD_CLIENT_SECRET='s', GITHUB_APP_ID='7', GITHUB_APP_CLIENT_ID='client',
                   GITHUB_APP_CLIENT_SECRET='s', GITHUB_APP_SLUG='example-app', CONNECT_SESSION_KEY=settings().session_key)
        with self.assertRaises(ValueError):
            Settings.load(env)
        env['PUBLIC_BASE_URL'] = 'https://example.com/path'
        with self.assertRaises(ValueError):
            Settings.load(env)
        env['PUBLIC_BASE_URL'] = 'https://example.com'
        env['GITHUB_APP_SLUG'] = '../another-app'
        with self.assertRaises(ValueError):
            Settings.load(env)

    def test_github_repository_picker_requires_admin_and_filters_other_apps(self):
        users = UserApis(settings())
        users.github = Mock(side_effect=[{'installations': [
            {'id': 101, 'app_id': 7}, {'id': 202, 'app_id': 8}, {'id': 303, 'app_id': 7, 'suspended_at': 'now'}]},
            {'repositories': [{'id': 1, 'permissions': {'admin': True}}, {'id': 2, 'permissions': {'pull': True}}]}])
        self.assertEqual([row['id'] for row in users.installations('user-token')], [101])
        self.assertEqual([row['id'] for row in users.repositories('user-token', 101)], [1])

    def test_github_authorization_uses_pkce_and_does_not_request_broad_repo_scope(self):
        query = parse_qs(urlsplit(settings().authorize('github', 'one-time-state', 'verifier')).query)
        self.assertEqual(query['state'], ['one-time-state'])
        self.assertEqual(query['code_challenge_method'], ['S256'])
        self.assertNotIn('scope', query)
        self.assertNotIn('github-secret', str(query))


class FakeUsers:
    def __init__(self):
        self.revoked_discord = set()
        self.revoked_github = set()
        self.exchanges = []

    def exchange(self, provider, code, verifier):
        self.exchanges.append((provider, code, verifier))
        return {'access_token': provider + ':' + code, 'token_type': 'Bearer', 'scope': 'identify guilds', 'expires_in': 1800}

    def discord(self, token, path):
        return {'id': token.split(':')[1]}

    def github(self, token, path):
        return {'id': token.split(':')[1], 'login': 'user' + token.split(':')[1]}

    def guilds(self, token):
        user = token.split(':')[1]
        if user in self.revoked_discord:
            return []
        return [{'id': '10' if user == '1' else '20', 'name': 'Team ' + user, 'permissions': str(1 << 5)}]

    def installations(self, token):
        user = token.split(':')[1]
        return [{'id': 101 if user == '1' else 202}]

    def repositories(self, token, installation):
        user = token.split(':')[1]
        if user in self.revoked_github or installation != (101 if user == '1' else 202):
            return []
        return [{'id': 11 if user == '1' else 22, 'full_name': 'user' + user + '/repo', 'permissions': {'admin': True}}]


class FakeBot:
    def __init__(self):
        self.denied = False
        self.missing = False
        self.manage = True
        self.created = {}
        self.created_payloads = []
        self.calls = []

    def call(self, method, path, data=None):
        self.calls.append((method, path))
        if path.startswith('/channels/'):
            identity = path.split('/')[2]
            if identity not in self.created:
                raise ApiError('Discord', 404)
            if method == 'PATCH':
                self.created[identity].update(data)
            return dict(self.created[identity])
        if method != 'GET':
            raise AssertionError('Onboarding must not silently send Discord messages')
        if path == '/users/@me':
            return {'id': '9000', 'bot': True}
        if path == '/oauth2/applications/@me':
            return {'id': '9000', 'bot_public': True, 'bot_require_code_grant': False,
                    'integration_types_config': {'0': {}},
                    'redirect_uris': ['https://notify.example/connect/discord/callback']}
        guild = path.split('/')[2]
        if path.endswith('/members/9000'):
            if self.missing:
                raise ApiError('Discord', 404)
            return {'user': {'id': '9000'}, 'roles': ['30']}
        if path.endswith('/roles'):
            return [{'id': guild, 'permissions': '0'}, {'id': '30', 'permissions': str(DETAIL | ((1 << 4) if self.manage else 0))}]
        if path.endswith('/channels'):
            rows = [{'id': str(int(guild) * 10 + i), 'guild_id': guild, 'type': 0, 'name': 'channel-' + str(i),
                     'permission_overwrites': ([{'id': '9000', 'type': 1, 'deny': str(1 << 11), 'allow': '0'}]
                                              if self.denied else [])} for i in range(1, 6)]
            for value in self.created.values():
                if value['guild_id'] == guild:
                    row = dict(value)
                    row['permission_overwrites'] = [{'id': '9000', 'type': 1, 'deny': str(1 << 11), 'allow': '0'}] if self.denied else []
                    rows.append(row)
            return rows
        raise AssertionError(path)

    def create_channel(self, guild, data):
        if not self.manage:
            raise ApiError('Discord', 403)
        identity = str(10000 + len(self.created_payloads))
        self.created_payloads.append(dict(data))
        self.created[identity] = {'id': identity, 'guild_id': guild, **data}
        self.calls.append(('POST', f'/guilds/{guild}/channels'))
        return dict(self.created[identity])


class Browser:
    def __init__(self, server):
        self.server, self.cookie, self.language = server, None, None

    def request(self, method, path, fields=None, origin='https://notify.example', host='notify.example'):
        headers = {'Host': host}
        cookies = []
        if self.cookie:
            cookies.append(COOKIE + '=' + self.cookie)
        if self.language:
            cookies.append('__Host-ghd-language=' + self.language)
        if cookies:
            headers['Cookie'] = '; '.join(cookies)
        body = None
        if fields is not None:
            body = urlencode(fields)
            headers.update({'Content-Type': 'application/x-www-form-urlencoded', 'Origin': origin})
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            connection.request(method, path, body, headers)
            response = connection.getresponse()
            result = response.status, dict(response.getheaders()), response.read().decode()
            for name, value in response.getheaders():
                if name.lower() == 'set-cookie':
                    jar = SimpleCookie(value)
                    if COOKIE in jar:
                        self.cookie = jar[COOKIE].value
                    if '__Host-ghd-language' in jar:
                        self.language = jar['__Host-ghd-language'].value
            return result
        finally:
            connection.close()


class WebFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Path(self.temp.name), {'DISCORD_BOT_TOKEN': 'unused'})
        self.users, self.bot = FakeUsers(), FakeBot()
        self.service.discord = lambda: self.bot
        self.install_api = Mock()
        self.install_api.call.side_effect = lambda method, path: {
            'id': int(path.rsplit('/', 1)[1]), 'full_name': 'user' + ('1' if path.endswith('/11') else '2') + '/repo'}
        self.service.github = lambda binding: self.install_api
        self.web = Onboarding(self.service, settings(), self.users)
        self.service.web = self.web
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.service))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.browser = Browser(self.server)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def session(self, browser=None):
        return self.web.sessions.get((browser or self.browser).cookie)

    def post(self, path, fields=None, browser=None):
        browser = browser or self.browser
        return browser.request('POST', path, {'csrf': self.session(browser).data['csrf'], **(fields or {})})

    def login(self, provider, user='1', browser=None):
        browser = browser or self.browser
        if not browser.cookie:
            self.assertEqual(browser.request('GET', '/connect')[0], 200)
        result = self.post('/connect/' + provider + '/start', browser=browser)
        self.assertEqual(result[0], 303, result)
        query = parse_qs(urlsplit(result[1]['Location']).query)
        state = query['state'][0]
        if provider == 'github':
            self.assertEqual(query['code_challenge_method'], ['S256'])
        result = browser.request('GET', '/connect/' + provider + '/callback?' + urlencode({'state': state, 'code': user}))
        self.assertEqual(result[0], 303, result)
        return state

    def configure(self, user='1', browser=None):
        self.login('discord', user, browser)
        guild = '10' if user == '1' else '20'
        self.assertEqual(self.post('/connect/guild', {'guild': guild}, browser)[0], 303)
        self.login('github', user, browser)
        selection = '101:11' if user == '1' else '202:22'
        self.assertEqual(self.post('/connect/repository', {'repository': selection}, browser)[0], 303)
        return {}

class ConnectionFlowTests(WebFixture):
    def test_common_footer_and_logo_are_present_on_setup_management_and_error_pages(self):
        self.configure()
        for path in ('/connect', '/connect/guilds', '/connect/server', '/connect/repositories', '/connect/channels', '/connect/missing'):
            with self.subTest(path=path):
                result = self.browser.request('GET', path)
                self.assertEqual(result[2].count('<footer class="site-footer">'), 1)
                self.assertIn('alt="Threadle"', result[2])
                self.assertIn('https://teamhjd.com/', result[2])
                self.assertIn('https://docs.teamhjd.com/', result[2])
                self.assertIn('2026-수원영통-1221호', result[2])
        self.post('/connect/save')
        self.assertIn('<footer class="site-footer">', self.browser.request('GET', '/connect/done')[2])

    def test_logo_assets_load_without_login_and_do_not_create_a_session(self):
        for name in ('notify-logo.png', 'teamhjd-wordmark.png'):
            connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
            try:
                connection.request('GET', '/connect/assets/' + name, headers={'Host': 'notify.example'})
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'image/png')
                self.assertIsNone(response.getheader('Set-Cookie'))
                self.assertEqual(response.read()[:8], b'\x89PNG\r\n\x1a\n')
            finally:
                connection.close()
        self.assertEqual(self.browser.request('GET', '/connect/assets/../credentials.json')[0], 404)

    def test_setup_preview_has_no_channel_picker_and_does_not_create_channels(self):
        self.configure()
        result = self.browser.request('GET', '/connect/channels')
        self.assertEqual(result[0], 200)
        self.assertNotIn('<select', result[2])
        self.assertIn('github-alert', result[2])
        self.assertEqual(self.bot.created_payloads, [])

    def test_permission_upgrade_preserves_repository_and_github_login(self):
        self.configure()
        original_token = self.session().data['github_token']
        self.bot.manage = False
        result = self.browser.request('GET', '/connect/channels')
        self.assertIn('/connect/bot/permissions', result[2])
        self.assertEqual(self.bot.created_payloads, [])
        result = self.post('/connect/bot/permissions')
        query = parse_qs(urlsplit(result[1]['Location']).query)
        self.assertTrue(int(query['permissions'][0]) & (1 << 4))
        self.bot.manage = True
        result = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': query['state'][0], 'code': '1'}))
        self.assertEqual(result[1]['Location'], '/connect/channels')
        self.assertEqual(self.session().data['github_token'], original_token)
        self.assertEqual(self.session().data['repository'], '101:11')
        self.assertEqual(self.post('/connect/save')[0], 303)

    def test_unauthorized_repository_and_injected_channels_create_nothing(self):
        self.configure()
        self.assertEqual(self.post('/connect/save', {'issue': '201'})[0], 403)
        self.users.revoked_github.add('1')
        self.assertEqual(self.post('/connect/save')[0], 403)
        self.assertEqual(self.bot.created_payloads, [])

    def test_existing_manual_connection_is_not_migrated_or_given_new_channels(self):
        self.configure()
        config = {'guild_id': '10', 'channels': {'issue': '101', 'pr': '102', 'discussion': '103'},
                  'alert_channel': '104', 'ci_channel': '105', 'workflow_names': ['existing-workflow']}
        identity = self.service.registry.add('existing', 11, 'user1/repo', 101, 'app', config)
        self.bot.manage = False
        self.assertEqual(self.post('/connect/save')[0], 303)
        self.assertEqual(self.session().data['completed'], identity)
        self.assertEqual(self.service.registry.get(identity).config, config)
        self.assertEqual(self.bot.created_payloads, [])

    def test_selecting_server_without_bot_starts_install_then_github_without_management_page(self):
        self.login('discord')
        self.bot.missing = True
        result = self.post('/connect/guild', {'guild': '10'})
        self.assertEqual(result[0], 303)
        target = urlsplit(result[1]['Location'])
        self.assertEqual(target.netloc, 'discord.com')
        query = parse_qs(target.query)
        self.assertEqual(query['guild_id'], ['10'])
        self.assertEqual(query['disable_guild_select'], ['true'])
        self.assertEqual(query['response_type'], ['code'])
        self.assertEqual(set(query['scope'][0].split()), {'bot', 'applications.commands', 'identify', 'guilds'})
        self.assertEqual(query['redirect_uri'], ['https://notify.example/connect/discord/callback'])
        self.bot.missing = False
        result = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': query['state'][0], 'code': '1'}))
        self.assertEqual(result[0], 303, result)
        self.assertEqual(urlsplit(result[1]['Location']).netloc, 'github.com')
        self.assertEqual(self.session().data['guild'], '10')
        self.assertNotIn('pending_install', self.session().data)
        self.assertEqual(self.service.registry.active(), [])

    def test_install_callback_rejects_a_different_discord_account(self):
        self.login('discord')
        self.bot.missing = True
        result = self.post('/connect/guild', {'guild': '10'})
        state = parse_qs(urlsplit(result[1]['Location']).query)['state'][0]
        self.bot.missing = False
        result = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': state, 'code': '2'}))
        self.assertEqual(result[0], 403)
        self.assertEqual(self.service.registry.active(), [])

    def test_selecting_server_with_bot_starts_github_directly(self):
        self.login('discord')
        result = self.post('/connect/guild', {'guild': '10'})
        self.assertEqual(result[0], 303)
        self.assertEqual(urlsplit(result[1]['Location']).netloc, 'github.com')
        self.assertEqual(self.session().data['guild'], '10')

    def test_existing_connection_management_remains_explicit(self):
        self.login('discord')
        result = self.post('/connect/guild', {'guild': '10', 'mode': 'manage'})
        self.assertEqual(result[1]['Location'], '/connect/server')
        self.assertEqual(self.browser.request('GET', '/connect/server')[0], 200)

    def test_new_user_finishes_from_browser_without_pat_or_operator_registration(self):
        fields = self.configure()
        self.assertEqual(self.browser.request('GET', '/connect/channels')[0], 200)
        result = self.post('/connect/save', fields)
        self.assertEqual(result[0], 303, result)
        binding = self.service.registry.active()[0]
        self.assertEqual(binding.auth, 'app')
        self.assertEqual(binding.installation, 101)
        self.assertEqual(binding.config['guild_id'], '10')
        self.assertNotIn('GITHUB_TOKEN', self.service.env)
        self.assertEqual(self.browser.request('GET', '/connect/done')[0], 200)
        self.assertEqual(self.post('/connect/save', fields)[0], 303)
        self.assertEqual(len(self.service.registry.active()), 1)
        self.assertTrue(all(method in ('GET', 'PATCH') or (method == 'POST' and path.endswith('/channels'))
                            for method, path in self.bot.calls))
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_two_users_cannot_access_each_others_servers_repositories_or_connections(self):
        a_fields = self.configure()
        self.assertEqual(self.post('/connect/save', a_fields)[0], 303)
        a_binding = self.service.registry.active()[0]
        other = Browser(self.server)
        self.configure('2', other)
        self.assertEqual(self.post('/connect/guild', {'guild': '10'}, other)[0], 403)
        self.assertEqual(self.post('/connect/repository', {'repository': '101:11'}, other)[0], 403)
        self.assertEqual(self.post('/connect/disconnect', {'binding': a_binding.id}, other)[0], 403)
        self.assertIsNotNone(self.service.registry.get(a_binding.id))

    def test_other_server_channel_id_is_rejected_before_registration(self):
        fields = self.configure()
        fields['issue'] = '201'
        self.assertEqual(self.post('/connect/save', fields)[0], 403)
        self.assertEqual(self.service.registry.active(), [])

    def test_bot_permissions_are_rechecked_on_save(self):
        fields = self.configure()
        self.bot.denied = True
        self.assertEqual(self.post('/connect/save', fields)[0], 403)
        self.assertEqual(self.service.registry.active(), [])

    def test_lost_discord_admin_rights_prevent_save_and_disconnect(self):
        fields = self.configure()
        self.users.revoked_discord.add('1')
        self.assertEqual(self.post('/connect/save', fields)[0], 403)
        self.assertEqual(self.service.registry.active(), [])

    def test_lost_github_repository_access_prevents_save(self):
        fields = self.configure()
        self.users.revoked_github.add('1')
        self.assertEqual(self.post('/connect/save', fields)[0], 403)
        self.assertEqual(self.service.registry.active(), [])

    def test_callback_state_replay_is_rejected_without_another_token_exchange(self):
        state = self.login('discord')
        result = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': state, 'code': '1'}))
        self.assertEqual(result[0], 403)
        self.assertEqual(len(self.users.exchanges), 1)

    def test_cross_site_posts_and_missing_csrf_are_rejected(self):
        self.browser.request('GET', '/connect')
        csrf = self.session().data['csrf']
        self.assertEqual(self.browser.request('POST', '/connect/discord/start', {'csrf': csrf}, origin='https://evil.example')[0], 403)
        self.assertEqual(self.browser.request('POST', '/connect/discord/start', {'csrf': 'wrong'})[0], 403)
        self.assertEqual(self.users.exchanges, [])

    def test_cookie_security_headers_and_host_checks(self):
        status, headers, body = self.browser.request('GET', '/connect')
        self.assertEqual(status, 200)
        for value in ('Secure', 'HttpOnly', 'SameSite=Lax', 'Path=/'):
            self.assertIn(value, headers['Set-Cookie'])
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['Referrer-Policy'], 'same-origin')
        self.assertIn("form-action 'self' https://discord.com https://github.com;", headers['Content-Security-Policy'])
        self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        self.assertNotIn('discord-secret', body)
        self.assertEqual(self.browser.request('GET', '/connect', host='evil.example')[0], 400)

    def test_cancelled_login_has_an_actionable_page_and_can_restart(self):
        self.browser.request('GET', '/connect')
        response = self.post('/connect/discord/start')
        state = parse_qs(urlsplit(response[1]['Location']).query)['state'][0]
        result = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': state, 'error': 'access_denied'}))
        self.assertEqual(result[0], 400)
        self.assertIn('다시 연결', result[2])
        self.assertEqual(self.post('/connect/discord/start')[0], 303)

    def test_disconnect_and_reconnect_do_not_replay_the_previous_binding_queue(self):
        fields = self.configure()
        self.post('/connect/save', fields)
        old = self.service.registry.active()[0]
        self.service.queue(old).put('old-delivery', 'issues', {'old': True})
        self.assertEqual(self.post('/connect/disconnect', {'binding': old.id})[0], 303)
        self.assertIsNone(self.service.registry.get(old.id))
        self.assertEqual(self.post('/connect/save', fields)[0], 303)
        new = self.service.registry.active()[0]
        self.assertNotEqual(old.id, new.id)
        self.assertIsNone(self.service.queue(new).next())

    def test_browser_created_connections_route_signed_events_only_to_the_matching_team(self):
        self.assertEqual(self.post('/connect/save', self.configure())[0], 303)
        other = Browser(self.server)
        self.assertEqual(self.post('/connect/save', self.configure('2', other), other)[0], 303)
        first = next(b for b in self.service.registry.active() if b.repo_id == 11)
        second = next(b for b in self.service.registry.active() if b.repo_id == 22)
        self.service.env['GITHUB_APP_WEBHOOK_SECRET'] = 'app-signing-secret'
        payload = {'repository': {'id': 11, 'full_name': 'user1/repo'}, 'installation': {'id': 101},
                   'action': 'opened', 'issue': {'number': 1, 'title': 'New issue', 'html_url': 'https://github.com/user1/repo/issues/1'}}
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new(b'app-signing-secret', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.receive('/github/app-webhook', signature, 'issues',
                                             'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', body), 202)
        self.service.dispatch()
        self.assertIsNotNone(self.service.queue(first).next())
        self.assertIsNone(self.service.queue(second).next())
        threads, feed = Mock(), Mock()
        threads.deliver.return_value = 'thread-in-first-server'
        self.service.process(first, threads, feed)
        self.assertEqual(threads.deliver.call_args.args[0].repo, 'user1/repo')
        self.assertEqual(feed.topic.call_args.args[-1], 'thread-in-first-server')
        self.assertIsNone(self.service.queue(first).next())

    def test_public_setup_check_rejects_missing_event_subscriptions(self):
        self.service.env['GITHUB_APP_WEBHOOK_SECRET'] = 'app-secret'
        self.service.tokens = Mock()
        app = {'id': 7, 'client_id': 'client-id', 'slug': 'example-app', 'events': [],
               'permissions': {key: 'read' for key in ('issues', 'pull_requests', 'discussions', 'actions')}}
        self.service.tokens.app_api.return_value.call.return_value = app
        with self.assertRaisesRegex(ValueError, 'subscriptions'):
            self.web.check()
        self.web.check(require_events=False)

    def test_private_discord_bot_cannot_be_enabled_for_other_users(self):
        self.service.env['GITHUB_APP_WEBHOOK_SECRET'] = 'app-secret'
        self.service.tokens = Mock()
        self.service.tokens.app_api.return_value.call.return_value = {
            'id': 7, 'client_id': 'client-id', 'slug': 'example-app',
            'permissions': {key: 'read' for key in ('issues', 'pull_requests', 'discussions', 'actions')}}
        original = self.bot.call
        self.bot.call = lambda method, path, data=None: ({'id': '9000', 'bot_public': False}
                          if path == '/oauth2/applications/@me' else original(method, path, data))
        with self.assertRaisesRegex(ValueError, 'Public Bot'):
            self.web.check(require_events=False)


if __name__ == '__main__':
    unittest.main()
