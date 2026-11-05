from pathlib import Path
import subprocess
import tempfile
import unittest

from deploy.package_source import build


class SourcePackageTests(unittest.TestCase):
    def test_package_includes_tracked_runtime_assets_and_deploy_inputs_only(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / 'checkout'
            root.mkdir()
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            names = ['service.py', 'deploy/deploy.ps1', 'deploy/changes.json', 'web_assets/icon.svg',
                     'requirements-app.txt', 'runtime.json', 'private.pem', 'queue.sqlite3', 'README.md']
            for name in names:
                path = root / name
                path.parent.mkdir(exist_ok=True)
                path.write_text('fixture')
            (root / 'deploy/changes.json').write_text('{"files":{}}')
            subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
            (root / 'untracked.py').write_text('not from checked commit')
            target = Path(folder) / 'package'
            count = build(root, target, 'a' * 40)
            self.assertEqual(count, 5)
            self.assertEqual((target / 'source-commit.txt').read_text().strip(), 'a' * 40)
            for name in names[:5]:
                self.assertEqual((target / name).read_text(), (root / name).read_text())
            for name in names[5:] + ['untracked.py', '.git/config']:
                self.assertFalse((target / name).exists())
            with self.assertRaises(ValueError):
                build(root, target, 'a' * 40)
            with self.assertRaises(ValueError):
                build(root, Path(folder) / 'other', 'main')

    def test_deploy_downloads_only_this_verified_run_and_checks_commit_before_apply(self):
        text = (Path(__file__).parent / '.github/workflows/threadle.yml').read_text()
        deploy = text.split('\n  deploy:\n')[1]
        self.assertNotIn('actions/checkout@', deploy)
        self.assertNotIn('Expand-Archive', deploy.replace('# No Git/PowerShell Expand-Archive fallback on the production notebook.', ''))
        self.assertIn('needs: verify', deploy)
        self.assertIn('name: threadle-source-${{ github.sha }}-${{ github.run_attempt }}', deploy)
        self.assertIn('digest-mismatch: error', deploy)
        self.assertLess(deploy.index('Deployment artifact commit mismatch'), deploy.index('-File $deployScript'))
        self.assertIn('source-commit.txt', deploy)
        self.assertNotIn('github-token:', deploy)  # Default current-run scope, no other run/repository selection.
