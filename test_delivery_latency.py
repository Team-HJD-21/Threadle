from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch
from delivery import Queue,COALESCE_SECONDS

class LatencyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.q=Queue(Path(self.temp.name)/'queue.db')
        self.payload={'repository':{'id':11,'full_name':'owner/repo'},'action':'opened','pull_request':{'number':1,'title':'Title','body':'Body'}}
    def tearDown(self):self.temp.cleanup()
    def put(self,identity,event='pull_request',when=100,payload=None):
        with patch('delivery.time.time',return_value=when):self.q.put(identity,event,payload or self.payload)
    def test_default_sends_first_event_without_waiting(self):
        self.put('pr')
        with patch('delivery.time.time',return_value=100):
            job,ids=self.q.group(self.q.next(COALESCE_SECONDS))
        self.assertEqual(ids,['pr']);self.assertEqual(job[1],'pull_request')
    def test_waiting_batch_does_not_hold_back_ci(self):
        self.put('pr');self.put('ci','workflow_run',100.1)
        with patch('delivery.time.time',return_value=100.2):
            job=self.q.next(2);self.assertEqual(job[0],'ci')
            self.assertEqual(self.q.group(job)[1],['ci'])
        self.q.finish('ci')
        with patch('delivery.time.time',return_value=102):self.assertEqual(self.q.next(2)[0],'pr')
    def test_related_updates_still_group_and_replay_after_restart(self):
        self.put('opened');changed=dict(self.payload,action='edited')
        self.put('edited',when=101.5,payload=changed)
        with patch('delivery.time.time',return_value=102):first,ids=self.q.group(self.q.next(2),2)
        self.assertEqual(ids,['opened','edited'])
        q=Queue(self.q.path)
        with patch('delivery.time.time',return_value=102.1):replay,again=q.group(q.next(2),2)
        self.assertEqual((replay,again),(first,ids))
        self.assertEqual(len(json.loads(first[2])['_events']),2)
    def test_ready_topics_keep_order_and_retry_delay_is_respected(self):
        self.put('one');other=dict(self.payload,pull_request=dict(self.payload['pull_request'],number=2))
        self.put('two',when=100.5,payload=other)
        with patch('delivery.time.time',return_value=103):
            self.assertEqual(self.q.next(2)[0],'one');self.q.retry('one',0)
            self.assertEqual(self.q.next(2)[0],'two')
        with patch('delivery.time.time',return_value=105):self.assertEqual(self.q.next(2)[0],'one')
    def test_no_window_remains_available_for_legacy_queue_callers(self):
        self.put('one')
        with patch('delivery.time.time',return_value=100.1):self.assertEqual(self.q.next()[0],'one')
