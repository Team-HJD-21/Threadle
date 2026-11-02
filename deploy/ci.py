from pathlib import Path
import ast
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deploy.patch_runtime import read_manifest

read_manifest(ROOT)
for path in ROOT.glob('*.py'):
    ast.parse(path.read_bytes(), filename=path.name)

suite = unittest.defaultTestLoader.loadTestsFromNames([
    'test_service', 'test_discord_threads', 'test_alert_feed', 'test_notification_language',
    'test_readable_notifications', 'test_notification_polish', 'test_notification_update',
    'test_private_media_regression', 'test_workflow_notifications', 'test_webhook_recovery', 'test_ci_stage_and_migration', 'test_channel_layouts', 'test_cd_patch', 'test_demo_preview', 'test_product_web',
    'test_team_features.NotificationTests', 'test_team_features.TeamWebTests',
    'test_connect', 'test_privacy_controls', 'test_legal_and_lifecycle', 'test_media_dependency',
])
result = unittest.TextTestRunner(verbosity=2).run(suite)
raise SystemExit(not result.wasSuccessful())
