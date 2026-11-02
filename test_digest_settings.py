import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from digest_settings import enabled,apply
from command_jobs import command_definitions
from delivery import Queue
from team_notifications import TeamNotifications,validate_options
from test_team_features import binding,MessageApi
import test_command_jobs as commands

class DigestPolicyTests(unittest.TestCase):
    def test_legacy_modes_are_resolved_without_mutating_settings(self):
        for mode in ('off','daily','weekly'):
            old={'digest':mode,'digest_hour':16,'digest_weekday':2,'enabled_at':1}
            snapshot=dict(old)
            self.assertEqual(enabled(old,'daily'),mode=='daily');self.assertEqual(enabled(old,'weekly'),mode=='weekly')
            self.assertEqual(old,snapshot)
    def test_switches_and_activation_times_are_independent(self):
        old={'digest':'daily','digest_hour':9,'enabled_at':10}
        both=apply(old,{'weekly_digest':True},100)
        self.assertTrue(enabled(both,'daily'));self.assertTrue(enabled(both,'weekly'))
        self.assertEqual(both.get('daily_enabled_at',both['enabled_at']),10)
        self.assertEqual(both['weekly_enabled_at'],100)
        weekly=apply(both,{'daily_digest':False},200)
        self.assertFalse(enabled(weekly,'daily'));self.assertTrue(enabled(weekly,'weekly'))
        self.assertEqual(weekly['weekly_enabled_at'],100)
    def test_time_changes_do_not_move_the_other_schedule_or_rearm_unchanged_legacy(self):
        old={'digest':'weekly','digest_hour':16,'digest_weekday':2,'enabled_at':10}
        result=apply(old,{'digest_hour':8},100)
        self.assertEqual(result['weekly_digest_hour'],16)
        self.assertNotIn('weekly_enabled_at',result)
        same=apply(old,{'weekly_digest':True,'weekly_digest_hour':16,'digest_weekday':2,'utc_offset':9},100)
        self.assertNotIn('weekly_enabled_at',same)
        moved=apply(result,{'weekly_digest_hour':17},200)
        self.assertEqual(moved['weekly_enabled_at'],200)
    def test_legacy_cached_command_can_still_turn_digests_off(self):
        result=apply({'daily_digest':True,'weekly_digest':True},{'digest':'off'},100)
        self.assertFalse(enabled(result,'daily'));self.assertFalse(enabled(result,'weekly'))
    def test_invalid_switches_are_rejected(self):
        for value in ('yes',1,None):
            with self.assertRaises(ValueError):validate_options({'daily_digest':value})
    def test_command_options_are_adjacent_in_topic_order(self):
        options=next(c['options'] for c in command_definitions() if c['name']=='settings')
        self.assertEqual([o['name'] for o in options],['repository','language','utc_offset','review_hours','batch_seconds','daily_digest','digest_hour','weekly_digest','digest_weekday','weekly_digest_hour','quiet','quiet_start','quiet_end'])
        self.assertTrue(all(len(o['description'])<=100 for o in options))
        self.assertEqual(len(options),len({o['name'] for o in options}))
    def test_both_schedules_deliver_at_their_own_times_without_repeating(self):
        now=[dt.datetime(2026,11,6,9,tzinfo=dt.timezone.utc).timestamp()]
        options={'daily_digest':True,'weekly_digest':True,'digest_hour':9,'weekly_digest_hour':17,'digest_weekday':4,'utc_offset':0,'daily_enabled_at':now[0]-86400,'weekly_enabled_at':now[0]-86400}
        with tempfile.TemporaryDirectory() as folder:
            queue=Queue(Path(folder)/'queue.db');api=MessageApi();b=binding(options)
            def notices():return TeamNotifications(b,queue,api,clock=lambda:now[0])
            with patch('team_notifications.open_prs',return_value=[]),patch('team_notifications.open_issue_digest',return_value=(0,[])):
                notices().digest(Mock());self.assertEqual(len(api.messages),1)
                now[0]+=8*3600
                notices().digest(Mock());notices().digest(Mock())
                self.assertEqual(len(api.messages),2)
                self.assertIn('일일',api.messages[0][1]['embeds'][0]['title'])
                self.assertIn('주간',api.messages[1][1]['embeds'][0]['title'])
    def test_same_time_schedules_have_distinct_receipts_and_restart_safe_delivery(self):
        now=dt.datetime(2026,11,6,9,tzinfo=dt.timezone.utc).timestamp()
        options={'daily_digest':True,'weekly_digest':True,'digest_hour':9,'weekly_digest_hour':9,'digest_weekday':4,'utc_offset':0,'enabled_at':now-1}
        with tempfile.TemporaryDirectory() as folder:
            queue=Queue(Path(folder)/'queue.db');api=MessageApi();b=binding(options)
            with patch('team_notifications.open_prs',return_value=[]),patch('team_notifications.open_issue_digest',return_value=(0,[])):
                for _ in range(2):TeamNotifications(b,queue,api,clock=lambda:now).digest(Mock())
            self.assertEqual(len(api.messages),2)
            self.assertNotEqual(api.messages[0][1]['nonce'],api.messages[1][1]['nonce'])

class IndependentCommandTests(unittest.TestCase):
    setUp=commands.CommandTests.setUp
    tearDown=commands.CommandTests.tearDown
    discord=commands.CommandTests.discord
    payload=commands.CommandTests.payload
    def test_commands_enable_both_and_turn_off_each_without_other_schedule_changes(self):
        self.jobs.execute(self.payload(daily_digest=True,weekly_digest=True,digest_hour=9,weekly_digest_hour=17,digest_weekday=4))
        before=self.service.registry.get(self.identity).config['team_notifications']
        self.assertTrue(before['daily_digest']);self.assertTrue(before['weekly_digest'])
        self.jobs.execute(self.payload(daily_digest=False))
        after=self.service.registry.get(self.identity).config['team_notifications']
        self.assertFalse(after['daily_digest']);self.assertTrue(after['weekly_digest'])
        self.assertEqual(after['weekly_enabled_at'],before['weekly_enabled_at'])
        self.jobs.execute(self.payload(weekly_digest=False))
        self.assertFalse(self.service.registry.get(self.identity).config['team_notifications']['weekly_digest'])
