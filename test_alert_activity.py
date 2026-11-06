import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from alert_activity import ActivityCards
from alert_feed import AlertFeed
from discord_threads import ApiError
from delivery import Queue
from test_alert_feed import FakeAlertApi
from alert_feed import closure_details
from discord_threads import Topic
from team_notifications import TeamNotifications
from test_team_features import binding


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name) / 'queue.sqlite3')
        self.api = FakeAlertApi()

    def tearDown(self):
        self.temp.cleanup()

    def cards(self, channel='alerts'):
        return ActivityCards(AlertFeed(self.api, {'alert_channel': channel}, self.queue), 'owner/repo')

    def item(self, number, event='a', activity='New commits'):
        return (event, 'pr:' + str(number), f'PR #{number} · {activity}', '',
                'https://discord.com/channels/10/' + str(number), 1)

    def test_first_event_is_immediate_later_topics_and_activities_edit_one_card(self):
        cards = self.cards()
        cards.update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        cards.update([self.item(2), self.item(1, 'b', 'New comment')], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        body = self.api.posts[0]['embeds'][0]['description']
        self.assertIn('[PR #1](https://discord.com/channels/10/1) · New commits, New comment', body)
        self.assertIn('[PR #2](https://discord.com/channels/10/2)', body)
        self.assertEqual(self.api.posts[0]['content'], '')
        self.assertEqual(self.api.posts[0]['allowed_mentions'], {'parse': []})
        self.assertEqual(sum(method == 'PATCH' for method, _, _ in self.api.calls), 1)

    def test_duplicate_types_and_replays_after_restart_do_not_add_lines_or_cards(self):
        self.cards().update([self.item(1), self.item(1, 'duplicate-delivery')], 'live:10')
        self.cards().update([self.item(1)], 'live:11')
        self.assertEqual(len(self.api.posts), 1)
        self.assertEqual(self.api.posts[0]['embeds'][0]['description'].count('New commits'), 1)
        self.assertFalse(any(method == 'PATCH' for method, _, _ in self.api.calls))
        self.cards().update([self.item(2)], 'live:11')
        self.assertEqual(len(self.api.posts), 2)

    def test_lost_post_then_new_event_recovers_and_updates_the_original_card(self):
        self.api.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            self.cards().update([self.item(1)], 'live:10')
        self.cards().update([self.item(2)], 'live:10')
        self.cards().update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #1', self.api.posts[0]['embeds'][0]['description'])
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_failed_patch_is_retried_without_posting_a_duplicate(self):
        self.cards().update([self.item(1)], 'live:10')
        call = self.api.call
        def fail(method, path, data=None):
            if method == 'PATCH':
                raise RuntimeError('network unavailable')
            return call(method, path, data)
        with patch.object(self.api, 'call', side_effect=fail), self.assertRaises(RuntimeError):
            self.cards().update([self.item(2)], 'live:10')
        self.cards().update([self.item(2)], 'live:11')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_deleted_activity_card_is_recreated_only_for_new_activity(self):
        self.cards().update([self.item(1)], 'live:10')
        self.api.posts.clear()
        call = self.api.call
        def gone(method, path, data=None):
            if method == 'PATCH':
                raise ApiError('Discord', 404, 10008)
            return call(method, path, data)
        with patch.object(self.api, 'call', side_effect=gone):
            self.cards().update([self.item(2)], 'live:10')
        self.assertEqual(len(self.api.posts), 1)
        self.assertIn('PR #1', self.api.posts[0]['embeds'][0]['description'])
        self.assertIn('PR #2', self.api.posts[0]['embeds'][0]['description'])

    def test_many_topics_have_bounded_pages_and_all_thread_links(self):
        self.cards().update([self.item(n) for n in range(1, 26)], 'batch:10')
        self.assertEqual(len(self.api.posts), 3)
        combined = '\n'.join(row['embeds'][0]['description'] for row in self.api.posts)
        for n in range(1, 26):
            self.assertIn('[' + f'PR #{n}' + ']', combined)
        for row in self.api.posts:
            self.assertLess(len(row['embeds'][0]['description'].encode('utf-16-le')) // 2, 4096)

    def test_same_events_are_independent_in_different_channels(self):
        for channel in ('alerts', 'ci'):
            self.cards(channel).update([self.item(1)], 'live:10')
        self.assertEqual(len(self.api.posts), 2)
        self.assertNotEqual(self.api.posts[0]['nonce'], self.api.posts[1]['nonce'])

    def test_parallel_topics_cannot_overwrite_each_other(self):
        cards = self.cards()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: cards.update([self.item(n)], 'live:10'), range(1, 6)))
        self.assertEqual(len(self.api.posts), 1)
        for n in range(1, 6):
            self.assertIn(f'PR #{n}', self.api.posts[0]['embeds'][0]['description'])


