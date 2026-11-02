import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cryptography.fernet import Fernet
from command_jobs import CommandJobs, command_definitions, register_extra_commands
from connect_store import Sessions
from service import Service
from test_service import config


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = Service(self.root, {'DISCORD_BOT_TOKEN': 'test-only'})
        self.identity = self.service.registry.add('one', 11, 'one/repo', 101, 'app', config(1000))
        self.sessions = Sessions(self.root / 'connect.sqlite3', Fernet.generate_key().decode())
        self.jobs = CommandJobs(self.service, self.sessions)
        self.api = Mock()
        self.service.discord = lambda: self.api
        self.api.call.side_effect = self.discord
        self.github = Mock()
        self.service.github = lambda _: self.github

    def tearDown(self):
        self.temp.cleanup()

    def discord(self, method, path, *args):
        if path.endswith('/members/1'):
            return {'roles': ['77']}
        if path.endswith('/roles'):
            return [{'id': '77', 'permissions': '32'}]
        if path == '/guilds/1000':
            return {'owner_id': '2'}
        if path.startswith('/channels/'):
            return {'guild_id': '1000'}
        return {}

    def payload(self, name='settings', **options):
        return {'id': '123456', 'application_id': '9000', 'guild_id': '1000', 'channel_id': '1002',
                'token': 'private-interaction-token', 'member': {'permissions': '32', 'user': {'id': '1'}},
                'data': {'name': name, 'options': [{'name': k, 'value': v} for k, v in options.items()]}}

    def test_settings_preserve_channels_and_validate_quiet_defaults(self):
        before = self.service.registry.get(self.identity)
        result = self.jobs.execute(self.payload(quiet=True, review_hours=24))
        after = self.service.registry.get(self.identity)
        self.assertIn('updated', result)
        self.assertEqual(before.config['channels'], after.config['channels'])
        self.assertEqual(after.config['team_notifications']['quiet_start'], 22)
        self.assertGreater(after.config['team_notifications']['review_since'], 0)
        with self.assertRaises(ValueError):
            self.jobs.execute(self.payload(quiet_start=8, quiet_end=8))
        self.assertEqual(self.service.registry.get(self.identity), after)

    def test_permission_revocation_and_other_guild_repository_cannot_modify(self):
        before = self.service.registry.get(self.identity)
        self.api.call.side_effect = lambda method, path, *args: [] if path.endswith('/roles') else {'owner_id': '2', 'roles': []}
        with self.assertRaisesRegex(ValueError, 'no longer'):
            self.jobs.execute(self.payload(review_hours=24))
        self.assertEqual(self.service.registry.get(self.identity), before)
        self.api.call.side_effect = self.discord
        with self.assertRaisesRegex(ValueError, 'Specify repository'):
            self.jobs.execute(self.payload(repository='other/repo', review_hours=24))

    def test_durable_encrypted_job_retries_response_without_repeating_mutation(self):
        payload = self.payload(review_hours=24)
        self.assertIsNone(self.jobs.enqueue(payload))
        self.assertIsNone(self.jobs.enqueue(payload))
        with self.sessions.db() as db:
            rows = db.execute('SELECT payload FROM command_jobs').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(payload['token'].encode(), rows[0][0])
        execute = Mock(wraps=self.jobs.execute)
        self.jobs.execute = execute
        def fail_response(method, path, *args):
            if method == 'PATCH':
                raise RuntimeError('response lost')
            return self.discord(method, path, *args)
        self.api.call.side_effect = fail_response
        with self.assertRaises(RuntimeError):
            self.jobs.process()
        self.api.call.side_effect = self.discord
        restarted = CommandJobs(self.service, self.sessions)
        restarted.execute = execute
        restarted.process()
        self.assertEqual(execute.call_count, 1)
        with self.sessions.db() as db:
            self.assertEqual(db.execute('SELECT done,payload,result FROM command_jobs').fetchone(), (1, None, None))

    def test_expired_job_does_not_execute(self):
        self.jobs.enqueue(self.payload())
        with self.sessions.db() as db:
            db.execute('UPDATE command_jobs SET expires=?', (time.time() - 1,))
        self.jobs.execute = Mock()
        self.jobs.process()
        self.jobs.execute.assert_not_called()

    def test_summary_counts_open_items_and_does_not_modify_configuration(self):
        binding = self.service.registry.get(self.identity)
        def query(method,path,body):
            field=next(name for name in ['pullRequests','issues','discussions'] if name+'(' in body['query'])
            return {'data':{'repository':{field:{'totalCount':7,'nodes':[{'number':n,'title':field+' item '+str(n),'url':'unused','closed':False} for n in range(1,8)],'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
        self.github.call.side_effect=query
        result='\n'.join(self.jobs.execute(self.payload('summary')))
        self.assertIn('PR · 7',result)
        self.assertIn('Issue · 7',result)
        self.assertIn('Discussion · 7',result)
        self.assertIn('https://github.com/one/repo/pull/7',result)
        self.assertEqual(self.service.registry.get(self.identity),binding)

    def test_registration_preserves_other_commands_and_uses_english(self):
        api = Mock()
        api.call.side_effect = [[{'id': '12', 'name': 'summary', 'type': 1}, {'id': '13', 'name': 'unrelated', 'type': 1}], {'id': '12'}, {'id': '14'}]
        register_extra_commands(SimpleNamespace(discord=lambda: api), SimpleNamespace(discord_id='9000'))
        self.assertEqual([c.args[0] for c in api.call.call_args_list], ['GET', 'PATCH', 'POST'])
        self.assertTrue(all(c['description'].isascii() for c in command_definitions()))
        self.assertTrue(all(c['default_member_permissions'] == '32' for c in command_definitions()))
