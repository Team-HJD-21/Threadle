import copy
import json
import unittest
from unittest.mock import patch
import test_command_jobs as fixtures
from test_service import config
from settings_audit import flush,history
from discord_threads import ApiError

class SettingsAuditTests(unittest.TestCase):
    setUp=fixtures.CommandTests.setUp
    tearDown=fixtures.CommandTests.tearDown
    payload=fixtures.CommandTests.payload
    discord=fixtures.CommandTests.discord

    def notifications(self,lose=False):
        self.sent=[];self.lost=lose
        def api(method,path,data=None):
            if method=='GET' and '/channels/' in path:
                if '/messages' in path:return copy.deepcopy(self.sent)
                return {'guild_id':'1000','type':0}
            if method=='POST' and path.endswith('/messages'):
                row=dict(copy.deepcopy(data),id=str(5000+len(self.sent)),author={'id':'bot'})
                self.sent.append(row)
                if self.lost:self.lost=False;raise ApiError('Discord','network')
                return row
            return self.discord(method,path,data)
        self.api.call.side_effect=api

    def test_update_is_audited_and_announced_with_before_after_actor(self):
        self.notifications();self.jobs.execute(self.payload(review_hours=4))
        flush(self.service)
        self.assertEqual(len(self.sent),1)
        text=str(self.sent[0]);self.assertIn('<@1>',text);self.assertIn('4h',text)
        self.assertEqual(self.sent[0]['allowed_mentions'],{'parse':[]})
        with self.service.registry.db() as db:
            row=db.execute('SELECT old,new,sent FROM settings_changes').fetchone()
        self.assertEqual(json.loads(row[0])['review_hours'],0)
        self.assertEqual(json.loads(row[1])['review_hours'],4);self.assertEqual(row[2],1)

    def test_status_and_identical_values_do_not_create_audit_or_notice(self):
        self.notifications();before=self.service.registry.get(self.identity)
        self.jobs.execute(self.payload());self.jobs.execute(self.payload(review_hours=0));flush(self.service)
        self.assertEqual(self.service.registry.get(self.identity),before);self.assertFalse(self.sent)
        with self.service.registry.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM settings_changes').fetchone()[0],0)

    def test_replayed_command_does_not_revert_newer_settings(self):
        first=self.payload(review_hours=4);self.jobs.execute(first)
        self.jobs.execute(self.payload(review_hours=12));self.jobs.execute(first)
        self.assertEqual(self.service.registry.get(self.identity).config['team_notifications']['review_hours'],12)
        with self.service.registry.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM settings_changes').fetchone()[0],2)

    def test_lost_public_response_recovers_without_duplicate_after_restart(self):
        self.notifications(lose=True);self.jobs.execute(self.payload(review_hours=4));flush(self.service)
        self.assertEqual(len(self.sent),1)
        with self.service.registry.db() as db:db.execute('UPDATE settings_changes SET next=0')
        flush(self.service);self.assertEqual(len(self.sent),1)
        with self.service.registry.db() as db:self.assertEqual(db.execute('SELECT sent FROM settings_changes').fetchone()[0],1)

    def test_dnd_notice_is_silent(self):
        self.notifications();self.jobs.execute(self.payload(quiet=True,quiet_start=22,quiet_end=8,utc_offset=0))
        with patch('settings_audit.time.time',return_value=1793754000):flush(self.service)
        self.assertEqual(len(self.sent),1);self.assertEqual(self.sent[0].get('flags',0)&4096,4096)

    def test_history_is_server_scoped_and_includes_legacy_actor_records(self):
        other=self.service.registry.add('two',11,'one/repo',101,'app',config(2000))
        self.jobs.execute(self.payload(review_hours=4))
        result=self.jobs.execute(self.payload('settings-history'))
        self.assertIn('<@1>',''.join(result));self.assertIn('4h',''.join(result))
        self.assertIn('기록된',history(self.service.registry,self.service.registry.get(other)))
        binding=self.service.registry.get(self.identity)
        self.service.registry.configure(binding.id,binding.config,'old-user','discord-command')
        self.assertIn('old-user',''.join(history(self.service.registry,binding)))
        self.assertIn('변경 전후 값',''.join(history(self.service.registry,binding)))

    def test_disconnected_connection_does_not_send_pending_notice(self):
        self.notifications();self.jobs.execute(self.payload(review_hours=4))
        self.service.registry.disable(self.identity);flush(self.service);self.assertFalse(self.sent)

    def test_record_failure_rolls_back_configuration_and_old_audit(self):
        before=self.service.registry.get(self.identity)
        with patch('settings_audit.record',side_effect=RuntimeError('storage failed')):
            with self.assertRaises(RuntimeError):self.jobs.execute(self.payload(review_hours=4))
        self.assertEqual(self.service.registry.get(self.identity),before)
        with self.service.registry.db() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM connection_audit WHERE action='configure'").fetchone()[0],0)
