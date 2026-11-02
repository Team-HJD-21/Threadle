import unittest
import test_alert_feed as fixtures
from alert_feed import AlertFeed
from delivery import Queue


class WorkflowTests(unittest.TestCase):
    setUp = fixtures.AlertTests.setUp
    tearDown = fixtures.AlertTests.tearDown
    def run_payload(self, action, conclusion=None, attempt=1):
        return {'action':action, 'workflow_run':{'id':987, 'run_attempt':attempt, 'name':'Deploy', 'event':'push',
            'head_branch':'feature/test', 'html_url':'https://github.com/run/987', 'conclusion':conclusion,
            'run_started_at':'2026-11-02T10:00:00Z', 'updated_at':'2026-11-02T10:01:05Z'}}

    def test_running_updates_same_message_after_restart_and_ignores_late_start(self):
        self.feed.ci_detail(REPO, self.run_payload('in_progress'), 'start')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('<t:', self.api.posts[0]['embeds'][0]['description'])
        self.feed = AlertFeed(self.api, self.config, Queue(self.path))
        self.feed.ci_detail(REPO, self.run_payload('completed','success'), 'end')
        self.feed.ci_detail(REPO, self.run_payload('in_progress'), 'late')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('성공', self.api.posts[0]['content'])
        self.assertIn('feature/test', self.api.posts[0]['embeds'][0]['description'])
        self.assertIn('1m 5s', self.api.posts[0]['embeds'][0]['description'])

    def test_failed_attempt_alert_once_and_rerun_gets_new_message(self):
        failed = self.run_payload('completed','failure')
        self.feed.ci(REPO, failed, 'end')
        self.feed.ci(REPO, failed, 'end')
        self.assertEqual([p['channel_id'] for p in self.api.posts], ['ci','alerts'])
        self.feed.ci_detail(REPO, self.run_payload('in_progress',attempt=2), 'rerun')
        self.assertEqual(len(self.api.posts), 3)

    def test_success_never_posts_to_alert(self):
        self.feed.ci_detail(REPO, self.run_payload('in_progress'), 'start')
        self.feed.ci(REPO, self.run_payload('completed','success'), 'end')
        self.assertEqual([p['channel_id'] for p in self.api.posts], ['ci'])

    def test_lost_start_response_recovers_message_without_duplicate(self):
        self.api.accept_then_fail=True
        with self.assertRaises(RuntimeError):
            self.feed.ci_detail(REPO,self.run_payload('in_progress'),'start')
        self.feed.ci_detail(REPO,self.run_payload('in_progress'),'start')
        self.assertEqual(len(self.api.posts),1)


REPO='owner/repo'
