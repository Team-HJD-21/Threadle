import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from alert_feed import AlertFeed, activity, alert_channel_config
from discord_threads import Topic
from delivery import Queue, CachedThreads
REPO = "Team-HJD-21/unity-6-the-developer"
from test_discord_threads import FakeDiscord


class FakeAlertApi:
    def __init__(self):
        self.posts = []
        self.calls = []
        self.accept_then_fail = False

    def call(self, method, path, data=None):
        self.calls.append((method, path, copy.deepcopy(data)))
        if method == 'GET' and path in ('/channels/alerts', '/channels/ci'):
            return {'guild_id': 'guild', 'type': 0}
        if method == 'GET' and path.split('?')[0] in ('/channels/alerts/messages', '/channels/ci/messages'):
            return [row for row in reversed(copy.deepcopy(self.posts)) if row['channel_id'] == path.split('/')[2]]
        if method == 'POST' and path in ('/channels/alerts/messages', '/channels/ci/messages'):
            message = dict(copy.deepcopy(data), id=str(100 + len(self.posts)), channel_id=path.split('/')[2])
            self.posts.append(message)
            if self.accept_then_fail:
                self.accept_then_fail = False
                raise RuntimeError('Response lost after Discord accepted the message')
            return message
        if method == 'PATCH' and '/messages/' in path:
            row = next(row for row in self.posts if row['id'] == path.rsplit('/', 1)[1])
            row.update(copy.deepcopy(data))
            return row
        raise AssertionError((method, path))


