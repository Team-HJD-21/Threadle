import hashlib
import hmac
import json
from pathlib import Path
import tempfile
import http.client
import threading
import shutil
import subprocess
from http.server import ThreadingHTTPServer
import unittest
from unittest.mock import Mock

from service import Service, handler
from registry import Binding
from auth import InstallationTokens, RepositoryApi
from discord_threads import ApiError


def config(guild):
    return {'guild_id': str(guild), 'channels': {'issue': str(guild + 1), 'pr': str(guild + 2),
                                              'discussion': str(guild + 3)}, 'alert_channel': str(guild + 4)}


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.temp.name)
        self.service = Service(self.root, {'WEBHOOK_SECRET': 'legacy', 'GITHUB_APP_WEBHOOK_SECRET': 'app',
                                          'DISCORD_BOT_TOKEN': 'not-real', 'GITHUB_TOKEN': 'legacy-token'})
        self.a = self.service.registry.add('team-one', 11, 'one/repo', 101, 'app', config(1000))
        self.b = self.service.registry.add('team-two', 22, 'two/repo', 202, 'app', config(2000))

    def tearDown(self):
        self.temp.cleanup()

    def send(self, repo=11, installation=101, event='issue_comment', extra=None, delivery='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', mode='app', secret=None):
        name = 'one/repo' if repo == 11 else 'two/repo'
        payload = {'repository': {'id': repo, 'full_name': name}, 'installation': {'id': installation},
                   'issue': {'number': 7, 'title': 'Title'}, 'comment': {'body': 'Body'}, 'action': 'created'}
        payload.update(extra or {})
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new((secret or mode).encode(), body, hashlib.sha256).hexdigest()
        return self.service.receive('/github/' + ('app-webhook' if mode == 'app' else 'webhook'), signature, event, delivery, body)

    def test_two_teams_use_isolated_queues_and_only_expected_destination(self):
        self.assertEqual(self.send(), 202)
        self.service.dispatch()
        a = self.service.registry.get(self.a)
        b = self.service.registry.get(self.b)
        self.assertIsNotNone(self.service.queue(a).next())
        self.assertIsNone(self.service.queue(b).next())
        self.assertNotEqual(self.service.queue(a).path, self.service.queue(b).path)
        threads, feed, github = Mock(), Mock(), Mock()
        github.call.return_value = {'id': 11}
        self.service.github = lambda _: github
        threads.deliver.return_value = 'thread-one'
        self.service.process(a, threads, feed)
        self.assertEqual(threads.deliver.call_args.args[0].repo, 'one/repo')
        self.assertEqual(feed.topic.call_args.args[-1], 'thread-one')
        self.assertEqual(feed.topic.call_count, 1)

    def test_wrong_installation_unknown_repository_and_wrong_secret_rejected(self):
        self.assertEqual(self.send(installation=202), 403)
        self.assertEqual(self.send(repo=999), 403)
        self.assertEqual(self.send(secret='incorrect'), 403)
        self.assertEqual(self.service.registry.pending(), [])

    def test_legacy_secret_cannot_authorize_app_connection(self):
        self.assertEqual(self.send(mode='legacy'), 403)
        identity = self.service.registry.add('existing', 33, 'existing/repo', 0, 'legacy', config(3000), True)
        self.assertEqual(self.send(repo=33, mode='legacy', extra={'repository': {'id': 33, 'full_name': 'existing/repo'}}), 202)
        self.service.dispatch()
        self.assertEqual(self.service.queue(self.service.registry.get(identity)).path, self.root / 'queue.sqlite3')

    def test_one_repository_may_fan_out_to_two_servers_without_shared_state(self):
        second = self.service.registry.add('another', 11, 'one/repo', 101, 'app', config(4000))
        self.send()
        self.service.dispatch()
        for identity in (self.a, second):
            self.assertIsNotNone(self.service.queue(self.service.registry.get(identity)).next())
        self.assertIsNone(self.service.queue(self.service.registry.get(self.b)).next())

    def test_inbox_restart_and_redelivery_do_not_duplicate_jobs(self):
        self.send()
        restarted = Service(self.root, self.service.env)
        restarted.dispatch()
        self.service.dispatch()
        self.send()
        self.service.dispatch()
        with self.service.queue(self.service.registry.get(self.a)).connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_disabling_before_dispatch_prevents_delivery(self):
        self.send()
        self.service.registry.disable(self.a)
        self.service.dispatch()
        self.assertIsNone(self.service.registry.get(self.a))
        self.assertEqual(self.service.registry.pending(), [])

    def test_installation_revocation_and_repository_removal_are_scoped(self):
        self.assertEqual(self.send(event='installation', extra={'action': 'suspend'}), 200)
        self.assertIsNone(self.service.registry.get(self.a))
        self.assertIsNotNone(self.service.registry.get(self.b))
        self.assertEqual(self.send(event='installation', extra={'action': 'unsuspend'}), 200)
        self.assertIsNotNone(self.service.registry.get(self.a))
        self.assertEqual(self.send(event='installation_repositories', extra={'action': 'removed', 'repositories_removed': [{'id': 11}]}), 200)
        self.assertIsNone(self.service.registry.get(self.a))

    def test_retry_of_team_one_does_not_block_team_two(self):
        self.send()
        self.send(repo=22, installation=202)
        self.service.dispatch()
        a, b = self.service.registry.get(self.a), self.service.registry.get(self.b)
        github = Mock()
        github.call.return_value = {'id': 11}
        self.service.github = lambda _: github
        failed = Mock()
        failed.deliver.side_effect = RuntimeError('Discord unavailable')
        self.service.process(a, failed, None)
        github.call.return_value = {'id': 22}
        successful = Mock()
        self.service.process(b, successful, None)
        self.assertIsNone(self.service.queue(b).next())
        with self.service.queue(a).connect() as db:
            self.assertEqual(db.execute('SELECT done,attempts FROM jobs').fetchone(), (0, 1))

    def test_repository_rename_keeps_stable_binding_id(self):
        self.assertEqual(self.send(extra={'repository': {'id': 11, 'full_name': 'renamed/repository'}}), 202)
        self.service.dispatch()
        github = Mock()
        github.call.return_value = {'id': 11, 'full_name': 'renamed/repository'}
        self.service.github = lambda _: github
        self.service.process(self.service.registry.get(self.a), Mock(), None)
        self.assertEqual(self.service.registry.get(self.a).repo, 'renamed/repository')
        self.send(delivery='bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.assertEqual(self.service.registry.get(self.a).repo, 'renamed/repository')

    def test_channel_configuration_must_have_all_topic_types(self):
        with self.assertRaises(ValueError):
            self.service.registry.add('bad', 99, 'bad/repo', 909, 'app', {'guild_id': '1', 'channels': {}})

    def test_legacy_queue_and_thread_cache_survive_registration(self):
        from delivery import Queue
        existing = Queue(self.root / 'queue.sqlite3')
        existing.put('old-delivery', 'issues', {'existing': True})
        with existing.connect() as db:
            db.execute('INSERT INTO links VALUES(?,?,?)', ('existing-topic', '3001', 'old-thread'))
        identity = self.service.registry.add('existing', 33, 'existing/repo', 0, 'legacy', config(3000), True)
        migrated = self.service.queue(self.service.registry.get(identity))
        self.assertEqual(migrated.next()[0], 'old-delivery')
        with migrated.connect() as db:
            self.assertEqual(db.execute('SELECT thread FROM links').fetchone()[0], 'old-thread')

    def test_missing_app_installation_cannot_disable_legacy_connection(self):
        identity = self.service.registry.add('existing', 33, 'existing/repo', 0, 'legacy', config(3000), True)
        self.assertEqual(self.send(installation=0, event='installation', extra={'action': 'deleted'}), 403)
        self.assertIsNotNone(self.service.registry.get(identity))

    def test_disabled_legacy_connection_can_be_replaced_without_replaying_its_queue(self):
        registry = self.service.registry
        identity = registry.add('existing', 33, 'existing/repo', 0, 'legacy', config(3000), True)
        old_queue = self.service.queue(registry.get(identity))
        old_queue.put('old-pending', 'issues', {'old': True})
        with self.assertRaisesRegex(ValueError, '기존 운영 연결'):
            registry.connect(33, 'existing/repo', 303, config(3000), 'discord-user', 'github-user')
        registry.disable(identity)
        replacement = registry.connect(33, 'existing/repo', 303, config(3000), 'discord-user', 'github-user')
        current = registry.get(replacement)
        self.assertNotEqual(replacement, identity)
        self.assertEqual((current.auth, current.installation, current.legacy_state), ('app', 303, False))
        self.assertIsNone(self.service.queue(current).next())
        self.assertEqual(old_queue.next()[0], 'old-pending')
        with registry.db() as db:
            archived = json.loads(db.execute('SELECT data FROM retired_bindings WHERE id=?', (identity,)).fetchone()[0])
        self.assertEqual((archived['auth'], archived['enabled']), ('legacy', 0))
        self.assertIsNotNone(registry.get(self.a))

    def test_signed_http_ingress_routes_to_installation_without_actions(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.service))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        body = json.dumps({'repository': {'id': 11, 'full_name': 'one/repo'},
                           'installation': {'id': 101}, 'issue': {'number': 1}}).encode()
        headers = {'X-GitHub-Event': 'issues', 'X-GitHub-Delivery': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                   'X-Hub-Signature-256': 'sha256=' + hmac.new(b'app', body, hashlib.sha256).hexdigest()}
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        try:
            connection.request('POST', '/github/app-webhook', body, headers)
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 202)
            self.service.dispatch()
            self.assertIsNotNone(self.service.queue(self.service.registry.get(self.a)).next())
            self.assertIsNone(self.service.queue(self.service.registry.get(self.b)).next())
        finally:
            connection.close()
            server.shutdown()
            server.server_close()
            thread.join()

    @unittest.skipUnless(shutil.which('powershell.exe'), 'Windows PowerShell is required')
    def test_windows_powershell_recognizes_health_as_ready(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.service))
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        try:
            command = (f"$r=Invoke-WebRequest 'http://127.0.0.1:{server.server_port}/health' "
                       "-UseBasicParsing -TimeoutSec 3; "
                       "if ($r.StatusCode -ne 200 -or $r.Content -isnot [string] "
                       "-or $r.Content -ne 'receiver-ready') { exit 1 }; 'ready'")
            result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', command],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), 'ready')
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def test_queued_event_is_not_sent_after_installation_deleted(self):
        self.send()
        self.service.dispatch()
        old_binding = self.service.registry.get(self.a)
        self.send(event='installation', extra={'action': 'deleted'})
        threads = Mock()
        self.assertFalse(self.service.process(old_binding, threads, None))
        threads.deliver.assert_not_called()

    def test_workflow_start_and_completion_are_durably_queued(self):
        for index, action in enumerate(('requested', 'in_progress', 'completed')):
            self.assertEqual(self.send(event='workflow_run', delivery=f'aaaaaaaa-bbbb-cccc-dddd-{index:012d}',
                extra={'action':action, 'workflow_run':{'id':90, 'status':action}}), 202)
        self.service.dispatch()
        with self.service.queue(self.service.registry.get(self.a)).connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM jobs WHERE event='workflow_run'").fetchone()[0], 3)

    def test_configured_ci_workflow_filter_preserves_existing_team_behavior(self):
        with self.service.registry.db() as db:
            settings = config(1000)
            settings['workflow_names'] = ['PR CI']
            db.execute('UPDATE bindings SET config=? WHERE id=?', (json.dumps(settings), self.a))
        self.send(event='workflow_run', extra={'action': 'completed', 'workflow_run': {'name': 'Unrelated'}})
        self.service.dispatch()
        threads, feed = Mock(), Mock()
        self.service.process(self.service.registry.get(self.a), threads, feed)
        threads.deliver.assert_not_called()
        feed.ci.assert_not_called()


