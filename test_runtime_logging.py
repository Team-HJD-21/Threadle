import io
import logging
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock,patch

from discord_threads import ApiError
from runtime_logging import SafeFormatter
import test_service as fixtures


class LoggingTests(unittest.TestCase):
    def capture(self,error):
        stream=io.StringIO();handler=logging.StreamHandler(stream)
        handler.setFormatter(SafeFormatter('%(message)s',secrets=['secret-token-value']))
        logger=logging.Logger('test');logger.addHandler(handler)
        try:
            raise error
        except Exception:
            logger.exception('Failure')
        return stream.getvalue()

    def test_database_reason_and_traceback_are_preserved(self):
        result=self.capture(sqlite3.OperationalError('database is locked'))
        self.assertIn('database is locked',result)
        self.assertIn('Traceback',result)
        self.assertIn('in capture',result)

    def test_api_status_and_code_are_preserved(self):
        result=self.capture(ApiError('Discord',403,50013))
        self.assertIn('status=403 code=50013',result)

    def test_untrusted_exception_payload_and_secrets_are_not_logged(self):
        self.assertNotIn('private-comment-body',self.capture(ValueError('private-comment-body')))
        result=self.capture(OSError('Bearer secret-token-value https://example.com/image?jwt=hidden'))
        for secret in ('secret-token-value','jwt=hidden'):
            self.assertNotIn(secret,result)
        self.assertIn('[REDACTED]',result)

    def test_fatal_main_exception_creates_durable_log_and_fault_capture(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            script="from runtime_logging import configure; import sys,sqlite3; configure(sys.argv[1],env={}); raise sqlite3.OperationalError('database is locked')"
            result=subprocess.run([sys.executable,'-B','-c',script,folder],capture_output=True,text=True,timeout=20)
            self.assertNotEqual(result.returncode,0)
            text=(Path(folder)/'receiver.log').read_text('utf-8')
            self.assertIn('Unhandled main-thread exception',text)
            self.assertIn('database is locked',text)
            self.assertIn('pid=',text)
            self.assertIn('thread=MainThread',text)
            self.assertTrue((Path(folder)/'receiver-fault.log').is_file())

    def test_unavailable_optional_fault_file_does_not_prevent_startup_logging(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            (Path(folder)/'receiver-fault.log').mkdir()
            script="from runtime_logging import configure; import sys; configure(sys.argv[1],env={})"
            result=subprocess.run([sys.executable,'-B','-c',script,folder],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0)
            text=(Path(folder)/'receiver.log').read_text('utf-8')
            self.assertIn('Native fault capture unavailable',text)
            self.assertIn('Receiver starting',text)


class CoordinatorLoggingTests(unittest.TestCase):
    setUp=fixtures.ServiceTests.setUp
    tearDown=fixtures.ServiceTests.tearDown

    def test_coordinator_exception_is_logged_and_still_exits_for_recovery(self):
        self.service.web=None;self.service.discord_connect=None
        stop=threading.Event()
        with patch('presence.Presence.start',return_value=Mock()),patch('guild_lifecycle.GuildLifecycle.start'),patch('data_retention.Retention.start'),patch('ci_channel_migration.CiChannelMigration.start'),patch('webhook_recovery.WebhookRecovery.start'),patch.object(self.service.registry,'active',side_effect=sqlite3.OperationalError('database is locked')):
            with self.assertLogs('service',level='ERROR') as logs, self.assertRaises(sqlite3.OperationalError):
                self.service.run(stop)
        self.assertTrue(stop.is_set())
        self.assertIn('Traceback',logs.output[0])
        self.assertIn('database is locked',logs.output[0])
        self.assertIn('Coordinator stopped',logs.output[0])
