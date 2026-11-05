from unittest import TestCase
from unittest.mock import patch
import test_accounts as fixtures


class ChatEventTests(TestCase):
    discord_call=fixtures.AccountTests.discord_call
    github_call=fixtures.AccountTests.github_call

    def setUp(self):
        fixtures.AccountTests.setUp(self)
        self.events=self.service.chat_events
        self.events.clock=self.clock

    def rows(self):
        with self.accounts.db() as db:return db.execute('SELECT * FROM chat_events').fetchall()

    def test_only_managed_channels_and_threads_are_logged_without_content(self):
        self.assertFalse(self.events.record('message-create','1000','9999','8000',actor='55'))
        self.assertFalse(self.events.record('message-create','2000','7777','8000',actor='55'))
        self.assertTrue(self.events.record('message-create','1000','7777','8000',author='55',actor='55'))
        self.assertTrue(self.events.record('message-create','1000','8888','8001',parent='1002',actor='55'))
        text=str(self.rows());self.assertNotIn('Hello from Discord',text)
        self.assertNotIn('access-secret',text);self.assertEqual(len(self.rows()),2)

    def test_event_cache_does_not_scan_repositories_per_message(self):
        with patch.object(self.service.registry,'active',wraps=self.service.registry.active) as active:
            for n in range(10):self.events.record('message-edit','1000','7777',str(8000+n))
            self.assertEqual(active.call_count,1)

    def test_audit_actor_evidence_is_separate_from_unattributed_message_deletion(self):
        self.events.record('message-delete','1000','7777','8000',author='55')
        self.events.audit({'id':'9001','guild':'1000','channel':'7777','actor':'66','target':'55','action':72,'count':2})
        with self.accounts.db() as db:
            actor=db.execute('SELECT actor FROM chat_events').fetchone()[0]
            evidence=db.execute('SELECT actor,target,count FROM chat_audit_events').fetchone()
        self.assertIsNone(actor);self.assertEqual(tuple(evidence),('66','55',2))

    def test_uncached_delete_recovers_author_but_never_guesses_deleting_actor(self):
        self.events.record('message-create','1000','7777','8000',author='55',actor='55')
        self.events.record('message-delete','1000','7777','8000')
        with self.accounts.db() as db:
            row=db.execute("SELECT author,actor FROM chat_events WHERE kind='message-delete'").fetchone()
        self.assertEqual(tuple(row),('55',None))

    def test_bot_intent_must_persist_before_delete_and_failure_is_distinguished(self):
        self.events.before_api('DELETE','/channels/7777/messages/8000')
        self.assertTrue(self.events.own_delete('7777','8000'))
        self.events.failed_api('DELETE','/channels/7777/messages/8000',403)
        self.assertFalse(self.events.own_delete('7777','8000'))
        self.events.before_api('DELETE','/channels/7777/messages/8000')
        self.events.after_api('DELETE','/channels/7777/messages/8000',None)
        self.assertTrue(self.events.own_delete('7777','8000'))

    def test_real_transport_writes_intent_before_lost_delete_response(self):
        from discord_threads import Api,ApiError
        from urllib.error import URLError
        api=Api('Discord','https://discord.com/api/v10','bot','Bot',sleep=lambda _:None)
        api.chat_events=self.events
        def lost(*args,**kwargs):
            self.assertTrue(self.events.own_delete('7777','8000'))
            raise URLError('lost response')
        with patch('discord_threads.urlopen',side_effect=lost),self.assertRaises(ApiError):
            api.call('DELETE','/channels/7777/messages/8000')
        self.assertTrue(self.events.own_delete('7777','8000'))

    def test_gateway_captures_uncached_deletes_bulk_and_metadata_without_polling(self):
        import asyncio,threading
        from types import SimpleNamespace as NS
        from presence import Presence,DEFAULT_TEXT
        from test_presence import FakeClient
        stop=threading.Event();clients=[]
        class EventsClient(FakeClient):
            async def start(client,token,reconnect):
                client.user=NS(id=9000)
                await client.handlers['on_ready']()
                await client.handlers['on_raw_message_edit'](NS(guild_id=1000,channel_id=7777,message_id=8000,
                    data={'author':{'id':'55'},'content':'SECRET CONTENT'}))
                await client.handlers['on_raw_message_delete'](NS(guild_id=1000,channel_id=7777,message_id=8000))
                await client.handlers['on_raw_bulk_message_delete'](NS(guild_id=1000,channel_id=7777,message_ids={8001,8002}))
                await client.handlers['on_raw_thread_update'](NS(guild_id=1000,thread_id=7777,data={'parent_id':'1002','name':'PRIVATE TITLE'}))
                stop.set()
                await asyncio.Event().wait()
        def create(**kwargs):
            client=EventsClient(stop,kwargs);clients.append(client);return client
        module=NS(Client=create,Intents=NS(none=lambda:NS()),CustomActivity=lambda name:None,Status=NS(online='online'))
        asyncio.run(Presence(self.service).session(stop,module,DEFAULT_TEXT,'online'))
        with self.accounts.db() as db:
            kinds=[row[0] for row in db.execute('SELECT kind FROM chat_events')]
        self.assertEqual(kinds.count('message-delete'),3)
        self.assertIn('thread-update',kinds);self.assertIn('message-edit',kinds)
        self.assertNotIn('SECRET',str(self.rows()));self.assertNotIn('PRIVATE TITLE',str(self.rows()))
        self.assertTrue(clients[0].kwargs['intents'].moderation)

    def test_old_events_and_intents_expire_without_content_polling(self):
        self.events.record('message-delete','1000','7777','8000')
        self.events.intent('7777','8000')
        self.clock.return_value+=31*86400;self.events.prune()
        self.assertEqual(self.rows(),[]);self.assertFalse(self.events.own_delete('7777','8000'))

    def test_transport_success_logs_own_actor_without_message_content(self):
        self.events.bot_id='9000'
        self.events.after_api('POST','/channels/1002/messages',{'id':'8000','content':'SECRET'})
        self.events.after_api('PATCH','/channels/1002/messages/8000',{'id':'8000','content':'SECRET'})
        with self.accounts.db() as db:rows=db.execute('SELECT kind,actor,source FROM chat_events').fetchall()
        self.assertEqual([tuple(row) for row in rows],[('message-create','9000','threadle'),('message-edit','9000','threadle')])
        self.assertNotIn('SECRET',str(self.rows()))
