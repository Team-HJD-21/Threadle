import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from contextlib import closing
from unittest.mock import patch

from data_retention import Retention, PERIOD
from legal_consent import VERSION
from operational_retention import purge_operations, remove_old_tree
from test_connect import WebFixture


class ConfirmationTests(WebFixture):
    def raw_save(self, fields=None):
        return self.browser.request('POST', '/connect/save', {'csrf': self.session().data['csrf'], **(fields or {})})

    def accepted(self):
        return {'legal_version': VERSION, 'legal_terms': '1', 'legal_age': '1', 'legal_audience': '1'}

    def test_unconfirmed_or_stale_notice_cannot_create_discord_channels_or_connections(self):
        self.configure()
        for fields in ({}, {**self.accepted(), 'legal_age': '0'}, {**self.accepted(), 'legal_version': 'old'}):
            self.assertEqual(self.raw_save(fields)[0], 400)
            self.assertEqual(self.bot.created_payloads, [])
            self.assertEqual(self.service.registry.active(), [])
        self.assertEqual(self.raw_save(self.accepted())[0], 303)
        with self.service.registry.db() as db:
            receipt = db.execute('SELECT discord_user,github_user,version,accepted_at FROM legal_acceptances').fetchone()
        self.assertEqual(receipt[:3], ('1','1',VERSION))
        self.assertLess(abs(receipt[3] - time.time()), 10)

    def test_form_is_bilingual_and_checks_are_never_preselected(self):
        self.configure()
        for language in ('ko','en'):
            self.browser.language = language
            status, _, html = self.browser.request('GET', '/connect/channels')
            self.assertEqual(status, 200)
            self.assertIn('legal_terms', html)
            self.assertIn('legal_age', html)
            self.assertIn('legal_audience', html)
            self.assertNotIn(' checked', html)
            self.assertIn('Before you connect' if language == 'en' else '연결 전 확인', html)
            self.assertIn('rel="noopener noreferrer"', html)

    def test_acceptance_is_idempotent_and_scoped_to_repository(self):
        self.configure()
        self.assertEqual(self.raw_save(self.accepted())[0], 303)
        self.assertEqual(self.raw_save()[0], 303)
        html = self.browser.request('GET', '/connect/channels')[2]
        self.assertNotIn('name="legal_terms"', html)
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM legal_acceptances').fetchone()[0], 1)
        session = self.session()
        session.data['legal_acceptance']['scope'] = 'another-repository'
        self.web.sessions.save(session)
        self.assertEqual(self.raw_save()[0], 400)

    def test_existing_connection_is_not_disabled_when_new_notice_is_not_accepted(self):
        self.configure()
        self.assertEqual(self.raw_save(self.accepted())[0], 303)
        binding = self.service.registry.active()[0]
        session = self.session()
        session.data.pop('legal_acceptance')
        self.web.sessions.save(session)
        self.assertEqual(self.raw_save()[0], 400)
        self.assertEqual(self.service.registry.active()[0], binding)


class RetentionTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from service import Service
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = Service(self.root, {'DISCORD_BOT_TOKEN':'unused'})
        self.now = time.time()
        self.old = self.now - PERIOD - 100

    def tree(self, parent, name):
        folder = self.root / parent / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder/'data.txt').write_text('backup')
        os.utime(folder/'data.txt', (self.old,self.old))
        os.utime(folder, (self.old,self.old))
        return folder

    def test_only_recognized_old_artifacts_and_old_admin_records_are_deleted(self):
        old = self.tree('backups','service-20260101T000000Z')
        manual = self.tree('backups','my-manual-backup')
        recent = self.tree('backups','admin-20260102T000000Z')
        (recent/'new.txt').write_text('recent modification')
        credentials = self.root/'credentials.json'
        credentials.write_text('keep')
        with self.service.registry.db() as db:
            db.execute("INSERT INTO admin_audit(actor,action,binding,happened) VALUES('old','pause','x','2000-01-01 00:00:00')")
            db.execute("INSERT INTO admin_audit(actor,action,binding) VALUES('recent','pause','x')")
        purge_operations(self.root,self.now)
        self.assertFalse(old.exists())
        self.assertTrue(manual.exists())
        self.assertTrue(recent.exists())
        self.assertEqual(credentials.read_text(),'keep')
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT actor FROM admin_audit').fetchall(),[('recent',)])

    def test_in_progress_and_failed_update_materials_are_preserved(self):
        from admin_updates import Updates
        updates=Updates(self.root)
        folder=self.tree('backups','service-20260101T000000Z')
        with updates.db() as db:
            db.execute('INSERT INTO updates VALUES(?,?,?,?,?,?,?,?)',('a'*32,'old','hash','operator','applying','',self.old,self.old))
        purge_operations(self.root,self.now)
        self.assertTrue(folder.exists())
        with updates.db() as db:
            db.execute("UPDATE updates SET state='failed'")
        failed=self.tree('admin-updates','a'*32)
        purge_operations(self.root,self.now)
        self.assertTrue(failed.exists())
        self.assertFalse(folder.exists())

    def test_successful_old_update_is_removed_but_unresolved_cd_is_kept(self):
        from admin_updates import Updates
        updates=Updates(self.root)
        with updates.db() as db:
            db.execute('INSERT INTO updates VALUES(?,?,?,?,?,?,?,?)',('b'*32,'old','hash','operator','success','',self.old,self.old))
        old=self.tree('admin-updates','b'*32)
        unresolved=self.tree('cd-deployments','20260101000000-'+'c'*32)
        complete=self.tree('cd-deployments','20260101000000-'+'d'*32)
        (complete/'result.txt').write_text('passed')
        os.utime(complete/'result.txt',(self.old,self.old)); os.utime(complete,(self.old,self.old))
        purge_operations(self.root,self.now)
        self.assertFalse(old.exists())
        self.assertFalse(complete.exists())
        self.assertTrue(unresolved.exists())
        with updates.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM updates').fetchone()[0],0)

    def test_links_and_external_directories_are_never_followed(self):
        outside = self.root.parent / (self.root.name + '-outside')
        outside.mkdir()
        self.addCleanup(lambda: outside.rmdir())
        self.assertFalse(remove_old_tree(self.root,outside,self.now))
        folder=self.tree('backups','service-20260101T000000Z')
        with patch('operational_retention.linked', side_effect=lambda p: Path(p).name == 'data.txt'):
            self.assertFalse(remove_old_tree(self.root,folder,self.now))
        self.assertTrue((folder/'data.txt').exists())

    def test_confirmation_records_are_removed_with_disconnected_connection(self):
        from test_service import config
        identity=self.service.registry.connect(11,'team/repo',101,config(1000),'1','22',consent={'version':VERSION,'accepted_at':self.now})
        self.service.registry.disable(identity)
        with self.service.registry.db() as db:
            db.execute('UPDATE connection_retention SET disconnected_at=? WHERE id=?',(self.old,identity))
        Retention(self.service).purge()
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM legal_acceptances').fetchone()[0],0)
