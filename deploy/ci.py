"""Run all regressions locally, or one disjoint partition on a hosted runner."""
from pathlib import Path
import argparse
import ast
import json
import sys
import time
import unittest
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from deploy.patch_runtime import read_manifest

TEST_TARGETS = [
    'test_state_encryption',
    'test_encrypted_runtime',
    'test_message_tracking', 'test_alert_filtering', 'test_operator_mail',
    'test_review_conversations',
    'test_restart_notices', 'test_ci_runner',
    'test_api_usage', 'test_topic_state', 'test_settings_audit', 'test_admin_updates', 'test_state_backups', 'test_yield_retries', 'test_delivery_metrics', 'test_metadata_notifications', 'test_delivery_latency', 'test_merge_conflicts', 'test_deferred_threads', 'test_comment_deletion', 'test_full_descriptions', 'test_thread_silent_create', 'test_root_status', 'test_digest_settings', 'test_dnd_notifications', 'test_message_edits', 'test_commit_notifications', 'test_notification_delivery', 'test_operator_web', 'test_service', 'test_command_jobs', 'test_channel_topics', 'test_connection_welcome', 'test_runtime_logging', 'test_discord_threads', 'test_alert_feed', 'test_notification_language',
    'test_readable_notifications', 'test_notification_polish', 'test_notification_update',
    'test_private_media_regression', 'test_workflow_notifications', 'test_webhook_recovery', 'test_ci_stage_and_migration', 'test_channel_layouts', 'test_cd_patch', 'test_demo_preview', 'test_product_web',
    'test_team_features.NotificationTests', 'test_team_features.TeamWebTests',
    'test_connect', 'test_privacy_controls', 'test_legal_and_lifecycle', 'test_media_dependency',
]

def cases(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from cases(test)
        else:
            yield test


def partition(suite, shard, shards):
    if shards < 1 or not 1 <= shard <= shards:
        raise ValueError('shard must be between 1 and shards')
    # Keep original order within each partition so unittest retains class/module
    # setup and teardown. Every test, including encrypted variants, runs once.
    return unittest.TestSuite(test for index, test in enumerate(cases(suite))
                              if index % shards == shard - 1)


class TimedResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.timings = []

    def startTest(self, test):
        self.started = time.perf_counter()
        super().startTest(test)

    def stopTest(self, test):
        self.timings.append({'id': test.id(),
                             'seconds': round(time.perf_counter() - self.started, 4)})
        super().stopTest(test)


def regressions(shard=1, shards=1, timings=None):
    suite = partition(unittest.defaultTestLoader.loadTestsFromNames(TEST_TARGETS), shard, shards)
    if not suite.countTestCases():
        raise ValueError('Empty test partition')
    started = time.perf_counter()
    result = unittest.TextTestRunner(verbosity=2, resultclass=TimedResult).run(suite)
    report = {'shard': shard, 'shards': shards, 'tests_run': result.testsRun,
              'skipped': len(result.skipped), 'successful': result.wasSuccessful(),
              'seconds': round(time.perf_counter() - started, 4), 'tests': result.timings}
    print(f"Partition {shard}/{shards}: {report['tests_run']} tests in {report['seconds']:.1f}s")
    print('Slowest tests (including setup and teardown):')
    for row in sorted(result.timings, key=lambda row: row['seconds'], reverse=True)[:15]:
        print(f"  {row['seconds']:7.2f}s  {row['id']}")
    if timings:
        path = Path(timings)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    return result.wasSuccessful()


def checks():
    read_manifest(ROOT)
    for path in ROOT.glob('*.py'):
        ast.parse(path.read_bytes(), filename=path.name)
    if sys.platform == 'win32':
        with tempfile.TemporaryDirectory(prefix='recovery-settings-', dir=ROOT) as folder:
            for name in ('test_recovery_settings.ps1', 'test_receiver_process.ps1'):
                subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive',
                                '-ExecutionPolicy', 'Bypass', '-File', str(ROOT/'deploy'/name),
                                '-TemporaryRoot', folder], check=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--tests-only', action='store_true')
    mode.add_argument('--checks-only', action='store_true')
    parser.add_argument('--shard', type=int, default=1)
    parser.add_argument('--shards', type=int, default=1)
    parser.add_argument('--timings', help='Write per-test durations as JSON, including on test failure')
    args = parser.parse_args(argv)
    if args.shards < 1 or not 1 <= args.shard <= args.shards:
        parser.error('--shard must be between 1 and --shards')
    if not args.tests_only:
        checks()
    if not args.checks_only and not regressions(args.shard, args.shards, args.timings):
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
