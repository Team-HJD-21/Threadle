from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch
from service import Service
from test_service import config
from data_retention import Retention,PERIOD
from state_backups import create,verify,restore_check,latest,purge_expired,StateBackups,checksum

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name);self.root=self.base/'service'
        self.service=Service(self.root,{})
        self.identity=self.service.registry.add('tenant',11,'owner/repo',1,'app',config(1000))
        self.binding=self.service.registry.get(self.identity);self.queue=self.service.queue(self.binding)
        self.queue.put('pending','workflow_run',{'marker':'pending event'})
        with self.queue.connect() as db:db.execute('INSERT INTO links VALUES(?,?,?)',('github-thread:owner/repo:pr:1','1002','777'))
    def tearDown(self):self.temp.cleanup()
    def test_online_wal_state_restores_pending_jobs_and_thread_links(self):
        with closing(sqlite3.connect(self.queue.path)) as live:
            live.execute('PRAGMA journal_mode=WAL');live.execute("INSERT INTO jobs(id,event,payload) VALUES('second','workflow_run','{}')");live.commit()
            snapshot=create(self.root)
        target=self.base/'restored';restore_check(snapshot,target)
        relative=self.queue.path.relative_to(self.root)
        with closing(sqlite3.connect(target/relative)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs WHERE done=0').fetchone()[0],2)
            self.assertEqual(db.execute('SELECT thread FROM links').fetchone()[0],'777')
        self.assertEqual(latest(self.root)['files'],len(verify(snapshot)['files']))
    def test_credentials_and_login_sessions_are_not_copied(self):
        (self.root/'credentials.json').write_text('not-a-real-secret')
        with closing(sqlite3.connect(self.root/'connect.sqlite3')) as db:db.execute('CREATE TABLE sessions(token TEXT)');db.commit()
        snapshot=create(self.root);names={row['path'] for row in verify(snapshot)['files']}
        self.assertNotIn('connect.sqlite3',names);self.assertNotIn('credentials.json',names)
    def test_corruption_rejected_before_restore_directory_is_created(self):
        snapshot=create(self.root);relative=verify(snapshot)['files'][0]['path'];(snapshot/relative).write_bytes(b'corrupt')
        target=self.base/'bad-restore'
        with self.assertRaises(ValueError):restore_check(snapshot,target)
        self.assertFalse(target.exists())
    def test_existing_restore_target_and_traversal_manifest_are_rejected(self):
        snapshot=create(self.root)
        with self.assertRaises(ValueError):restore_check(snapshot,self.root)
        path=snapshot/'manifest.json';data=json.loads(path.read_text());data['files'][0]['path']='../outside.sqlite3';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):verify(snapshot)
        self.assertTrue((self.root/'connections.sqlite3').is_file())
    def test_unknown_sidecar_is_not_accepted_as_verified_snapshot_data(self):
        snapshot=create(self.root);(snapshot/'connections.sqlite3-wal').write_bytes(b'unverified')
        with self.assertRaises(ValueError):verify(snapshot)
    def test_low_disk_or_interruption_never_publishes_partial_snapshot(self):
        before=checksum(self.root/'connections.sqlite3')
        with patch('state_backups.shutil.disk_usage',return_value=Mock(free=0)):
            with self.assertRaises(OSError):create(self.root)
        stop=threading.Event();stop.set()
        with self.assertRaises(TimeoutError):create(self.root,stop=stop)
        self.assertIsNone(latest(self.root));self.assertEqual(checksum(self.root/'connections.sqlite3'),before)
    def test_daily_tick_does_not_repeat_recent_verified_backup(self):
        snapshot=create(self.root);StateBackups(self.root).tick(threading.Event())
        self.assertEqual(len(list((self.root/'database-backups').glob('db-*'))),1);self.assertTrue(snapshot.exists())
    def test_connection_expiry_removes_matching_backups_but_keeps_manual_material(self):
        snapshot=create(self.root);manual=self.root/'database-backups'/'manual';manual.mkdir();(manual/'note.txt').write_text('keep')
        self.service.registry.disable(self.identity)
        with self.service.registry.db() as db:db.execute('UPDATE connection_retention SET disconnected_at=?',(time.time()-PERIOD-1,))
        Retention(self.service).purge()
        self.assertFalse(snapshot.exists());self.assertTrue((manual/'note.txt').is_file())
        with self.service.registry.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM backup_expiry').fetchone()[0],0)
    def test_expiry_cleanup_request_survives_temporary_failure(self):
        snapshot=create(self.root);self.service.registry.disable(self.identity)
        with self.service.registry.db() as db:db.execute('UPDATE connection_retention SET disconnected_at=?',(time.time()-PERIOD-1,))
        with patch('state_backups.expire',side_effect=OSError('busy')):
            with self.assertRaises(OSError):Retention(self.service).purge()
        with self.service.registry.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM backup_expiry').fetchone()[0],1)
        purge_expired(self.root);self.assertFalse(snapshot.exists())
    def test_source_queue_symlink_cannot_read_or_delete_outside_state(self):
        outside=self.base/'outside.sqlite3'
        with closing(sqlite3.connect(outside)) as db:db.execute('CREATE TABLE marker(value TEXT)');db.commit()
        self.queue.path.unlink()
        try:self.queue.path.symlink_to(outside)
        except OSError:self.skipTest('OS does not permit symlink creation')
        with self.assertRaises(ValueError):create(self.root)
        self.assertTrue(outside.is_file())
