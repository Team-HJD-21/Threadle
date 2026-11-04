from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import state_db
from service import Service
from state_encryption import migrate, rollback, finalize, export, export_recovery, import_recovery
from state_backups import create, verify, restore_check
from test_service import config


@unittest.skipUnless(os.name == 'nt', 'Production key protection uses Windows DPAPI')
class EncryptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'runtime'
        self.service = Service(self.root, {})
        self.identities = [self.service.registry.add('tenant', 11, 'owner/private', 1, 'app', config(guild)) for guild in (1000, 2000)]
        self.binding = self.service.registry.get(self.identities[0])
        self.queue = self.service.queue(self.binding)
        self.queue.put('private-event', 'issues', {'action': 'opened', 'issue': {'number': 7, 'body': 'private-body-unique-marker'}})
        self.queue.retry('private-event', 2, .1)
        with self.queue.connect() as db:
            db.execute('UPDATE jobs SET next=0')
            db.execute('INSERT INTO links VALUES(?,?,?)', ('github-thread:owner/private:issue:7', '1001', '7777'))
        self.snapshot = create(self.root)
        self.job = self.root / 'cd-deployments' / 'encryption-test'

    def test_existing_connections_pending_events_links_and_backups_survive_conversion(self):
        original = self.service.registry.active()
        self.assertGreater(migrate(self.root, self.job), 0)
        reopened = Service(self.root, {})
        self.assertEqual(reopened.registry.active(), original)
        queue = reopened.queue(reopened.registry.get(self.identities[0]))
        job = queue.next()
        self.assertEqual((job[0], job[3]), ('private-event', 3))
        self.assertEqual(json.loads(job[2])['issue']['body'], 'private-body-unique-marker')
        with queue.connect() as db:
            self.assertEqual(db.execute('SELECT thread FROM links').fetchone(), ('7777',))
            db.execute('CREATE TABLE unique_test(v TEXT UNIQUE)')
            db.execute("INSERT INTO unique_test VALUES('one')")
            with self.assertRaises(sqlite3.IntegrityError): db.execute("INSERT INTO unique_test VALUES('one')")
        for path in (self.root/'connections.sqlite3', queue.path, self.snapshot/'connections.sqlite3'):
            self.assertNotEqual(path.read_bytes()[:16], b'SQLite format 3\0')
            self.assertNotIn(b'private-body-unique-marker', path.read_bytes())
            with closing(sqlite3.connect(path)) as db:
                with self.assertRaises(sqlite3.DatabaseError): db.execute('SELECT * FROM sqlite_master').fetchall()
        self.assertTrue(verify(self.snapshot)['encrypted'])
        target = self.base/'restored'
        restore_check(self.snapshot, target)
        with closing(state_db.connect(target/queue.path.relative_to(self.root), key_from=self.root)) as db:
            self.assertEqual(db.execute('SELECT id FROM jobs').fetchone(), ('private-event',))
        finalize(self.root, self.job)
        self.assertFalse((self.root/'state-conversion-active.json').exists())
        self.assertFalse(list((self.root/'.state-keys').glob('conversion-*')))
        # Fresh state and online backups use encryption after the one-time conversion.
        second = reopened.queue(reopened.registry.get(self.identities[1]))
        second.put('second-event', 'issues', {'body': 'second-private-marker'})
        self.assertNotIn(b'second-private-marker', second.path.read_bytes())
        fresh = create(self.root)
        self.assertTrue(verify(fresh)['encrypted'])
        self.assertFalse(list(fresh.rglob('*.dpapi')))

    def test_finalize_can_remove_guard_while_live_receiver_opens_encrypted_state(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        migrate(self.root,self.job)
        active=self.root/'state-conversion-active.json'
        entered=threading.Event();finished=threading.Event()
        original=Path.read_text
        def read(path,*args,**kwargs):
            if path==active and threading.current_thread().name.startswith('guard-reader'):
                entered.set()
                if not finished.wait(5):raise TimeoutError('Finalize did not finish')
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',read),ThreadPoolExecutor(max_workers=1,thread_name_prefix='guard-reader') as worker:
            future=worker.submit(self.service.registry.active)
            try:
                self.assertTrue(entered.wait(5))
                finalize(self.root,self.job)
            finally:finished.set()
            self.assertEqual(future.result(timeout=5),self.service.registry.active())
        self.assertIsNotNone(self.queue.next())

    def test_post_finalize_failure_can_rollback_existing_encryption_without_resetting_live_data(self):
        migrate(self.root,self.job);finalize(self.root,self.job)
        next_job=self.root/'cd-deployments'/'next-deployment'
        self.assertEqual(migrate(self.root,next_job),0)
        finalize(self.root,next_job)
        self.queue.put('after-finalize','issues',{'body':'keep live event'})
        expected=Service(self.root,{}).registry.active()
        rollback(self.root,next_job)
        self.assertEqual(Service(self.root,{}).registry.active(),expected)
        with self.queue.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT id FROM jobs')},{'private-event','after-finalize'})
        self.assertTrue((self.root/state_db.POLICY).exists())
        self.assertFalse((self.root/'state-conversion-active.json').exists())

    def test_wal_contains_no_plain_repository_body(self):
        migrate(self.root,self.job); finalize(self.root,self.job)
        with self.queue.connect() as keeper:
            self.assertEqual(keeper.execute('PRAGMA journal_mode=WAL').fetchone(),('wal',))
            keeper.execute('BEGIN')
            keeper.execute('SELECT count(*) FROM jobs').fetchone()
            self.queue.put('wal-private','issue_comment',{'body':'uniquely-sensitive-wal-body'})
            wal = self.queue.path.with_name(self.queue.path.name+'-wal')
            self.assertTrue(wal.exists())
            self.assertNotIn(b'uniquely-sensitive-wal-body',wal.read_bytes())
            keeper.rollback()

    def test_recovery_key_requires_passphrase_and_restores_encrypted_backup_on_new_root(self):
        migrate(self.root,self.job); finalize(self.root,self.job)
        recovery = self.base/'separately-stored-recovery.json'
        password = 'test-only-recovery-passphrase-2026'
        export_recovery(self.root,recovery,password)
        raw_key = state_db.load_key(self.root)
        self.assertNotIn(raw_key,recovery.read_bytes())
        target = self.base/'recovered-runtime'
        restore_check(self.snapshot,target)
        with self.assertRaises(state_db.StorageError): import_recovery(target,recovery,'wrong passphrase')
        self.assertFalse((target/state_db.KEY_FILE).exists())
        import_recovery(target,recovery,password)
        self.assertEqual(Service(target,{}).registry.active(),self.service.registry.active())
        with self.assertRaises(state_db.StorageError): import_recovery(target,recovery,password)
        with self.assertRaises(state_db.StorageError): export_recovery(self.root,self.root/'unsafe-recovery.json',password)

    def test_corrupt_key_fails_without_replacing_key_or_database(self):
        migrate(self.root, self.job)
        finalize(self.root, self.job)
        key = self.root/state_db.KEY_FILE
        encrypted = (self.root/'connections.sqlite3').read_bytes()
        key.write_bytes(b'invalid-protected-key')
        with self.assertRaises(state_db.StorageError): Service(self.root, {})
        self.assertEqual(key.read_bytes(), b'invalid-protected-key')
        self.assertEqual((self.root/'connections.sqlite3').read_bytes(), encrypted)

    def test_missing_key_is_never_silently_regenerated(self):
        migrate(self.root,self.job); finalize(self.root,self.job)
        (self.root/state_db.KEY_FILE).unlink()
        with self.assertRaises(state_db.StorageError):
            migrate(self.root,self.root/'cd-deployments'/'missing-key')
        self.assertFalse((self.root/state_db.KEY_FILE).exists())

    def test_failed_preparation_retains_plain_connections_and_pending_event(self):
        calls = 0
        def fail_second(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2: raise OSError('simulated disk failure')
            return export(*args, **kwargs)
        with patch('state_encryption.export', side_effect=fail_second):
            with self.assertRaises(OSError): migrate(self.root, self.job)
        self.assertFalse((self.root/state_db.POLICY).exists())
        self.assertFalse((self.root/'state-conversion-active.json').exists())
        self.assertEqual(Service(self.root, {}).registry.active(), self.service.registry.active())
        self.assertEqual(self.queue.next()[0], 'private-event')
        self.assertTrue(verify(self.snapshot))

    def test_rollback_preserves_events_received_during_health_check(self):
        migrate(self.root, self.job)
        active = Service(self.root, {})
        queue = active.queue(active.registry.get(self.identities[0]))
        queue.put('received-after-migration', 'issues', {'body': 'new pending event'})
        active.queue(active.registry.get(self.identities[1])).put('new-queue', 'issues', {'body': 'other server'})
        rollback(self.root, self.job)
        self.assertFalse((self.root/state_db.POLICY).exists())
        restored = Service(self.root, {})
        with restored.queue(restored.registry.get(self.identities[0])).connect() as db:
            self.assertEqual(set(r[0] for r in db.execute('SELECT id FROM jobs')), {'private-event', 'received-after-migration'})
        self.assertEqual(restored.queue(restored.registry.get(self.identities[1])).next()[0], 'new-queue')
        self.assertTrue(verify(self.snapshot))

    def test_interrupted_replacement_blocks_empty_database_creation_and_can_recover(self):
        real_replace = os.replace
        crashed = False
        def crash_after_original(source, destination):
            nonlocal crashed
            result = real_replace(source, destination)
            if str(destination).endswith('.original') and not crashed:
                crashed = True
                raise SystemExit('simulated process loss')
            return result
        with patch('state_encryption.os.replace', side_effect=crash_after_original):
            with self.assertRaises(SystemExit): migrate(self.root, self.job)
        with self.assertRaises(state_db.StorageError): Service(self.root, {})
        rollback(self.root, self.job)
        self.assertEqual(Service(self.root, {}).registry.active(), self.service.registry.active())
        self.assertEqual(self.queue.next()[0], 'private-event')

    def test_old_application_restore_requires_compatible_database_and_late_rollback_keeps_new_work(self):
        from deploy.patch_runtime import restore
        migrate(self.root,self.job); finalize(self.root,self.job)
        with self.assertRaisesRegex(ValueError,'database compatibility'):
            restore(self.root,self.job)
        self.queue.put('after-health','issues',{'body':'keep this newly received event'})
        rollback(self.root,self.job)
        (self.job/'before.json').write_text('{}')
        restore(self.root,self.job)
        with self.queue.connect() as db:
            self.assertEqual(set(r[0] for r in db.execute('SELECT id FROM jobs')),{'private-event','after-health'})

    def test_interrupted_rollback_resumes_without_resetting_connections(self):
        migrate(self.root,self.job)
        self.queue.put('before-rollback','issues',{'body':'still pending'})
        real_replace = os.replace
        crashed = False
        def crash_after_plain_replacement(source,destination):
            nonlocal crashed
            result = real_replace(source,destination)
            if str(source).endswith('.rollback') and not crashed:
                crashed = True
                raise SystemExit('simulated rollback process loss')
            return result
        with patch('state_encryption.os.replace',side_effect=crash_after_plain_replacement):
            with self.assertRaises(SystemExit): rollback(self.root,self.job)
        with self.assertRaises(state_db.StorageError): Service(self.root,{})
        rollback(self.root,self.job)
        self.assertEqual(Service(self.root,{}).registry.active(),self.service.registry.active())
        with self.queue.connect() as db:
            self.assertEqual(set(r[0] for r in db.execute('SELECT id FROM jobs')),{'private-event','before-rollback'})

    def test_tampered_ciphertext_is_rejected_without_plaintext_fallback(self):
        migrate(self.root, self.job); finalize(self.root, self.job)
        path = self.root/'connections.sqlite3'
        raw = bytearray(path.read_bytes()); raw[80] ^= 1; path.write_bytes(raw)
        with self.assertRaises(state_db.StorageError): Service(self.root, {})
        self.assertEqual(path.read_bytes(), raw)


class GuardRaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/state_db.POLICY).write_text('{}')
        self.active=self.root/'state-conversion-active.json'
        self.path=self.root/'connections.sqlite3'

    def test_guard_removed_during_read_is_treated_as_completed_conversion(self):
        self.active.write_text('{"state":"awaiting-health"}')
        original=Path.read_text
        def remove_before_read(path,*args,**kwargs):
            if path==self.active:self.active.unlink()
            return original(path,*args,**kwargs)
        with patch.object(Path,'read_text',remove_before_read):
            self.assertEqual(state_db.key_root(self.path),self.root)

    def test_offline_conversion_states_still_block_database_access(self):
        for state in ('preparing','replacing','rolling-back'):
            self.active.write_text(json.dumps({'state':state}))
            with self.assertRaises(state_db.StorageError):state_db.key_root(self.path)

    def test_unreadable_and_invalid_guards_are_not_ignored(self):
        self.active.write_text('invalid json')
        with self.assertRaises(ValueError):state_db.key_root(self.path)
        with patch.object(Path,'read_text',side_effect=PermissionError):
            with self.assertRaises(PermissionError):state_db.key_root(self.path)


if __name__ == '__main__': unittest.main()
