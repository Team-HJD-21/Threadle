import http.client
import json
import time
import unittest
from unittest.mock import Mock
from urllib.parse import urlsplit

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import serialization

from discord_connect import DiscordConnect, register_command
from test_connect import WebFixture


class DiscordEntryTests(WebFixture):
    def setUp(self):
        super().setUp()
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()
        self.endpoint = DiscordConnect(self.service, self.web.settings, public, self.web.sessions)
        self.service.discord_connect = self.endpoint

    def payload(self):
        return {'type': 2, 'id': '123456', 'application_id': '9000', 'guild_id': '10', 'channel_id': '104',
                'member': {'permissions': '32', 'user': {'id': '1'}}, 'data': {'name': 'connect'}}

    def send_command(self, payload=None, timestamp=None, tamper=False):
        body = json.dumps(payload or self.payload(), ensure_ascii=False).encode()
        timestamp = timestamp or str(int(time.time()))
        signature = self.private.sign(timestamp.encode() + body).hex()
        if tamper:
            body += b' '
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request('POST', '/discord/interactions', body, {
                'X-Signature-Ed25519': signature, 'X-Signature-Timestamp': timestamp,
                'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, response.getheader('Content-Type'), json.loads(response.read())
        finally:
            connection.close()

    def open_button(self):
        status, content_type, reply = self.send_command()
        self.assertEqual(status, 200)
        self.assertIn('application/json', content_type)
        self.assertEqual(reply['data']['flags'], 64)
        self.assertEqual(self.bot.calls, [])
        url = urlsplit(reply['data']['components'][0]['components'][0]['url'])
        result = self.browser.request('GET', url.path + '?' + url.query)
        self.assertEqual(result[0], 303)
        self.assertEqual(result[1]['Location'], '/connect')
        return url

    def test_bot_command_preserves_server_and_uses_automatic_channels_without_manual_ids(self):
        self.open_button()
        self.login('discord')
        session = self.session()
        self.assertEqual(session.data['guild'], '10')
        self.assertEqual(session.data['preferred_channel'], '104')
        self.login('github')
        self.assertEqual(self.post('/connect/repository', {'repository': '101:11'})[0], 303)
        page = self.browser.request('GET', '/connect/channels')
        self.assertEqual(page[0], 200)
        self.assertNotIn('<select', page[2])
        self.assertIn('github-issue', page[2])
        self.assertEqual(self.post('/connect/save')[0], 303)
        self.assertNotEqual(self.service.registry.active()[0].config['channels']['issue'], '104')
        self.assertEqual(len(self.bot.created_payloads), 6)

    def test_copied_button_cannot_link_another_discord_users_github_account(self):
        self.open_button()
        response = self.post('/connect/discord/start')
        from urllib.parse import parse_qs, urlencode
        state = parse_qs(urlsplit(response[1]['Location']).query)['state'][0]
        response = self.browser.request('GET', '/connect/discord/callback?' + urlencode({'state': state, 'code': '2'}))
        self.assertEqual(response[0], 403)
        self.assertNotIn('discord_token', self.session().data)
        self.assertEqual(self.service.registry.active(), [])

    def test_signed_ping_is_available_while_public_signup_is_still_disabled(self):
        self.service.web = None
        status, _, reply = self.send_command({'type': 1})
        self.assertEqual((status, reply), (200, {'type': 1}))
        self.assertEqual(self.send_command()[2]['type'], 4)

    def test_modified_body_and_expired_signatures_are_rejected(self):
        self.assertEqual(self.send_command(tamper=True)[0], 401)
        self.assertEqual(self.send_command(timestamp=str(int(time.time()) - 301))[0], 401)
        with self.web.sessions.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0], 0)

    def test_summary_and_settings_defer_without_external_calls_and_reject_non_managers(self):
        for name in ('summary', 'settings'):
            payload = self.payload()
            payload.update(id='123456' if name == 'summary' else '123457', token='private-test-token')
            payload['data'] = {'name': name, 'options': [{'name': 'type', 'value': 'pr'}] if name == 'summary' else []}
            self.assertEqual(self.send_command(payload)[2], {'type': 5, 'data': {'flags': 64}})
            self.assertEqual(self.bot.calls, [])
            self.assertEqual(self.send_command(payload, tamper=True)[0], 401)
            payload['member']['permissions'] = '0'
            self.assertIn('Manage Server', self.send_command(payload)[2]['data']['content'])

    def test_duplicate_interaction_does_not_create_multiple_tickets(self):
        first = self.send_command()[2]
        second = self.send_command()[2]
        self.assertEqual(first, second)
        with self.web.sessions.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM tickets').fetchone()[0], 1)

    def test_connection_button_is_single_use(self):
        url = self.open_button()
        self.assertEqual(self.browser.request('GET', url.path + '?' + url.query)[0], 401)

    def test_non_manager_receives_ephemeral_explanation_without_ticket(self):
        payload = self.payload()
        payload['member']['permissions'] = '2048'
        status, _, reply = self.send_command(payload)
        self.assertEqual(status, 200)
        self.assertEqual(reply['data']['flags'], 64)
        self.assertNotIn('components', reply['data'])

    def test_other_application_cannot_issue_a_connection_button(self):
        payload = self.payload()
        payload['application_id'] = '9001'
        self.assertEqual(self.send_command(payload)[0], 403)


class CommandRegistrationTests(unittest.TestCase):
    def run_registration(self, commands, result):
        from test_connect import settings
        service = Mock()
        service.discord.return_value.call.side_effect = [commands, result, None]
        register_command(service, settings())
        return service.discord.return_value.call.call_args_list

    def test_creates_english_command_before_removing_only_old_korean_command(self):
        calls = self.run_registration([{'id': '1', 'name': '연결', 'type': 1}, {'id': '2', 'name': 'help', 'type': 1}], {'id': '3'})
        self.assertEqual([c.args[0] for c in calls], ['GET', 'POST', 'DELETE'])
        definition = calls[1].args[2]
        self.assertEqual(definition['name'], 'connect')
        self.assertEqual(definition['description'], 'Connect GitHub repository notifications to this server.')
        self.assertIsNone(definition['name_localizations'])
        self.assertIsNone(definition['description_localizations'])
        self.assertEqual(calls[2].args[1], '/applications/9000/commands/1')

    def test_repeated_registration_updates_existing_command_and_clears_localizations(self):
        calls = self.run_registration([{'id': '3', 'name': 'connect', 'type': 1}], {'id': '3'})
        self.assertEqual([c.args[0] for c in calls], ['GET', 'PATCH'])
        self.assertEqual(calls[1].args[1], '/applications/9000/commands/3')
        self.assertIsNone(calls[1].args[2]['description_localizations'])

    def test_failed_creation_keeps_old_command(self):
        from test_connect import settings
        service = Mock()
        api = service.discord.return_value
        api.call.side_effect = [[{'id': '1', 'name': '연결', 'type': 1}], {}]
        with self.assertRaises(ValueError):
            register_command(service, settings())
        self.assertNotIn('DELETE', [c.args[0] for c in api.call.call_args_list])
