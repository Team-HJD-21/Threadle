import http.client
import json
import time
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from operator_web import AccessIdentity, COOKIE, revision
from test_connect import WebFixture
from test_service import config


class OperatorTests(WebFixture):
    def setUp(self):
        super().setUp()
        self.identity = self.service.registry.add('one', 11, 'one/repo', 101, 'app', config(1000))
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims = {'sub': 'operator-one', 'email': 'owner@example.com', 'aud': 'a' * 64,
                       'iss': 'https://example.cloudflareaccess.com', 'iat': int(time.time()), 'exp': int(time.time()) + 300}
        self.cookie = None

    def enable(self):
        operator = self.service.operator
        operator.settings = {'emails': ['owner@example.com']}
        operator.identity = AccessIdentity({'team_domain': 'example.cloudflareaccess.com', 'audience': 'a' * 64})
        operator.identity.keys = Mock()
        operator.identity.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=self.private.public_key())

    def request(self, method='GET', path='/connect/admin', fields=None, claims=None, headers=None, signed=True):
        token = jwt.encode(claims or self.claims, self.private, algorithm='RS256', headers={'kid': 'test'})
        all_headers = {'Host': 'notify.example'}
        if signed:
            all_headers['Cf-Access-Jwt-Assertion'] = token
        if self.cookie:
            all_headers['Cookie'] = COOKIE + '=' + self.cookie
        body = None
        if fields is not None:
            body = urlencode(fields)
            all_headers.update({'Content-Type': 'application/x-www-form-urlencoded', 'Origin': 'https://notify.example'})
        all_headers.update(headers or {})
        client = http.client.HTTPConnection(*self.server.server_address, timeout=5)
        try:
            client.request(method, path, body, all_headers)
            response = client.getresponse()
            result = response.status, dict(response.getheaders()), response.read().decode()
            for name, value in response.getheaders():
                if name.lower() == 'set-cookie':
                    jar = SimpleCookie(value)
                    if COOKIE in jar:
                        self.cookie = jar[COOKIE].value
            return result
        finally:
            client.close()

    def fields(self, action):
        row = self.service.operator.rows()[0]
        session = self.web.sessions.get(self.cookie)
        return {'binding': row['id'], 'revision': revision(row), 'action': action, 'csrf': session.data['csrf'], 'confirmation': row['repo']}

    def test_verified_backup_status_is_visible_to_authenticated_admin(self):
        from state_backups import create
        create(self.service.root)
        self.enable()
        status,_,body=self.request()
        self.assertEqual(status,200)
        self.assertIn('DB 백업·복원 검증',body)
        self.assertNotIn('database-backups/',body)

    def test_disabled_and_forged_identity_headers_cannot_read_registry(self):
        for _ in range(2):
            response = self.request(headers={'Cf-Access-Authenticated-User-Email': 'owner@example.com'}, signed=False)
            self.assertEqual(response[0], 403)
            self.assertNotIn('one/repo', response[2])
            self.enable()
        self.assertEqual(self.service.operator.audit(), [])

    def test_wrong_audience_issuer_expiration_signature_and_email_are_rejected(self):
        self.enable()
        cases = [{'aud': 'b' * 64}, {'iss': 'https://other.cloudflareaccess.com'}, {'exp': int(time.time()) - 1}, {'email': 'someone@example.com'}]
        for changed in cases:
            response = self.request(claims=dict(self.claims, **changed))
            self.assertEqual(response[0], 403)
            self.assertNotIn('one/repo', response[2])
        self.service.operator.identity.keys.get_signing_key_from_jwt.return_value = SimpleNamespace(key=rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key())
        self.assertEqual(self.request()[0], 403)

    def test_authorized_dashboard_is_private_and_does_not_create_queue_or_expose_secrets(self):
        self.enable()
        queue_path = self.service.root / 'connections' / self.identity / 'queue.sqlite3'
        result = self.request()
        self.assertEqual(result[0], 200)
        self.assertIn('one/repo', result[2])
        self.assertIn('1000', result[2])
        self.assertIn('noindex', result[1]['X-Robots-Tag'])
        self.assertEqual(result[1]['Cache-Control'], 'no-store')
        self.assertIn('HttpOnly', result[1]['Set-Cookie'])
        self.assertNotIn('unused', result[2])
        self.assertFalse(queue_path.exists())

    def test_csrf_origin_and_confirmation_are_required_for_mutation(self):
        self.enable()
        self.request()
        fields = self.fields('pause')
        self.assertEqual(self.request('POST', '/connect/admin/action', fields, headers={'Origin': 'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', '/connect/admin/action', dict(fields, csrf='wrong'))[0], 403)
        self.assertEqual(self.request('POST', '/connect/admin/action', dict(fields, confirmation='wrong'))[0], 400)
        self.assertEqual(self.request('POST', '/connect/admin/confirm', fields)[0], 200)
        self.assertIsNotNone(self.service.registry.get(self.identity))
        self.assertEqual(self.service.operator.audit(), [])
        self.assertEqual(self.request('GET', '/connect/admin/action')[0], 404)

    def test_queue_status_reads_release_the_database_file(self):
        self.enable()
        binding = self.service.registry.get(self.identity)
        queue = self.service.queue(binding)
        queue.put('saved', 'issues', {})
        self.assertEqual(self.request()[0], 200)
        moved = queue.path.with_suffix('.verify-close')
        queue.path.rename(moved)
        moved.rename(queue.path)

    def test_pause_accepts_webhooks_preserves_queue_and_resumes_without_reenrollment(self):
        self.enable()
        self.request()
        binding = self.service.registry.get(self.identity)
        queue = self.service.queue(binding)
        payload = {'repository': {'id': 11, 'full_name': 'one/repo'}, 'installation': {'id': 101}, 'issue': {'number': 1}}
        queue.put('first', 'issues', payload)
        old_fields = self.fields('pause')
        self.assertEqual(self.request('POST', '/connect/admin/action', old_fields)[0], 303)
        self.assertIsNone(self.service.registry.get(self.identity))
        self.assertEqual(self.service.registry.receive('app', 'second', 'issues', payload), 202)
        self.service.dispatch()
        with queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE done=0').fetchone()[0], 2)
        self.assertFalse(self.service.process(binding, Mock(), Mock()))
        with self.assertRaisesRegex(ValueError, 'paused'):
            self.service.registry.connect(11, 'one/repo', 101, config(1000), '1', '1')
        self.assertEqual(self.request('POST', '/connect/admin/action', old_fields)[0], 409)
        self.assertEqual(self.request('POST', '/connect/admin/action', self.fields('resume'))[0], 303)
        self.assertEqual(self.service.registry.get(self.identity), binding)
        self.assertEqual([r[2] for r in self.service.operator.audit()], ['resume', 'pause'])

    def test_disconnect_is_soft_and_does_not_delete_discord_channels(self):
        self.enable()
        self.request()
        binding = self.service.registry.get(self.identity)
        queue = self.service.queue(binding)
        queue.put('saved', 'issues', {'safe': 'test'})
        self.assertEqual(self.request('POST', '/connect/admin/action', self.fields('disconnect'))[0], 303)
        self.assertIsNone(self.service.registry.get(self.identity))
        self.assertIsNotNone(queue.next())
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(self.service.operator.rows()[0]['enabled'], 0)

    def test_auth_is_checked_again_before_each_change(self):
        self.enable()
        self.request()
        self.assertEqual(self.request('POST', '/connect/admin/action', self.fields('pause'), claims=dict(self.claims, email='other@example.com'))[0], 403)
        self.assertIsNotNone(self.service.registry.get(self.identity))

    def test_english_admin_page_keeps_language_switch_on_the_admin_route(self):
        self.enable()
        result = self.request(path='/connect/admin?lang=en')
        self.assertEqual(result[0], 200)
        self.assertIn('Threadle administration', result[2])
        self.assertIn('Discord server ID 1000', result[2])
        self.assertIn('/connect/admin?lang=ko', result[2])
        self.assertNotIn('일시중지', result[2])

    def test_jwks_destination_comes_only_from_validated_operator_configuration(self):
        for domain in ('http://localhost', 'example.com/path', 'example.com', 'example.cloudflareaccess.com.evil.test'):
            with self.assertRaises(ValueError):
                AccessIdentity({'team_domain': domain, 'audience': 'a' * 64})

    def test_delivery_metrics_display_is_readonly_and_bilingual(self):
        self.enable();binding=self.service.registry.get(self.identity);queue=self.service.queue(binding)
        queue.put('measured','issue_comment',{},received=100)
        with queue.connect() as db:db.execute('UPDATE delivery_latency SET started=100.25,first_discord=101.25,completed=102')
        result=self.request();self.assertEqual(result[0],200);self.assertIn('1.25초',result[2]);self.assertIn('P95',result[2])
        result=self.request(path='/connect/admin?lang=en');self.assertIn('First Discord write: median 1.25s',result[2])
        with queue.connect() as db:self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0],0)

    def test_site_changelog_requires_admin_identity_and_separates_history(self):
        self.assertEqual(self.request(path='/connect/admin/changelog',signed=False)[0],403)
        self.enable()
        status,headers,body=self.request(path='/connect/admin/changelog')
        self.assertEqual(status,200)
        self.assertIn('사이트·관리자 패치노트',body)
        self.assertIn('v1.0.0-beta.16',body)
        self.assertEqual(headers.get('X-Robots-Tag'),'noindex, nofollow')
        from changelog import content
        public=content(lambda ko,en:ko)
        self.assertNotIn('v1.0.0-beta.16',public)
        self.assertNotIn('모바일 CI 예시',public)
        self.assertIn('v1.0.0-beta.15',public)

    def test_server_plan_and_effective_settings_without_manual_update_ui(self):
        self.enable()
        binding=self.service.registry.get(self.identity)
        settings=dict(binding.config,language='en',team_notifications={'digest':'weekly','digest_hour':17,'quiet':True,'quiet_start':23,'quiet_end':7},secret='NEVER_RENDER')
        self.service.registry.configure(self.identity,settings,'operator','test')
        with self.service.registry.db() as db:
            db.execute("INSERT INTO guild_subscriptions VALUES(?, 'pro','active',NULL,?)",('1000',time.time()))
        status,_,body=self.request()
        self.assertEqual(status,200)
        for text in ('Pro','English (EN)','17:00','23:00–07:00','/settings'):self.assertIn(text,body)
        for text in ('NEVER_RENDER','update-package','/update/upload','service-update'):self.assertNotIn(text,body)
        from guild_plans import subscription,allows
        self.assertEqual(subscription(self.service.registry,'1000')['plan'],'pro')
        self.assertEqual(subscription(self.service.registry,'2000')['plan'],'free')
        self.assertTrue(allows(self.service.registry,'1000','notifications'))
        with self.assertRaises(ValueError):allows(self.service.registry,'1000','unregistered')
        with self.service.registry.db() as db:db.execute("UPDATE guild_subscriptions SET expires_at=1")
        self.assertEqual(subscription(self.service.registry,'1000')['plan'],'free')
        self.assertEqual(subscription(self.service.registry,'1000')['status'],'expired')

    def test_manual_refresh_requires_auth_origin_csrf_and_get_never_queries(self):
        from unittest.mock import patch
        self.enable()
        usage=self.service.api_usage
        with patch.object(usage,'refresh',return_value=True) as refresh:
            self.assertEqual(self.request()[0],200)
            self.assertEqual(self.request()[0],200)
            refresh.assert_not_called()
            path='/connect/admin/api-usage/refresh'
            fields={'csrf':self.web.sessions.get(self.cookie).data['csrf']}
            self.assertEqual(self.request('POST',path,fields,signed=False)[0],403)
            self.assertEqual(self.request('POST',path,{'csrf':'bad'})[0],403)
            self.assertEqual(self.request('POST',path,fields,headers={'Origin':'https://bad.example'})[0],403)
            self.assertEqual(self.request('GET',path)[0],404)
            refresh.assert_not_called()
            result=self.request('POST',path,fields)
            self.assertEqual(result[0],303)
            self.assertEqual(result[1]['Location'],'/connect/admin#api-usage')
            refresh.assert_called_once_with(self.service)