class LifecycleAlertTests(unittest.TestCase):
    setUp = ActivityTests.setUp
    tearDown = ActivityTests.tearDown

    def notices(self, now=1000, **options):
        config = binding(options)
        config.config.update(guild_id='guild', alert_channel='alerts')
        return TeamNotifications(config, self.queue, self.api, clock=lambda: now)

    def send(self, notices, number, kind='issue', state='Closed', actor='closer', event_key=None, channels=None, title=None):
        topic = Topic('owner/repo', kind, number, title or f'Fix item {number}',
                      f'https://github.com/owner/repo/{"pull" if kind == "pr" else "issues"}/{number}')
        obj = {'title': topic.title, 'state': 'closed', 'user': {'login': 'original-author'}}
        if state == 'Merged':
            obj.update(merged=True, merged_by={'login': actor})
        payload = {'action': 'closed', 'sender': {'login': actor}, 'pull_request' if kind == 'pr' else 'issue': obj}
        notices.topic(topic, 'pull_request' if kind == 'pr' else 'issues', payload,
                      event_key or str(number), 'thread-' + str(number), channels or ['alerts'])
        return topic, payload

    def test_closures_send_immediately_and_append_across_minutes_and_restart(self):
        self.send(self.notices(1019, batch_seconds=300), 442, actor='alice')
        self.assertEqual(len(self.api.posts), 1)
        self.send(self.notices(1081), 444, actor='bob')
        self.assertEqual(len(self.api.posts), 1)
        card = self.api.posts[0]['embeds'][0]
        self.assertIn('ISSUE Closed · 2건', card['title'])
        for number, actor in [(442, 'alice'), (444, 'bob')]:
            self.assertIn(f'**[ISSUE #{number}](https://discord.com/channels/guild/thread-{number})**', card['description'])
            self.assertIn(f'**Fix item {number}**', card['description'])
            self.assertIn('Closed by ' + actor, card['description'])
            self.assertIn(f'https://github.com/owner/repo/issues/{number}', card['description'])
        self.assertEqual(card['color'], 0x8957E5)
        self.assertEqual(self.api.posts[0]['content'], '')
        self.assertEqual(self.api.posts[0]['allowed_mentions'], {'parse': []})
        self.assertEqual(sum(method == 'PATCH' for method, _, _ in self.api.calls), 1)

    def test_pr_merges_pr_closures_and_issue_closures_have_separate_cards(self):
        notices = self.notices()
        self.send(notices, 1, 'pr', 'Merged')
        self.send(notices, 2, 'pr', 'Merged')
        self.send(notices, 3, 'pr')
        self.send(notices, 4)
        self.assertEqual(len(self.api.posts), 3)
        self.assertIn('PR Merged · 2건', self.api.posts[0]['embeds'][0]['title'])
        self.assertEqual(self.api.posts[0]['embeds'][0]['color'], 0x8957E5)
        self.assertIn('PR Closed · 1건', self.api.posts[1]['embeds'][0]['title'])
        self.assertEqual(self.api.posts[1]['embeds'][0]['color'], 0xD1242F)

    def test_legacy_feed_uses_actual_closer_and_merger_not_original_author(self):
        for kind, state, field in [('issue', 'Closed', 'closed_by'), ('pr', 'Closed', 'closed_by'), ('pr', 'Merged', 'merged_by')]:
            with self.subTest(kind=kind, state=state):
                topic = Topic('owner/repo', kind, 10, 'Old title', 'https://github.com/owner/repo/issues/10')
                obj = {'title': 'Actual title', field: {'login': 'actual-actor'}, 'user': {'login': 'original-author'}}
                if state == 'Merged':
                    obj['merged_at'] = '2026-11-07T00:00:00Z'
                payload = {'action': 'closed', 'sender': {'login': 'delivery-bot'}, 'issue' if kind == 'issue' else 'pull_request': obj}
                feed = AlertFeed(self.api, {'guild_id': 'guild', 'alert_channel': 'alerts', 'language': 'en'}, self.queue)
                feed.topic(topic, 'threadle_bundle', {'_events': [{'event': 'issues' if kind == 'issue' else 'pull_request_target', 'payload': payload}]}, kind + state, 'thread')
                body = self.api.posts[-1]['embeds'][0]['description']
                self.assertIn('Actual title', body)
                self.assertIn(state + ' by actual-actor', body)
                self.assertNotIn('delivery-bot', body)
                self.assertNotIn('original-author', body)

    def test_replay_after_new_window_never_moves_or_repeats_an_old_closure(self):
        self.send(self.notices(1000), 1)
        self.send(self.notices(1301), 2)
        self.assertEqual(len(self.api.posts), 2)
        self.send(self.notices(1310), 1)
        self.send(self.notices(1320), 3)
        self.assertEqual(len(self.api.posts), 2)
        self.assertNotIn('ISSUE #1', self.api.posts[1]['embeds'][0]['description'])
        self.assertIn('ISSUE #2', self.api.posts[1]['embeds'][0]['description'])
        self.assertIn('ISSUE #3', self.api.posts[1]['embeds'][0]['description'])

    def test_continuous_closures_rotate_after_fifteen_minutes(self):
        for n, now in enumerate(range(1000, 2001, 200)):
            self.send(self.notices(now), n)
        self.assertEqual(len(self.api.posts), 2)
        self.assertIn('ISSUE #5', self.api.posts[1]['embeds'][0]['description'])

    def test_retry_recovers_lost_post_and_failed_patch_without_duplicates(self):
        self.api.accept_then_fail = True
        with self.assertRaises(RuntimeError):
            self.send(self.notices(1000), 1)
        self.send(self.notices(1020), 2)
        self.send(self.notices(1030), 1)
        self.assertEqual(len(self.api.posts), 1)
        original = self.api.call
        def fail_patch(method, path, data=None):
            if method == 'PATCH':
                raise RuntimeError('network unavailable')
            return original(method, path, data)
        with patch.object(self.api, 'call', side_effect=fail_patch), self.assertRaises(RuntimeError):
            self.send(self.notices(1040), 3)
        self.send(self.notices(1500), 3)
        self.assertEqual(len(self.api.posts), 1)
        for number in range(1, 4):
            self.assertIn(f'ISSUE #{number}', self.api.posts[0]['embeds'][0]['description'])

    def test_large_burst_paginates_with_all_titles_actors_and_links(self):
        from notification_text import units
        notices = self.notices()
        for number in range(1, 14):
            self.send(notices, number, title='Title ' + '😀' * 200 + ' @everyone [text]')
        self.assertGreater(len(self.api.posts), 1)
        body = '\n'.join(post['embeds'][0]['description'] for post in self.api.posts)
        for number in range(1, 14):
            self.assertEqual(body.count(f'**[ISSUE #{number}]'), 1)
            self.assertIn(f'/issues/{number})', body)
        for post in self.api.posts:
            self.assertLessEqual(units(post['embeds'][0]['description']), 3500)

    def test_different_destinations_and_repositories_never_share_closure_cards(self):
        from dataclasses import replace
        notices = self.notices()
        self.send(notices, 1, channels=['alerts', 'ci'])
        other = self.notices()
        other.binding = replace(other.binding, repo='other/repo')
        topic = Topic('other/repo', 'issue', 1, 'Other repository', 'https://github.com/other/repo/issues/1')
        other.topic(topic, 'issues', {'action': 'closed', 'sender': {'login': 'other-actor'}}, 'other', 'other-thread', ['alerts'])
        self.assertEqual(len(self.api.posts), 3)
        self.assertIn('other/repo', self.api.posts[-1]['embeds'][0]['title'])
        self.assertNotIn('Other repository', self.api.posts[0]['embeds'][0]['description'])

    def test_missing_actor_is_not_guessed_and_later_reopen_is_not_reported_as_closed(self):
        topic = Topic('owner/repo', 'issue', 1, 'Title', 'url')
        closed = {'action': 'closed', 'issue': {'user': {'login': 'author'}}}
        self.assertEqual(closure_details(topic, 'issues', closed)['actor'], '')
        self.assertIsNone(closure_details(topic, 'threadle_bundle', {'_events': [
            {'event': 'issues', 'payload': closed}, {'event': 'issues', 'payload': {'action': 'reopened'}}]}))

    def test_closure_during_dnd_stays_in_the_existing_end_of_dnd_roundup(self):
        notices = self.notices(23 * 3600, quiet=True, quiet_start=22, quiet_end=8, utc_offset=0)
        self.send(notices, 1)
        self.assertEqual(self.api.posts, [])
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM dnd_activity WHERE done=0').fetchone()[0], 1)

    def test_parallel_closures_share_one_card_without_lost_items(self):
        notices = self.notices()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda n: self.send(notices, n), range(1, 6)))
        self.assertEqual(len(self.api.posts), 1)
        for n in range(1, 6):
            self.assertIn(f'**[ISSUE #{n}]', self.api.posts[0]['embeds'][0]['description'])

    def test_existing_activity_schema_and_normal_comments_keep_working(self):
        with self.queue.connect() as db:
            db.executescript('''
                CREATE TABLE activity_events(channel TEXT,event TEXT,topic TEXT,window TEXT,PRIMARY KEY(channel,event,topic));
                CREATE TABLE activity_items(channel TEXT,window TEXT,topic TEXT,label TEXT,activities TEXT,url TEXT,PRIMARY KEY(channel,window,topic));
                CREATE TABLE activity_pages(channel TEXT,window TEXT,page INTEGER,digest TEXT,PRIMARY KEY(channel,window,page));
                INSERT INTO activity_events VALUES('alerts','old','old-topic','live:old');
                INSERT INTO activity_items VALUES('alerts','live:old','old-topic','PR #99','["New comment"]','old-link');
            ''')
        notices = self.notices()
        self.send(notices, 1)
        topic = Topic('owner/repo', 'issue', 1, 'Title', 'https://github.com/owner/repo/issues/1')
        notices.topic(topic, 'issue_comment', {'action': 'created'}, 'comment', 'thread-1', ['alerts'])
        self.assertEqual(len(self.api.posts), 2)
        self.assertIn('New comment', self.api.posts[-1]['embeds'][0]['description'])
        self.assertIn('최근 활동', self.api.posts[-1]['embeds'][0]['title'])
        with self.queue.connect() as db:
            self.assertEqual(db.execute("SELECT label FROM activity_items WHERE window='live:old'").fetchone()[0], 'PR #99')
