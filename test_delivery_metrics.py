from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from delivery import Queue
from delivery_metrics import begin,end,discord_write,deferred,summary
from discord_threads import Api
from service import Service
from test_service import config

class MetricTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'queue.db';self.q=Queue(self.path)
        self.q.put('one','issue_comment',{},received=100)
    def tearDown(self):self.temp.cleanup()
    def test_measures_acknowledged_first_write_not_subsequent_edits(self):
        with patch('delivery_metrics.time.time',return_value=101):token=begin(self.q,['one'])
        api=Api('Discord','https://discord.com/api/v10','test','Bot')
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'{"id":"456"}';response.headers={}
        with patch('discord_threads.urlopen',return_value=response),patch('delivery_metrics.time.time',return_value=103):api.call('POST','/channels/123/messages',{'content':'hello'})
        with patch('delivery_metrics.time.time',return_value=108):discord_write('PATCH','/channels/123/messages/456')
        self.q.finish('one')
        with patch('delivery_metrics.time.time',return_value=110):end(token)
        data=summary(self.path);self.assertEqual(data['count'],1);self.assertEqual(data['p50'],3);self.assertEqual(data['queue_p50'],1);self.assertEqual(data['processing_p50'],2)
    def test_deferred_and_no_write_events_are_not_speed_samples(self):
        with patch('delivery_metrics.time.time',return_value=101):token=begin(self.q,['one']);deferred();discord_write('POST','/channels/123/messages')
        self.q.finish('one')
        with patch('delivery_metrics.time.time',return_value=102):end(token)
        self.assertIsNone(summary(self.path))
    def test_retry_preserves_original_receipt_start_and_first_write(self):
        with patch('delivery_metrics.time.time',return_value=101):token=begin(self.q,['one']);end(token)
        with patch('delivery_metrics.time.time',return_value=110):token=begin(self.q,['one']);discord_write('POST','/channels/123/messages')
        self.q.finish('one')
        with patch('delivery_metrics.time.time',return_value=112):end(token)
        data=summary(self.path);self.assertEqual(data['p50'],10);self.assertEqual(data['queue_p50'],1)
    def test_legacy_queue_without_metrics_is_not_fabricated(self):
        with self.q.connect() as db:db.execute('DELETE FROM delivery_latency')
        token=begin(self.q,['one']);discord_write('POST','/channels/123/messages');self.q.finish('one');end(token)
        self.assertIsNone(summary(self.path))
    def test_metrics_storage_failure_does_not_fail_delivery_context(self):
        with patch.object(self.q,'connect',side_effect=sqlite3.OperationalError('busy')):
            token=begin(self.q,['one']);discord_write('POST','/channels/123/messages');end(token)
    def test_nested_contexts_and_non_message_requests_stay_separate(self):
        self.q.put('two','issue_comment',{},received=100)
        with patch('delivery_metrics.time.time',return_value=101):outer=begin(self.q,['one']);inner=begin(self.q,['two'])
        with patch('delivery_metrics.time.time',return_value=102):discord_write('POST','/channels/123/messages');end(inner)
        with patch('delivery_metrics.time.time',return_value=103):discord_write('GET','/channels/123/messages/456');discord_write('POST','/channels/123/messages/456/threads');end(outer)
        with self.q.connect() as db:rows=dict(db.execute('SELECT delivery,first_discord FROM delivery_latency'))
        self.assertEqual(rows,{'one':None,'two':102})
    def test_global_receipt_is_preserved_when_fanning_out(self):
        service=Service(Path(self.temp.name)/'service',{})
        ids=[service.registry.add('tenant',11,'owner/repo',1,'app',config(n)) for n in (1000,2000)]
        value={'installation':{'id':1},'repository':{'id':11,'full_name':'owner/repo'},'action':'created'}
        with patch('registry.time.time',return_value=100):service.registry.receive('app','event','issue_comment',value)
        with patch('registry.time.time',return_value=105):service.dispatch()
        for identity in ids:
            with service.queue(service.registry.get(identity)).connect() as db:self.assertEqual(db.execute('SELECT received FROM delivery_latency').fetchone()[0],100)
    def test_missing_metrics_file_and_clock_reversal_do_not_produce_statistics(self):
        self.assertIsNone(summary(Path(self.temp.name)/'absent.db'))
        with self.q.connect() as db:db.execute("UPDATE delivery_latency SET started=99,first_discord=103,completed=104")
        self.assertIsNone(summary(self.path))

    def test_partial_sample_without_start_time_is_not_reported(self):
        with self.q.connect() as db:db.execute('UPDATE delivery_latency SET first_discord=103,completed=104')
        self.assertIsNone(summary(self.path))
