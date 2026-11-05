from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from delivery import Queue
from service import Service
from test_service import config


class IdleWorkTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_active_bindings_use_one_connection_and_preserve_pause_disable_rules(self):
        service=Service(self.root,{})
        ids=[service.registry.add('team',11,'owner/repo',1,'app',config(1000+i*10)) for i in range(10)]
        service.registry.disable(ids[0])
        with service.registry.db() as db:db.execute('INSERT INTO admin_paused VALUES(?)',(ids[1],))
        original=service.registry.db
        with patch.object(service.registry,'db',wraps=original) as opening:
            self.assertEqual([b.id for b in service.registry.active()],ids[2:])
            self.assertEqual(opening.call_count,1)
        self.assertEqual([b.id for b in service.registry.active(include_paused=True)],ids[1:])

    def test_new_work_wakes_an_idle_queue_without_added_batching_delay(self):
        queue=Queue(self.root/'queue.db');queue.wake.clear()
        queue.put('new','issues',{})
        # Event set after the durable commit: an arrival between check and wait
        # is remembered, even if no waiter was yet blocked.
        self.assertTrue(queue.wake.is_set())
        self.assertTrue(queue.wait_for_work())
        self.assertEqual(queue.next()[0],'new')

    def test_retry_deadline_shortens_idle_wait_and_empty_queue_stays_asleep(self):
        queue=Queue(self.root/'queue.db');queue.wake=Mock()
        queue.put('retry','issues',{})
        with queue.connect() as db:db.execute('UPDATE jobs SET next=102')
        with patch('delivery.time.time',return_value=100):queue.wait_for_work()
        queue.wake.wait.assert_called_with(2)
        queue.finish('retry')
        queue.wait_for_work();queue.wake.wait.assert_called_with(5)

    def test_commit_interrupts_a_worker_already_waiting_for_work(self):
        queue=Queue(self.root/'queue.db');entered=threading.Event();finished=threading.Event()
        event=queue.wake
        class ObservedEvent:
            def set(self):event.set()
            def wait(self,timeout):entered.set();return event.wait(timeout)
        queue.wake=ObservedEvent()
        worker=threading.Thread(target=lambda:(queue.wait_for_work(30),finished.set()),daemon=True)
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            queue.put('arrived','issues',{})
            self.assertTrue(finished.wait(2))
            self.assertEqual(queue.next()[0],'arrived')
        finally:event.set();worker.join(2)
