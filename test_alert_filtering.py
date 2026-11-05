import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest

from alert_policy import filter_alert, should_alert, METADATA_ACTIONS
from delivery import Queue
from discord_threads import Topic
from team_notifications import TeamNotifications
from test_team_features import binding, MessageApi


class AlertPolicyTests(unittest.TestCase):
    def test_metadata_and_content_edits_do_not_create_alerts(self):
        for event in ('issues', 'pull_request', 'discussion'):
            for action in METADATA_ACTIONS | {'edited'}:
                with self.subTest(event=event, action=action):
                    self.assertIsNone(filter_alert(event, {'action':action}))
        for event in ('issue_comment', 'discussion_comment', 'pull_request_review_comment'):
            for action in ('edited', 'deleted'):
                self.assertFalse(should_alert(event, {'action':action}))

    def test_creation_comments_and_review_requests_remain_visible(self):
        for event, action in (('pull_request','opened'), ('issues','opened'), ('discussion','created'),
                              ('issue_comment','created'), ('discussion_comment','created'),
                              ('pull_request_review_comment','created'), ('pull_request','review_requested')):
            self.assertTrue(should_alert(event, {'action':action}), (event,action))

    def test_only_meaningful_submitted_reviews_create_alerts(self):
        for state, body, expected in (('approved','',True), ('changes_requested','',True),
                                      ('commented','Please check this.',True), ('commented',' \n ',False)):
            value={'action':'submitted','review':{'state':state,'body':body}}
            self.assertEqual(should_alert('pull_request_review',value),expected)
        self.assertFalse(should_alert('pull_request_review',{'action':'edited','review':{'state':'approved'}}))
        self.assertFalse(should_alert('pull_request_review',{'action':'dismissed','review':{'state':'approved'}}))

    def test_existing_lifecycle_and_workflow_policy_is_preserved(self):
        for action in ('closed','reopened','answered','unanswered','converted_to_draft','ready_for_review','synchronize'):
            self.assertTrue(should_alert('pull_request', {'action':action}))
        self.assertTrue(should_alert('workflow_run',{'action':'completed'}))

    def test_bundle_excludes_metadata_from_count_and_preserves_original(self):
        payload={'_created':False,'_events':[
            {'event':'issues','payload':{'action':'assigned'}},
            {'event':'issue_comment','payload':{'action':'created'}},
            {'event':'issue_comment','payload':{'action':'edited'}},
            {'event':'pull_request_review','payload':{'action':'submitted','review':{'state':'approved'}}},
        ]}
        original=copy.deepcopy(payload)
        event,visible=filter_alert('threadle_bundle',payload)
        self.assertEqual(event,'threadle_bundle')
        self.assertEqual(len(visible['_events']),2)
        self.assertEqual(payload,original)
        self.assertFalse(visible['_created'])

    def test_single_visible_bundle_entry_uses_its_readable_activity(self):
        payload={'_events':[{'event':'issues','payload':{'action':'labeled'}},
                            {'event':'issue_comment','payload':{'action':'created','comment':{'body':'Hello'}}}]}
        event,visible=filter_alert('threadle_bundle',payload)
        self.assertEqual(event,'issue_comment');self.assertEqual(visible['action'],'created')
        self.assertIsNone(filter_alert('threadle_bundle',{'_events':[payload['_events'][0]]}))


class NotificationFilteringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.queue=Queue(Path(self.temp.name)/'queue.sqlite3');self.api=MessageApi()
        self.now=dt.datetime(2026,11,5,12,tzinfo=dt.timezone.utc).timestamp()
        self.topic=Topic('owner/repo','pr',7,'A PR','https://github.com/owner/repo/pull/7')
        self.b=binding()
    def tearDown(self):self.temp.cleanup()
    def notices(self):return TeamNotifications(self.b,self.queue,self.api,clock=lambda:self.now)

    def test_metadata_never_schedules_secondary_notifications(self):
        notices=self.notices()
        for action in METADATA_ACTIONS | {'edited'}:
            notices.topic(self.topic,'pull_request',{'action':action},action,'777',['104','201'])
        notices.flush()
        self.assertEqual(self.api.messages,[])
        self.assertFalse(notices.has_pending())

    def test_filtered_bundle_uses_comment_title(self):
        self.notices().topic(self.topic,'threadle_bundle',{'_events':[
            {'event':'pull_request','payload':{'action':'assigned'}},
            {'event':'issue_comment','payload':{'action':'created'}}]},'bundle','777',['104'])
        self.assertEqual(len(self.api.messages),1)
        body=self.api.messages[0][1]['embeds'][0]['description']
        self.assertIn('New comment',body);self.assertNotIn('updates',body)

    def test_base_alert_feed_also_filters_metadata_and_edited_comments(self):
        from alert_feed import AlertFeed
        feed=AlertFeed(self.api,self.b.config,self.queue)
        feed.topic(self.topic,'pull_request',{'action':'assigned'},'assigned','777')
        feed.topic(self.topic,'issue_comment',{'action':'edited'},'edited','777')
        self.assertEqual(self.api.messages,[])
        feed.topic(self.topic,'issue_comment',{'action':'created'},'created','777')
        self.assertEqual(len(self.api.messages),1)

    def test_metadata_is_not_added_to_dnd_roundup(self):
        self.now=dt.datetime(2026,11,5,23,tzinfo=dt.timezone.utc).timestamp()
        self.b.config['team_notifications']={'quiet':True,'quiet_start':22,'quiet_end':8,'utc_offset':0}
        notices=self.notices()
        notices.topic(self.topic,'pull_request',{'action':'labeled'},'label','777',['104'])
        self.assertFalse(notices.has_pending())
        notices.topic(self.topic,'issue_comment',{'action':'created'},'comment','777',['104'])
        self.assertTrue(notices.has_pending())
        self.now+=9*3600;self.notices().flush()
        self.assertEqual(len(self.api.messages),1)
        self.assertIn('PR #7',self.api.messages[0][1]['embeds'][0]['description'])
