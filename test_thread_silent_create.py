import unittest
from unittest.mock import Mock
from discord_threads import ApiError,Threads,Topic

class SilentCreateTests(unittest.TestCase):
    def setUp(self):
        self.api=Mock();self.threads=Threads(self.api,'g',{'pr':'c'})
        self.topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1')
    def test_dnd_creation_requests_silent_without_changing_thread_shape(self):
        self.threads.silent_notifications=True;self.threads.start('c','100',self.topic)
        data=self.api.call.call_args.args[2]
        self.assertEqual(data,{'name':self.topic.name,'auto_archive_duration':10080,'flags':4096})
    def test_normal_creation_is_unchanged(self):
        self.threads.start('c','100',self.topic)
        self.assertNotIn('flags',self.api.call.call_args.args[2])
    def test_rejected_flag_falls_back_once_to_the_same_source_message(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,50035),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        calls=self.api.call.call_args_list
        self.assertEqual(len(calls),2);self.assertEqual(calls[0].args[1],calls[1].args[1])
        self.assertIn('flags',calls[0].args[2]);self.assertNotIn('flags',calls[1].args[2])
    def test_duplicate_existing_thread_is_recovered_without_fallback(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,160004),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        self.assertEqual([c.args[0] for c in self.api.call.call_args_list],['POST','GET'])
    def test_duplicate_after_rejected_flag_still_recovers(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,50035),ApiError('Discord',400,160004),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        self.assertEqual([c.args[0] for c in self.api.call.call_args_list],['POST','POST','GET'])
    def test_permission_and_uncertain_network_errors_are_not_retried_without_flags(self):
        self.threads.silent_notifications=True
        for status,code in [(403,50013),('network',None),(400,160006)]:
            self.api.reset_mock();self.api.call.side_effect=ApiError('Discord',status,code)
            with self.assertRaises(ApiError):self.threads.start('c','100',self.topic)
            self.assertEqual(self.api.call.call_count,1)


class StarterPinTests(unittest.TestCase):
    def test_pins_original_cards_and_preserves_manual_pins_on_close(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import call
        from delivery import CachedThreads, Queue
        from open_topic_board import refresh

        with tempfile.TemporaryDirectory() as directory:
            queue = Queue(Path(directory) / 'queue.sqlite3')
            api = Mock()
            threads = CachedThreads(api, 'guild', {'pr': 'channel'}, queue)
            threads.bot = {'id': 'bot'}
            def discord(method, path):
                if path.endswith('?limit=100'):
                    return [
                        {'id': 'notice', 'type': 6, 'author': {'id': 'bot'}, 'message_reference': {'message_id': '1'}},
                        {'id': 'manual', 'type': 6, 'author': {'id': 'user'}, 'message_reference': {'message_id': '1'}},
                        {'id': 'unrelated', 'type': 6, 'author': {'id': 'bot'}, 'message_reference': {'message_id': '5'}},
                        {'id': 'regular', 'type': 0, 'author': {'id': 'bot'}, 'message_reference': {'message_id': '1'}},
                    ]
                number = path.rsplit('/', 1)[-1]
                return {'author': {'id': 'bot'}, 'pinned': number == '5',
                        'embeds': [{'footer': {'text': f'github-thread:owner/repo:pr:{number}'}}]}
            api.call.side_effect = discord
            with queue.connect() as db:
                db.execute('CREATE TABLE topic_thread_names(topic TEXT PRIMARY KEY,name TEXT NOT NULL)')
                db.execute('CREATE TABLE open_topic_boards(channel TEXT,kind TEXT,message TEXT)')
                db.execute("INSERT INTO open_topic_boards VALUES('channel','pr','board')")
                for number, state in ((1, 'Open'), (2, 'Merged'), (3, 'Draft'), (4, 'Open'), (5, 'Open')):
                    key = f'github-thread:owner/repo:pr:{number}'
                    db.execute('INSERT INTO links VALUES(?,?,?)', (key, 'other' if number == 4 else 'channel', str(number)))
                    db.execute('INSERT INTO topic_thread_names VALUES(?,?)', (key, f'[PR #{number} · {state}] repo · Example'))
            refresh(threads, 'channel', 'pr')
            self.assertEqual(api.call.call_args_list, [
                call('DELETE', '/channels/channel/messages/pins/board'),
                call('GET', '/channels/channel/messages/1'),
                call('PUT', '/channels/channel/messages/pins/1'),
                call('GET', '/channels/channel/messages/5'),
                call('GET', '/channels/channel/messages?limit=100'),
                call('DELETE', '/channels/channel/messages/notice'),
            ])
            api.call.reset_mock()
            refresh(threads, 'channel', 'pr')
            api.call.assert_not_called()
            with queue.connect() as db:
                db.execute("UPDATE topic_thread_names SET name=replace(name,'Open','Closed') WHERE topic LIKE '%:1' OR topic LIKE '%:5'")
            refresh(threads, 'channel', 'pr')
            api.call.assert_called_once_with('DELETE', '/channels/channel/messages/pins/1')
