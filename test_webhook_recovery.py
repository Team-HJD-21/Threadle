import datetime as dt
import unittest
from unittest.mock import Mock
import test_service as fixtures
from webhook_recovery import WebhookRecovery


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.ServiceTests.setUp
    tearDown = fixtures.ServiceTests.tearDown
    send = fixtures.ServiceTests.send

    def test_failed_deliveries_recover_but_received_and_other_repos_do_not(self):
        now=dt.datetime(2026,11,2,12,tzinfo=dt.timezone.utc).timestamp()
        api=Mock(last_link='')
        guid='aaaaaaaa-bbbb-cccc-dddd-000000000001'
        row={'id':10,'guid':guid,'delivered_at':'2026-11-02T11:00:00Z','status_code':0,
             'installation_id':101,'repository_id':11,'event':'issue_comment'}
        api.call.return_value=[row,dict(row,id=11,guid='aaaaaaaa-bbbb-cccc-dddd-000000000002',repository_id=999),
                               dict(row,id=12,guid='aaaaaaaa-bbbb-cccc-dddd-000000000003',status_code=202)]
        self.service.tokens=Mock()
        self.service.tokens.app_api.return_value=api
        recovery=WebhookRecovery(self.service,clock=lambda:now)
        self.assertEqual(recovery.check(),1)
        api.call.assert_any_call('POST','/app/hook/deliveries/10/attempts')
        self.assertEqual(recovery.check(),0)
        self.send(delivery=guid)
        recovery.clock=lambda:now+4000
        self.assertEqual(recovery.check(),0)

    def test_milestone_duplicate_is_suppressed_but_remove_then_reassign_survives(self):
        base={'issue':{'number':7,'title':'Title','updated_at':'2026-11-02T11:00:00Z'},
              'milestone':{'id':123,'title':'Launch'},'sender':{'id':9},'action':'milestoned'}
        for index,action in enumerate(('milestoned','milestoned','demilestoned','milestoned')):
            self.assertEqual(self.send(event='issues',delivery=f'aaaaaaaa-bbbb-cccc-dddd-{index:012d}',extra=dict(base,action=action)),202)
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM inbox WHERE done=0').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM inbox WHERE done=1').fetchone()[0],1)


    def test_group_preserves_milestone_remove_then_reassign(self):
        from notification_layout import group_payload
        base={'repository':{'id':11,'full_name':'one/repo'},'issue':{'number':7,'title':'Title','updated_at':'2026-11-02T11:00:00Z'},'milestone':{'id':123}}
        entries=[('issues',dict(base,action=action)) for action in ('milestoned','milestoned','demilestoned','milestoned')]
        self.assertEqual(len(group_payload(entries)['_events']),3)
