import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy import ci


class RunnerTests(unittest.TestCase):
    def test_current_manifest_upgrades_the_last_successful_production_release(self):
        root = Path(__file__).parent
        previous = json.loads((root/'deploy/fixtures/beta31-upgrade-baseline.json').read_text())
        current = ci.read_manifest(root)
        ci.check_upgrade(current, previous)

    def test_losing_a_predecessor_or_an_older_supported_version_fails_before_deployment(self):
        previous = {'files': {'runtime.py': {'before': 'oldest', 'after': 'installed', 'previous': ['older']}}}
        for current in (
            {'files': {}},
            {'files': {'runtime.py': {'before': 'oldest', 'after': 'new', 'previous': ['older']}}},
            {'files': {'runtime.py': {'before': 'oldest', 'after': 'new', 'previous': ['installed']}}},
        ):
            with self.assertRaisesRegex(ValueError, 'runtime.py'):
                ci.check_upgrade(current, previous)
        ci.check_upgrade({'files': {'runtime.py': {'before': 'oldest', 'after': 'new', 'previous': ['older', 'installed']}}}, previous)
        with self.assertRaisesRegex(ValueError, 'runtime.py'):
            ci.check_upgrade({'files': {'runtime.py': {'before': 'installed', 'after': 'new'}}},
                             {'files': {'runtime.py': {'before': None, 'after': 'installed'}}})

    def test_checks_receive_the_explicit_baseline_ref(self):
        with patch.object(ci, 'checks') as checks:
            self.assertEqual(ci.main(['--checks-only', '--baseline-ref', 'HEAD^1']), 0)
            checks.assert_called_once_with('HEAD^1')

    def test_four_partitions_cover_every_regression_exactly_once_in_original_order(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromNames(ci.TEST_TARGETS)
        self.assertFalse(loader.errors)
        original = [test.id() for test in ci.cases(suite)]
        partitions = [[test.id() for test in ci.partition(suite, n, 4)] for n in range(1, 5)]
        self.assertEqual(len(original), len(set(original)))
        self.assertCountEqual(original, [name for group in partitions for name in group])
        for n, group in enumerate(partitions):
            self.assertEqual(group, original[n::4])
        self.assertTrue(any('test_encrypted_runtime.' in name for name in partitions[0]))

    def test_partition_preserves_class_setup_and_teardown(self):
        events = []

        class Example(unittest.TestCase):
            @classmethod
            def setUpClass(cls): events.append('setup')
            @classmethod
            def tearDownClass(cls): events.append('teardown')
            def test_a(self): events.append('a')
            def test_b(self): events.append('b')
            def test_c(self): events.append('c')

        result = unittest.TestResult()
        ci.partition(unittest.defaultTestLoader.loadTestsFromTestCase(Example), 1, 2).run(result)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(events, ['setup', 'a', 'c', 'teardown'])

    def test_invalid_partition_cannot_silently_skip_tests(self):
        for shard, shards in ((0, 4), (5, 4), (1, 0)):
            with self.assertRaises(ValueError): ci.partition(unittest.TestSuite(), shard, shards)

    def test_failed_regressions_fail_the_command(self):
        with patch.object(ci, 'checks') as checks, patch.object(ci, 'regressions', return_value=False):
            self.assertEqual(ci.main(['--tests-only', '--shard', '2', '--shards', '4']), 1)
            checks.assert_not_called()

    def test_deployment_checks_can_run_independently(self):
        with patch.object(ci, 'checks') as checks, patch.object(ci, 'regressions') as regressions:
            self.assertEqual(ci.main(['--checks-only']), 0)
            checks.assert_called_once()
            regressions.assert_not_called()

    def test_failure_still_writes_timings(self):
        class Failing(unittest.TestCase):
            def runTest(self): self.fail('intentional fixture failure')

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'timings.json'
            with patch.object(ci.unittest.defaultTestLoader, 'loadTestsFromNames', return_value=unittest.TestSuite([Failing()])), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                self.assertFalse(ci.regressions(timings=path))
            report = json.loads(path.read_text())
            self.assertFalse(report['successful'])
            self.assertEqual(report['tests_run'], 1)
            self.assertEqual(len(report['tests']), 1)
            self.assertGreaterEqual(report['tests'][0]['seconds'], 0)
