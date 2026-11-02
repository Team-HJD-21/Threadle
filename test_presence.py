import asyncio
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from presence import Presence, settings, DEFAULT_TEXT


class FakeClient:
    def __init__(self, stop, kwargs):
        self.stop, self.kwargs = stop, kwargs
        self.handlers = {}
        self.closed = False
        self.ready = False

    def event(self, callback):
        self.handlers[callback.__name__] = callback
        return callback

    def is_ready(self):
        return self.ready

    async def start(self, token, reconnect):
        self.token, self.reconnect = token, reconnect
        self.ready = True
        await self.handlers['on_ready']()
        self.stop.set()
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


class PresenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = SimpleNamespace(root=self.root, env={'DISCORD_BOT_TOKEN': 'fictional-token'})
        self.presence = Presence(self.service)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_settings_and_invalid_values(self):
        self.assertEqual(settings(self.root), (True, DEFAULT_TEXT, 'online'))
        for data in ([], {'text': '\n'}, {'text': ' '}, {'text': 'x'*129}, {'status':'invisible'}, {'enabled':'true'}):
            (self.root / 'presence.json').write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                settings(self.root)

    def test_presence_uses_only_guild_intent_and_closes_on_service_stop(self):
        stop = threading.Event()
        clients = []
        def create(**kwargs):
            client = FakeClient(stop, kwargs)
            clients.append(client)
            return client
        module = SimpleNamespace(Client=create, Intents=SimpleNamespace(none=lambda: SimpleNamespace(guilds=False)),
                                 CustomActivity=lambda name: {'type':4,'state':name},
                                 Status=SimpleNamespace(online='online'))
        asyncio.run(self.presence.session(stop, module, DEFAULT_TEXT, 'online'))
        client = clients[0]
        self.assertTrue(client.kwargs['intents'].guilds)
        self.assertEqual(client.kwargs['activity'], {'type':4,'state':DEFAULT_TEXT})
        self.assertTrue(client.reconnect)
        self.assertTrue(client.closed)
        state = (self.root / 'presence-status.json').read_text()
        self.assertIn('connected', state)
        self.assertNotIn('fictional-token', state)

    def test_disabled_and_invalid_configuration_do_not_start_a_client(self):
        for data, expected in [({'enabled':False}, 'disabled'), ({'status':'invalid'}, 'invalid-settings')]:
            (self.root / 'presence.json').write_text(json.dumps(data))
            self.presence.run(threading.Event())
            self.assertEqual(json.loads((self.root / 'presence-status.json').read_text())['state'], expected)

    def test_missing_dependency_does_not_raise_or_stop_the_receiver(self):
        with patch.dict('sys.modules', {'discord':None}):
            self.presence.run(threading.Event())
        self.assertEqual(json.loads((self.root / 'presence-status.json').read_text())['state'], 'missing-dependency')

    def test_connection_failure_retries_without_logging_credentials(self):
        class LoginFailure(Exception):
            pass
        stop = threading.Event()
        with patch.dict('sys.modules', {'discord':SimpleNamespace(LoginFailure=LoginFailure)}):
            async def failing(*args):
                stop.set()
                raise OSError('connection failed')
            with patch.object(self.presence, 'session', side_effect=failing), self.assertLogs('presence', level='WARNING') as logs:
                self.presence.run(stop)
        self.assertNotIn('fictional-token', ''.join(logs.output))
        self.assertEqual(json.loads((self.root / 'presence-status.json').read_text())['state'], 'offline')

    def test_bad_credentials_stop_presence_without_retrying_forever(self):
        class LoginFailure(Exception):
            pass
        stop = threading.Event()
        timer = threading.Timer(3, stop.set)
        timer.start()
        try:
            with patch.dict('sys.modules', {'discord':SimpleNamespace(LoginFailure=LoginFailure)}):
                with patch.object(self.presence, 'session', side_effect=LoginFailure), self.assertLogs('presence', level='ERROR'):
                    self.presence.run(stop)
        finally:
            timer.cancel()
        self.assertEqual(json.loads((self.root / 'presence-status.json').read_text())['state'], 'authentication-failed')
