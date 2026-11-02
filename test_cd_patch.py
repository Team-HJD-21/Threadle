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
        self.job = self.base / 'job'

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
        target = self.root / 'service.py'
        target.write_text('custom_server_change = True\n')
        with self.assertRaisesRegex(ValueError, 'differs from supported baseline'):
            prepare(self.root, self.source, self.job)
        self.assertFalse(self.job.exists())
        self.assertEqual(target.read_text(), 'custom_server_change = True\n')

    def test_changed_target_after_prepare_is_rejected_before_first_write(self):
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root, self.source, self.job)
        (self.root / 'service.py').write_text('changed_during_preflight = True\n')
        before = (self.root / 'auth.py').read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            apply(self.root, self.job)
        self.assertEqual((self.root / 'auth.py').read_bytes(), before)

    def test_corrupt_backup_prevents_restore(self):
        with patch('deploy.patch_runtime.subprocess.run'):
            prepare(self.root, self.source, self.job)
        (self.job / 'backup/service.py').write_text('corrupt')
        with self.assertRaisesRegex(ValueError, 'Backup checksum mismatch'):
            restore(self.root, self.job)
