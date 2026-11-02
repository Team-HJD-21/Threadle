from pathlib import Path
import ast
import sys
import unittest
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deploy.patch_runtime import read_manifest

read_manifest(ROOT)
for path in ROOT.glob('*.py'):
    ast.parse(path.read_bytes(), filename=path.name)

suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_thread_silent_create', 'test_root_status', 'test_digest_settings', 'test_dnd_notifications', 'test_message_edits', 'test_commit_notifications', 'test_notification_delivery', 'test_operator_web', 'test_service', 'test_command_jobs', 'test_channel_topics', 'test_connection_welcome', 'test_runtime_logging', 'test_discord_threads', 'test_alert_feed', 'test_notification_language',
    'test_readable_notifications', 'test_notification_polish', 'test_notification_update',
    'test_private_media_regression', 'test_workflow_notifications', 'test_webhook_recovery', 'test_ci_stage_and_migration', 'test_channel_layouts', 'test_cd_patch', 'test_demo_preview', 'test_product_web',
    'test_team_features.NotificationTests', 'test_team_features.TeamWebTests',
    'test_connect', 'test_privacy_controls', 'test_legal_and_lifecycle', 'test_media_dependency',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
if not result.wasSuccessful():
    raise SystemExit(1)
if sys.platform == 'win32':
    with tempfile.TemporaryDirectory(prefix='recovery-settings-',dir=ROOT) as folder:
        subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(ROOT/'deploy/test_recovery_settings.ps1'),'-TemporaryRoot',folder],check=True)
raise SystemExit(0)
