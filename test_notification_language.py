import json
from pathlib import Path
import tempfile
import unittest

from alert_feed import AlertFeed
from delivery import Queue, CachedThreads
from discord_threads import Topic, make_embed
from test_alert_feed import FakeAlertApi
from test_discord_threads import FakeDiscord


class NotificationLanguageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(Path(self.temp.name) / 'queue.sqlite3')
        self.topic = Topic('team/repo', 'issue', 1, '한국어 제목', 'https://github.com/team/repo/issues/1')

    def test_english_activity_card_does_not_repeat_after_language_change(self):
        api = FakeAlertApi()
        config = {'guild_id': 'guild', 'alert_channel': 'alerts', 'language': 'en'}
        feed = AlertFeed(api, config, self.queue)
        payload = {'action': 'created', 'sender': {'login': 'author'}}
        feed.topic(self.topic, 'issue_comment', payload, 'event1', 'thread')
        message = api.posts[0]
        self.assertEqual(message['content'], '')
        self.assertEqual(message['embeds'][0]['title'], 'team/repo · Recent activity')
        self.assertIn('[ISSUE #1](https://discord.com/channels/guild/thread) · New comment', message['embeds'][0]['description'])
        AlertFeed(api, dict(config, language='ko'), self.queue).topic(self.topic, 'issue_comment', payload, 'event1', 'thread')
        self.assertEqual(len(api.posts), 1)
        self.assertEqual(message['allowed_mentions'], {'parse': []})

    def test_thread_and_detail_use_saved_language_while_comment_stays_original(self):
        api = FakeDiscord()
        threads = CachedThreads(api, 'g', {'issue': 'c'}, self.queue, 'en')
        threads.bot = {'id': 'bot'}
        payload = {'action': 'created', 'comment': {'body': '원문 댓글 @everyone'}, 'sender': {'login': 'author'}}
        embed = make_embed(self.topic, 'issue_comment', payload, 'en')
        thread = threads.deliver(self.topic, embed, 'event', lambda: False)
        self.assertIn('New comment', api.posts[thread][0]['embeds'][0]['title'])
        self.assertIn('원문 댓글 @everyone', api.posts[thread][0]['embeds'][0]['description'])
        self.assertEqual(next(iter(api.roots.values()))['embeds'][0]['author']['name'],'GitHub')

    def test_ci_alert_and_pr_failure_are_english(self):
        api = FakeAlertApi()
        feed = AlertFeed(api, {'guild_id': 'guild', 'alert_channel': 'alerts', 'language': 'en'}, self.queue)
        payload = {'workflow_run': {'id': 1, 'event': 'push', 'conclusion': 'failure', 'name': 'Build', 'html_url': 'https://github.com/run'}}
        feed.ci('team/repo', payload, 'event')
        self.assertIn('failed CI run', api.posts[0]['content'])
        self.assertIn('View run on GitHub', api.posts[0]['embeds'][0]['description'])
        self.assertIn('CI Failed', make_embed(self.topic, 'workflow_run', payload, 'en')['title'])
        self.assertNotIn('실패', json.dumps(make_embed(self.topic, 'workflow_run', payload, 'en'), ensure_ascii=False))

    def test_review_notice_translates_bot_prefix_only(self):
        payload = {'action': 'submitted', 'review': {'state': 'approved', 'body': '검증 완료'}}
        en = make_embed(self.topic, 'pull_request_review', payload, 'en')
        ko = make_embed(self.topic, 'pull_request_review', payload)
        self.assertIn('Approved', en['title'])
        self.assertIn('Review: approved', en['description'])
        self.assertIn('검증 완료', en['description'])
        self.assertIn('리뷰 결과: approved', ko['description'])

    def test_korean_guidance_keeps_english_developer_status(self):
        from test_alert_feed import FakeAlertApi
        api=FakeAlertApi()
        feed=AlertFeed(api,{'guild_id':'guild','alert_channel':'alerts','language':'ko'},self.queue)
        feed.topic(self.topic,'pull_request',{'action':'closed','pull_request':{'merged':True}},'technical-status','thread')
        content=api.posts[0]['content']
        self.assertIn('새 소식을 가져왔어요',content)
        self.assertIn('Merged',content)
        self.assertNotIn('병합',content)



class ReviewRequestBodyTests(unittest.TestCase):
    def test_review_request_events_keep_reviewer_without_repeating_description(self):
        topic = Topic('team/repo', 'pr', 7, 'PR', 'https://github.com/team/repo/pull/7')
        for action in ('review_requested', 'review_request_removed'):
            payload = {'action': action, 'pull_request': {'body': 'long unchanged description'}, 'requested_reviewer': {'login': 'reviewer'}}
            result = make_embed(topic, 'pull_request', payload, 'en')
            self.assertNotIn('long unchanged description', result['description'])
            self.assertIn('reviewer', result['description'])
        payload['action'] = 'edited'
        self.assertIn('long unchanged description', make_embed(topic, 'pull_request', payload, 'en')['description'])

    def test_new_commit_keeps_pr_topic_and_links_compare_without_repeating_body(self):
        from discord_threads import route
        from unittest.mock import Mock
        payload = {'repository': {'full_name': 'team/repo'}, 'number': 7, 'action': 'synchronize',
                   'before': 'a' * 40, 'after': 'b' * 40,
                   'pull_request': {'number': 7, 'title': 'PR', 'html_url': 'https://github.com/team/repo/pull/7', 'body': 'unchanged body'}}
        github = Mock()
        topics = route('pull_request', payload, github)
        self.assertEqual([(t.kind, t.number) for t in topics], [('pr', 7)])
        result = make_embed(topics[0], 'pull_request', payload, 'en')
        self.assertIn('/compare/' + 'a' * 40 + '...' + 'b' * 40, result['description'])
        self.assertNotIn('unchanged body', result['description'])