class AlertTests(unittest.TestCase):
    def setUp(self):
        clock = patch('alert_feed.time.time', return_value=1793880000)
        clock.start()
        self.addCleanup(clock.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.temp.name) / 'queue.sqlite3'
        self.queue = Queue(self.path)
        self.api = FakeAlertApi()
        self.config = {'guild_id': 'guild', 'alert_channel': 'alerts', 'ci_channel': 'ci'}
        self.feed = AlertFeed(self.api, self.config, self.queue)
        self.topic = Topic(REPO, 'issue', 582, 'Title @everyone', 'https://github.com/' + REPO + '/issues/582')

    def tearDown(self):
        self.temp.cleanup()

    def notify(self, key='event'):
        self.feed.topic(self.topic, 'issue_comment', {'action': 'created', 'sender': {'login': 'hjd5031'}}, key, 'thread')

    def test_posts_normal_alert_with_correct_thread_link_and_no_mentions(self):
        self.feed.check()
        self.notify()
        message = self.api.posts[0]
        self.assertEqual(message['content'], '')
        self.assertIn('ISSUE #582', message['embeds'][0]['description'])
        self.assertIn('New comment', message['embeds'][0]['description'])
        self.assertEqual(message['embeds'][0]['url'], 'https://discord.com/channels/guild/thread')
        self.assertIn('https://discord.com/channels/guild/thread', message['embeds'][0]['description'])
        self.assertEqual(message['allowed_mentions'], {'parse': []})
        self.assertFalse(any('/threads' in path for _, path, _ in self.api.calls))

    def test_duplicate_redelivery_and_restart_do_not_repeat_notice(self):
        self.notify()
        self.feed = AlertFeed(self.api, self.config, Queue(self.path))
        self.notify()
        self.assertEqual(len(self.api.posts), 1)
        self.notify('different-event')
        self.assertEqual(len(self.api.posts), 1)

    def test_ambiguous_send_recovery_finds_durable_receipt(self):
        self.api.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            self.notify()
        self.feed = AlertFeed(self.api, self.config, Queue(self.path))
        self.notify()
        self.assertEqual(len(self.api.posts), 1)

    def test_pr_review_and_merge_have_readable_activity(self):
        self.assertEqual(activity('pull_request_review', {'review': {'state': 'approved'}}), 'Approved')
        self.assertEqual(activity('pull_request', {'action': 'closed', 'pull_request': {'merged': True}}), 'Merged')
        self.assertEqual(activity('discussion_comment', {'action': 'deleted'}), 'Comment deleted')

    def test_all_topic_types_link_to_their_actual_thread(self):
        for kind, thread in [('issue', 'issue-thread'), ('pr', 'pr-thread'), ('discussion', 'discussion-thread')]:
            topic = Topic(REPO, kind, 12, kind, 'https://github.com')
            self.feed.topic(topic, 'issue_comment', {'action': 'created'}, 'same-event', thread)
        self.assertEqual(len(self.api.posts), 3)
        for post, value in zip(self.api.posts, ('issue-thread', 'pr-thread', 'discussion-thread')):
            self.assertIn('https://discord.com/channels/guild/' + value, post['embeds'][0]['description'])

    def test_ci_failure_alert_deduplicates_run_across_deliveries(self):
        run = {'id': 123, 'name': 'PR CI', 'event': 'push', 'conclusion': 'failure',
               'head_branch': 'main', 'html_url': 'https://github.com/run'}
        self.feed.ci(REPO, {'workflow_run': run}, 'event')
        self.assertIn('Failed', self.api.posts[0]['content'])
        self.assertEqual([row['channel_id'] for row in self.api.posts], ['ci', 'alerts'])
        self.assertIn('channels/guild/ci', self.api.posts[1]['embeds'][0]['description'])
        for changed in ({'conclusion': 'success'}, {'event': 'pull_request'}):
            self.feed.ci(REPO, {'workflow_run': dict(run, **changed)}, 'other')
        self.assertEqual(len(self.api.posts), 2)
        self.feed.ci(REPO, {'workflow_run': dict(run, id=124, name='Other workflow')}, 'other')
        self.assertEqual(len(self.api.posts), 4)

    def test_ci_retry_recovers_detail_and_sends_alert_once(self):
        payload = {'workflow_run': {'id': 123, 'name': 'Deploy', 'event': 'workflow_dispatch',
                   'conclusion': 'failure', 'head_branch': 'main', 'html_url': 'https://github.com/run'}}
        self.api.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            self.feed.ci(REPO, payload, 'event')
        self.feed = AlertFeed(self.api, self.config, Queue(self.path))
        self.feed.ci(REPO, payload, 'event')
        self.feed.ci(REPO, payload, 'event')
        self.assertEqual([row['channel_id'] for row in self.api.posts], ['ci', 'alerts'])

    def test_finds_only_requested_alert_channel_in_existing_category(self):
        api = Mock()
        channels = [{'id': 'wrong', 'type': 0, 'parent_id': 'other', 'name': 'github-alert'},
                    {'id': 'right', 'type': 0, 'parent_id': 'github', 'name': '🚨github-alert'},
                    {'id': 'ci', 'type': 0, 'parent_id': 'github', 'name': 'github-ci'}]
        api.call.side_effect = [{'parent_id': 'github'}, channels]
        result = alert_channel_config(api, {'guild_id': 'g', 'channels': {'issue': 'i'}})
        self.assertEqual(result['alert_channel'], 'right')
        self.assertEqual(result['ci_channel'], 'ci')
        self.assertTrue(all(call.args[0] == 'GET' for call in api.call.call_args_list))

    def test_missing_or_ambiguous_channel_never_creates_one(self):
        for channels in ([], [{'id': 'a', 'parent_id': 'github', 'type': 0, 'name': 'github-alert'},
                              {'id': 'b', 'parent_id': 'github', 'type': 0, 'name': 'github-alert'}]):
            api = Mock()
            api.call.side_effect = [{'parent_id': 'github'}, channels]
            with self.assertRaises(ValueError):
                alert_channel_config(api, {'guild_id': 'g', 'channels': {'issue': 'i'}})


    def test_real_thread_delivery_returns_link_target_and_deduplicates_detail(self):
        api = FakeDiscord()
        threads = CachedThreads(api, 'g', {'issue': 'c'}, self.queue)
        threads.bot = {'id': 'bot'}
        first = threads.deliver(self.topic, {'title': 'Detail'}, 'webhook:test', lambda: False)
        retry = threads.deliver(self.topic, {'title': 'Detail'}, 'webhook:test', lambda: False)
        self.assertEqual(first, retry)
        self.assertEqual(len(api.posts[first]), 1)


if __name__ == '__main__':
    unittest.main()