class AuthTests(unittest.TestCase):
    def test_repository_api_rejects_other_repository_and_graphql_scope(self):
        b = Binding('id', 'team', 1, 'owner/repo', 12, 'app', config(1000), False)
        client = RepositoryApi(b, Mock(), api_factory=Mock())
        with self.assertRaises(ValueError):
            client.call('GET', '/repos/other/repo/issues')
        with self.assertRaises(ValueError):
            client.call('POST', '/graphql', {'variables': {'owner': 'other', 'name': 'repo'}})

    def test_expired_installation_token_is_refreshed_and_scoped_per_repository(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            key = Path(root) / 'test.pem'
            key.write_text('test-key')
            clock = Mock(return_value=1000)
            api = Mock()
            api.call.return_value = {'token': 'temporary-token', 'expires_at': '1970-01-01T01:00:00Z'}
            signer = Mock(return_value='signed-jwt')
            tokens = InstallationTokens('app-id', key, api_factory=Mock(return_value=api), clock=clock, signer=signer)
            tokens.token(12, 100)
            tokens.token(12, 100)
            self.assertEqual(api.call.call_count, 1)
            tokens.token(12, 200)
            self.assertEqual(api.call.call_args.args[2], {'repository_ids': [200]})
            clock.return_value = 3590
            tokens.token(12, 100)
            self.assertEqual(api.call.call_count, 3)
            self.assertEqual(signer.call_args.kwargs['algorithm'], 'RS256')
            self.assertEqual(signer.call_args.args[0]['iat'], 3530)

    def test_real_rs256_signature_and_claims(self):
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            path = Path(root) / 'generated.pem'
            path.write_bytes(private.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            api, factory = Mock(), Mock()
            factory.return_value = api
            api.call.return_value = {'token': 'not-a-real-installation-token', 'expires_at': '2100-01-01T00:00:00Z'}
            tokens = InstallationTokens('application-id', path, api_factory=factory)
            tokens.token(123, 456)
            signed = factory.call_args.args[2]
            claims = jwt.decode(signed, private.public_key(), algorithms=['RS256'], issuer='application-id')
            self.assertLessEqual(claims['exp'] - claims['iat'], 600)
            self.assertEqual(api.call.call_args.args[2], {'repository_ids': [456]})

    def test_401_refreshes_app_token_once(self):
        binding = Binding('id', 'team', 1, 'owner/repo', 12, 'app', config(1000), False)
        tokens = Mock()
        tokens.token.side_effect = ['expired', 'fresh']
        first, second = Mock(), Mock()
        first.call.side_effect = ApiError('GitHub', 401)
        second.call.return_value = {'id': 1}
        factory = Mock(side_effect=[first, second])
        client = RepositoryApi(binding, tokens, api_factory=factory)
        self.assertEqual(client.call('GET', '/repositories/1'), {'id': 1})
        tokens.invalidate.assert_called_once_with(12, 1)


if __name__ == '__main__':
    unittest.main()
