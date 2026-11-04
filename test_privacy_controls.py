import json
import os
from pathlib import Path
import re
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
        without_audience = {key: value for key, value in self.accepted().items() if key != 'legal_audience'}
        for fields in ({}, without_audience, {**self.accepted(), 'legal_age': '0'}, {**self.accepted(), 'legal_version': 'old'}):
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
            for control in re.findall(r'<input\b[^>]*>', html):
                if 'name="legal_' in control:
                    self.assertNotRegex(control, r'\schecked(?:\s|=|>)')
            self.assertIn('Before you connect' if language == 'en' else '연결 전 확인', html)
            self.assertIn('rel="noopener noreferrer"', html)
            self.assert_audience_notice(html, language)

    def assert_audience_notice(self, html, language):
        self.assertIn('legal-audience-notice', html)
        for text in (('titles, bodies, comments, reviews, images, and GIFs',
                      'even without GitHub access', 'default role permissions',
                      'checked which roles and members') if language == 'en' else
                     ('제목·본문·댓글·리뷰·이미지·GIF', 'GitHub 접근 권한이 없는 구성원도',
                      '서버의 기본 역할 권한', '채널을 볼 수 있는 역할·구성원을 확인')):
            self.assertIn(text, html)

    def test_repository_visibility_notice_only_labels_known_private_or_internal_repositories(self):
        self.configure()
        original_repositories = self.users.repositories
        for visibility, ko, en in (({'private': True}, '비공개 저장소 (GitHub)', 'Private repository (GitHub)'),
                                   ({'visibility': 'private'}, '비공개 저장소 (GitHub)', 'Private repository (GitHub)'),
                                   ({'private': True, 'visibility': 'internal'}, '조직 내부 저장소 (GitHub)', 'Internal repository (GitHub)'),
                                   ({'private': False, 'visibility': 'public'}, None, None), ({}, None, None)):
            with patch.object(self.users, 'repositories', side_effect=lambda token, installation, extra=visibility:
                              [{**repo, **extra} for repo in original_repositories(token, installation)]):
                for language, label in (('ko', ko), ('en', en)):
                    self.browser.language = language
                    status, _, html = self.browser.request('GET', '/connect/channels')
                    self.assertEqual(status, 200)
                    if label:
                        self.assertIn('repository-visibility', html)
                        self.assertIn(label, html)
                    else:
                        self.assertNotIn('repository-visibility', html)

    def test_team_settings_preserve_audience_notice_and_require_confirmation_on_apply(self):
        self.configure()
        for language in ('ko', 'en'):
            self.browser.language = language
            status, _, html = self.browser.request('GET', '/connect/team')
            self.assertEqual(status, 200)
            self.assert_audience_notice(html, language)
            self.assertIn('name="legal_audience" value="1" required', html)
            self.assertNotIn('name="legal_audience" value="1" required checked', html)
        fields = {key: value for key, value in self.accepted().items() if key != 'legal_audience'}
        status, _, _ = self.browser.request('POST', '/connect/team/apply',
                                            {'csrf': self.session().data['csrf'], **fields})
        self.assertEqual(status, 400)
        self.assertEqual(self.bot.created_payloads, [])
        self.assertEqual(self.service.registry.active(), [])

    def test_acceptance_is_idempotent_and_scoped_to_repository(self):
        self.configure()
        self.assertEqual(self.raw_save(self.accepted())[0], 303)
        self.assertEqual(self.raw_save()[0], 303)
        html = self.browser.request('GET', '/connect/channels')[2]
        self.assertNotIn('name="legal_terms"', html)
        self.assertIn('legal-audience-notice', html)
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

    def test_connection_expiry_removes_email_snapshots_but_keeps_operator_subscription(self):
        from test_service import config
        self.service.operator_mail.subscribe('operator@example.com','operator',True)
        identity=self.service.registry.connect(11,'team/repo',101,config(1000),'1','22')
        self.service.registry.disable(identity)
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operator_mail_outbox').fetchone()[0],1)
            db.execute('UPDATE connection_retention SET disconnected_at=? WHERE id=?',(self.old,identity))
        Retention(self.service).purge()
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM operator_mail_outbox').fetchone()[0],0)
        self.assertTrue(self.service.operator_mail.subscribed('operator@example.com'))
