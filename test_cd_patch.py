from pathlib import Path
import hashlib
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch
from deploy.patch_runtime import prepare, apply, restore


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'server'
        self.source = Path(__file__).parent
        self.root.mkdir()
        self.manifest = json.loads((self.source / 'deploy/changes.json').read_text())
        # Candidate is a complete installed tree; deploy only selected changes.
        for path in self.source.glob('*.py'):
            shutil.copy2(path, self.root / path.name)
        for name in self.manifest['files']:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.source / name, target)
        (self.root / 'credentials.json').write_text('preserve credentials')
        (self.root / 'queue.sqlite3').write_bytes(b'preserve queued events')
        self.changed_file = next(iter(self.manifest['files']))
        self.job = self.base / 'job'
        media = patch('deploy.media_dependency.stage')
        media.start()
        self.addCleanup(media.stop)
        cipher = patch('deploy.cipher_dependency.stage')
        cipher.start()
        self.addCleanup(cipher.stop)

    def tearDown(self):
        self.temp.cleanup()

    def test_applied_patch_can_restore_without_touching_credentials_or_queue(self):
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root, self.source, self.job)
        apply(self.root, self.job)
        restore(self.root, self.job)
        self.assertEqual((self.root / 'credentials.json').read_text(), 'preserve credentials')
        self.assertEqual((self.root / 'queue.sqlite3').read_bytes(), b'preserve queued events')

    def test_unrecognized_server_change_aborts_before_any_backup_or_write(self):
        target = self.root / self.changed_file
        target.write_text('custom_server_change = True\n')
        with self.assertRaisesRegex(ValueError, 'differs from supported baseline'):
            prepare(self.root, self.source, self.job)
        self.assertFalse(self.job.exists())
        self.assertEqual(target.read_text(), 'custom_server_change = True\n')

    def test_preflight_reports_all_unrecognized_files_without_changes(self):
        names = ['connect_auth.py', 'webhook_recovery.py']
        for name in names:
            (self.root/name).write_bytes(b'unknown_local_change = True\n')
        with self.assertRaises(ValueError) as raised:
            prepare(self.root, self.source, self.job)
        for name in names:
            self.assertIn(name + ' (installed sha256=', str(raised.exception))
            self.assertEqual((self.root/name).read_bytes(), b'unknown_local_change = True\n')
        self.assertFalse(self.job.exists())

    def test_changed_target_after_prepare_is_rejected_before_first_write(self):
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root, self.source, self.job)
        (self.root / self.changed_file).write_text('changed_during_preflight = True\n')
        before = (self.root / 'auth.py').read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            apply(self.root, self.job)
        self.assertEqual((self.root / 'auth.py').read_bytes(), before)

    def test_corrupt_backup_prevents_restore(self):
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root, self.source, self.job)
        (self.job / 'backup' / self.changed_file).write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'Backup checksum mismatch'):
            restore(self.root, self.job)


    def test_explicit_previous_release_hash_is_accepted_and_restorable(self):
        from deploy.patch_runtime import digest
        previous = b'previous_release = True\n'
        (self.root/'service.py').write_bytes(previous)
        manifest = {'version':'test','files':{'service.py':{
            'before': digest(b'baseline = True\n'), 'previous':[digest(previous)],
            'after':digest((self.source/'service.py').read_bytes())}}}
        with patch('deploy.patch_runtime.read_manifest',return_value=manifest), patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root,self.source,self.job)
        apply(self.root,self.job)
        restore(self.root,self.job)
        self.assertEqual((self.root/'service.py').read_bytes(),previous)


    def test_beta8_delivery_upgrades_and_restores_exact_previous_content(self):
        # Actual deployed beta.8 source from main 92252017, not fabricated from the current manifest.
        previous=(self.source/'deploy/fixtures/beta8-delivery.py').read_bytes()
        from deploy.patch_runtime import digest
        self.assertEqual(digest(previous),'a25bc5076e93ad49abc4a0d552f8d000f94c8a48b5041d151e463a28d880cb8f')
        (self.root/'delivery.py').write_bytes(previous)
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root,self.source,self.job)
        apply(self.root,self.job)
        self.assertEqual((self.root/'delivery.py').read_bytes(),(self.source/'delivery.py').read_bytes())
        restore(self.root,self.job)
        self.assertEqual((self.root/'delivery.py').read_bytes(),previous)
