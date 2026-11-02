import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from delivery import Queue, CachedThreads
from discord_threads import Topic
from pr_routing import classify, FileIndex, PrRouter, directory_tree
from registry import Binding
from team_notifications import TeamNotifications, allowed_time
from test_connect import WebFixture
from test_discord_threads import FakeDiscord
from service import Service


def groups():
    return [{'id': 'a' * 16, 'name': 'Frontend', 'paths': ['web/'], 'channel': '201'},
            {'id': 'b' * 16, 'name': 'Backend', 'paths': ['api/'], 'channel': '202'}]


def binding(options=None):
    config = {'guild_id': '10', 'channels': {'issue': '101', 'pr': '102', 'discussion': '103'},
              'alert_channel': '104', 'routing_groups': groups(), 'team_notifications': options or {}}
    return Binding('binding', 'tenant', 11, 'owner/repo', 1, 'app', config, False)


class FilesApi:
    def __init__(self, files):
        self.files, self.calls = files, []
        self.pr = {'head': {'sha': 'a' * 40}, 'base': {'sha': 'b' * 40}, 'changed_files': len(files)}

    def call(self, method, path):
        self.calls.append(path)
        if '/files?' in path:
            page = int(path.split('page=')[-1])
            return copy.deepcopy(self.files[(page-1)*100:page*100])
        return copy.deepcopy(self.pr)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index = FileIndex(self.root / 'files.sqlite3')
        self.queue = Queue(self.root / 'queue.sqlite3')
        self.router = PrRouter(self.queue, self.index)
        self.topic = Topic('owner/repo', 'pr', 7, 'A PR', 'https://github.com/owner/repo/pull/7')

    def tearDown(self):
        self.temp.cleanup()

    def test_paths_match_at_directory_boundaries_and_root_is_explicit(self):
        matches, unmatched = classify(['website/a.js', 'README.md'], groups())
        self.assertEqual(matches, {})
        self.assertTrue(unmatched)
        root = [dict(groups()[0], paths=['@root'])]
        self.assertIn('a' * 16, classify(['README.md'], root)[0])

    def test_single_area_mixed_area_and_unassigned_files_choose_expected_channels(self):
        for files, expected in [(['web/a'], '201'), (['web/a', 'api/b'], '102'), (['web/a', 'README.md'], '102')]:
            with self.subTest(files=files):
                topic = Topic('owner/repo', 'pr', len(files) * 100 + len(files[-1]), 'PR', 'https://github.com/owner/repo/pull/1')
                api = FilesApi([{'filename': p} for p in files])
                self.assertEqual(self.router.resolve(binding(), topic, 'pull_request', {'action': 'opened'}, api)[0], expected)

    def test_new_commits_add_recipients_without_moving_thread_and_comments_do_not_fetch_files(self):
        api = FilesApi([{'filename': 'web/a'}])
        first = self.router.resolve(binding(), self.topic, 'pull_request', {'action': 'opened'}, api)
        self.assertEqual(first[0], '201')
        api.files = [{'filename': 'api/b'}]
        api.pr['head']['sha'] = 'c' * 40
        second = self.router.resolve(binding(), self.topic, 'pull_request', {'action': 'synchronize'}, api)
        self.assertEqual(second[0], '201')
        self.assertEqual({g['channel'] for g in second[1]}, {'201', '202'})
        count = len(api.calls)
        self.router.resolve(binding(), self.topic, 'issue_comment', {}, api)
        self.assertEqual(len(api.calls), count)

    def test_renamed_file_considers_old_and_new_area(self):
        api = FilesApi([{'filename': 'api/a', 'previous_filename': 'web/a', 'status': 'renamed'}])
        self.assertEqual(self.router.resolve(binding(), self.topic, 'pull_request', {'action': 'opened'}, api)[0], '102')

    def test_file_cache_is_shared_but_scoped_to_repository_and_snapshot(self):
        api = FilesApi([{'filename': 'web/a'}])
        self.index.files(binding(), 7, api)
        self.index.files(binding(), 7, api)
        self.assertEqual(sum('/files?' in p for p in api.calls), 1)
        other = Binding('other', 'tenant', 12, 'other/repo', 1, 'app', {}, False)
        self.index.files(other, 7, api)
        self.assertEqual(sum('/files?' in p for p in api.calls), 2)

    def test_partial_file_results_are_not_used_for_routing(self):
        api = FilesApi([{'filename': 'web/a'}])
        api.pr['changed_files'] = 2
        with self.assertRaises(RuntimeError):
            self.router.resolve(binding(), self.topic, 'pull_request', {'action': 'opened'}, api)
        api.pr['changed_files'] = 3001
        self.assertEqual(self.router.resolve(binding(), self.topic, 'pull_request', {'action': 'opened'}, api)[0], '102')

    def test_existing_thread_location_survives_first_rule_configuration(self):
        with self.queue.connect() as db:
            db.execute('INSERT INTO links VALUES(?,?,?)', (self.topic.key, '102', '999'))
        self.assertEqual(self.router.resolve(binding(), self.topic, 'pull_request', {}, FilesApi([{'filename': 'web/a'}]))[0], '102')
        api = Mock()
        api.call.return_value = {'id': '999', 'parent_id': '102'}
        cached = CachedThreads(api, '10', {'pr': '201'}, self.queue)
        self.assertEqual(cached.ensure(self.topic)['id'], '999')
        self.assertFalse(any(c.args[0] == 'POST' for c in api.call.call_args_list))

    def test_directory_browsing_is_lazy_and_uses_a_fixed_tree_snapshot(self):
        users, data = Mock(), {}
        users.github.side_effect = [{'sha': 'root', 'tree': [{'type': 'tree', 'path': 'web', 'sha': 'websha'}]},
                                    {'sha': 'websha', 'tree': [{'type': 'tree', 'path': 'src', 'sha': 'srcsha'}]}]
        self.assertEqual(directory_tree(users, 'token', 'owner/repo', data), ['web/'])
        self.assertEqual(directory_tree(users, 'token', 'owner/repo', data, 'web/'), ['web/src/'])
        directory_tree(users, 'token', 'owner/repo', data)
        self.assertEqual(users.github.call_count, 2)
        with self.assertRaises(ValueError):
            directory_tree(users, 'token', 'owner/repo', data, '../secret/')


class MessageApi:
    def __init__(self):
        self.messages = []

    def call(self, method, path, data=None):
        if method == 'POST':
            result = dict(data, id=str(len(self.messages)+1))
            self.messages.append((path, result))
            return result
        if '/messages?' in path:
            return [message for channel, message in reversed(self.messages) if channel == path.split('?')[0]]
        return {'id': path.split('/')[2], 'guild_id': '10', 'type': 0}


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.queue = Queue(Path(self.temp.name) / 'queue.sqlite3')
        self.api = MessageApi()
        self.now = [dt.datetime(2026, 10, 31, 0, 0, tzinfo=dt.timezone.utc).timestamp()]
        self.topic = Topic('owner/repo', 'pr', 7, 'PR', 'https://github.com/owner/repo/pull/7')

    def tearDown(self):
        self.temp.cleanup()

    def notices(self, **options):
        return TeamNotifications(binding(options), self.queue, self.api, clock=lambda: self.now[0])

    def test_fanout_receipts_are_distinct_and_retry_does_not_duplicate(self):
        notices = self.notices()
        for _ in range(2):
            notices.topic(self.topic, 'issue_comment', {}, 'event1', 'thread', ['201', '202'])
        self.assertEqual(len(self.api.messages), 2)
        self.assertNotEqual(self.api.messages[0][1]['nonce'], self.api.messages[1][1]['nonce'])
        self.assertTrue(all(m['allowed_mentions'] == {'parse': []} for _, m in self.api.messages))

    def test_batches_survive_restart_and_include_multiple_events_once(self):
        notices = self.notices(batch_seconds=60)
        notices.topic(self.topic, 'pull_request', {'action': 'synchronize'}, '1', 'thread', ['201'])
        notices.topic(self.topic, 'issue_comment', {}, '2', 'thread', ['201'])
        self.assertEqual(len(self.api.messages), 0)
        self.now[0] += 61
        restarted = self.notices(batch_seconds=60)
        restarted.flush()
        restarted.flush()
        self.assertEqual(len(self.api.messages), 1)
        self.assertIn('2건', self.api.messages[0][1]['embeds'][0]['title'])

    def test_one_ci_event_can_notify_multiple_prs_in_the_same_channel(self):
        notices = self.notices(batch_seconds=60)
        second = Topic('owner/repo', 'pr', 8, 'Other PR', 'https://github.com/owner/repo/pull/8')
        for topic in (self.topic, second):
            notices.topic(topic, 'issue_comment', {}, 'same-event', 'thread', ['104'])
        self.now[0] += 61
        notices.flush()
        self.assertEqual(len(self.api.messages), 2)

    def test_non_pr_ci_failure_bypasses_dnd(self):
        self.now[0] += 23 * 3600
        notices = self.notices(quiet=True, quiet_start=22, quiet_end=8, utc_offset=0)
        notices.ci('owner/repo', {'workflow_run': {'id': 1, 'event': 'push', 'conclusion': 'failure', 'name': 'Build', 'html_url': 'https://github.com/owner/repo/actions/runs/1'}}, 'ci1')
        self.assertEqual(len(self.api.messages), 1)
        self.now[0] += 9 * 3600
        notices.flush()
        self.assertEqual(len(self.api.messages), 1)

    def test_ci_detail_and_failure_alert_are_immediate_during_dnd(self):
        self.now[0] += 23 * 3600
        notices = self.notices(quiet=True, quiet_start=22, quiet_end=8, utc_offset=0)
        notices.binding.config['ci_channel'] = '105'
        payload = {'workflow_run': {'id': 1, 'event': 'workflow_dispatch', 'conclusion': 'failure',
                   'name': 'Deploy', 'html_url': 'https://github.com/owner/repo/actions/runs/1'}}
        notices.ci('owner/repo', payload, 'ci1')
        notices.ci('owner/repo', payload, 'ci1')
        self.assertEqual([path for path, _ in self.api.messages], ['/channels/105/messages', '/channels/104/messages'])
        self.now[0] += 9 * 3600
        notices.flush()
        self.assertEqual([path for path, _ in self.api.messages], ['/channels/105/messages', '/channels/104/messages'])
        self.assertIn('/10/105', self.api.messages[1][1]['embeds'][0]['description'])

    def test_critical_events_skip_batching_but_observe_quiet_hours(self):
        notices = self.notices(batch_seconds=300)
        notices.topic(self.topic, 'pull_request', {'action': 'closed'}, 'close', 'thread', ['201'])
        self.assertEqual(len(self.api.messages), 1)
        options = {'quiet': True, 'quiet_start': 22, 'quiet_end': 8, 'utc_offset': 0}
        self.now[0] += 23 * 3600
        due = allowed_time(self.now[0], options)
        self.assertEqual(dt.datetime.fromtimestamp(due, dt.timezone.utc).hour, 8)
        quiet = self.notices(**options)
        quiet.topic(self.topic, 'pull_request', {'action': 'closed'}, 'quiet-close', 'thread', ['201'])
        self.assertEqual(len(self.api.messages), 1)
        self.now[0] = due
        quiet.flush()
        self.assertEqual(len(self.api.messages), 2)

    def test_turning_off_batching_keeps_already_queued_notifications_deliverable(self):
        notices = self.notices(batch_seconds=60)
        notices.topic(self.topic, 'issue_comment', {}, 'queued', 'thread', ['104'])
        disabled = self.notices()
        self.assertTrue(disabled.has_pending())
        self.now[0] += 61
        disabled.flush()
        self.assertEqual(len(self.api.messages), 1)
        self.assertFalse(disabled.has_pending())

    def test_turning_off_review_reminders_clears_old_requests(self):
        notices = self.notices(review_hours=4)
        notices.observe(self.topic, 'pull_request', {'action': 'review_requested', 'requested_reviewer': {'login': 'dev'}}, 'request')
        self.notices()
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM review_waits').fetchone()[0], 0)

    def test_open_pr_reminder_repeats_without_assignee_and_survives_restart(self):
        notices = self.notices(review_hours=4, review_since=self.now[0])
        github = Mock()
        github.pages.return_value = [{'number':7, 'title':'Unassigned PR', 'state':'open'}]
        notices.reminders(github)
        self.now[0] += 4 * 3600
        notices.reminders(github)
        notices.reminders(github)
        self.assertEqual(len(self.api.messages), 1)
        restarted = self.notices(review_hours=4, review_since=self.now[0]-4*3600)
        restarted.reminders(github)
        self.assertEqual(len(self.api.messages), 1)
        self.now[0] += 4*3600
        restarted.reminders(github)
        self.assertEqual(len(self.api.messages), 2)
        github.pages.return_value = []
        self.now[0] += 4*3600
        restarted.reminders(github)
        self.assertEqual(len(self.api.messages), 2)

    def test_review_responses_and_drafts_cancel_waits(self):
        notices = self.notices(review_hours=4)
        payload = {'action': 'review_requested', 'requested_reviewer': {'login': 'reviewer'}}
        notices.observe(self.topic, 'pull_request', payload, 'a')
        notices.observe(self.topic, 'pull_request_review', {'action': 'submitted', 'review': {'user': {'login': 'reviewer'}}}, 'b')
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM review_waits').fetchone()[0], 0)
        notices.observe(self.topic, 'pull_request', payload, 'c')
        notices.observe(self.topic, 'pull_request', {'action': 'converted_to_draft'}, 'd')
        with self.queue.connect() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM review_waits').fetchone()[0], 0)

    def test_daily_digest_uses_current_open_prs_and_does_not_repeat_after_restart(self):
        notices = self.notices(digest='daily', digest_hour=9, utc_offset=9, enabled_at=self.now[0]-3600)
        github = Mock()
        github.pages.return_value = [{'number': 7, 'title': 'Ready', 'requested_reviewers': [{'login': 'dev'}]}, {'number': 8, 'title':'Unassigned draft', 'draft': True}]
        github.call.return_value = {'data':{'repository':{'oldest':{'totalCount':0,'nodes':[]},'completed':{'nodes':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
        notices.digest(github)
        self.notices(digest='daily', digest_hour=9, utc_offset=9, enabled_at=self.now[0]-3600).digest(github)
        self.assertEqual(len(self.api.messages), 1)
        self.assertEqual(github.call.call_count, 1)
        self.assertIn('현재 열린 PR 2개', self.api.messages[0][1]['embeds'][0]['description'])


class DeliveryIntegrationTests(unittest.TestCase):
    def test_role_thread_recovers_after_lost_cache_without_creating_another_thread(self):
        with tempfile.TemporaryDirectory() as temporary:
            service = Service(Path(temporary), {'DISCORD_BOT_TOKEN': 'unused'})
            config = binding().config
            identity = service.registry.add('tenant', 11, 'owner/repo', 1, 'app', config)
            current = service.registry.get(identity)
            fake = FakeDiscord()
            class Discord:
                def call(self, method, path, data=None):
                    result = fake.call(method, path.replace('/guilds/10/', '/guilds/g/').replace('/channels/201', '/channels/c'), data)
                    def translate(value):
                        if isinstance(value, dict):
                            return {k: '201' if k == 'parent_id' and v == 'c' else '10' if k == 'guild_id' and v == 'g' else translate(v) for k, v in value.items()}
                        return [translate(v) for v in value] if isinstance(value, list) else value
                    return translate(result)
            api = Discord()
            files = FilesApi([{'filename': 'web/a'}])
            files.pr.update({'number': 7, 'state': 'open', 'title': 'PR'})
            def github_call(method, path):
                return {'id': 11, 'full_name': 'owner/repo'} if path == '/repositories/11' else files.call(method, path)
            github = Mock()
            github.call.side_effect = github_call
            service.github = lambda _: github
            queue = service.queue(current)
            threads = CachedThreads(api, '10', config['channels'], queue)
            threads.bot = {'id': 'bot', 'bot': True}
            payload = {'repository': {'id': 11, 'full_name': 'owner/repo'}, 'installation': {'id': 1},
                       'action': 'opened', 'pull_request': files.pr}
            queue.put('one', 'pull_request', payload)
            self.assertTrue(service.process(current, threads, None, Mock()))
            self.assertIsNone(queue.next())
            self.assertEqual(fake.starts, 1)
            with queue.connect() as db:
                db.execute('DELETE FROM links')
            queue.put('two', 'pull_request_review_comment', dict(payload, action='created', comment={'body': 'Review comment'}))
            self.assertTrue(service.process(current, threads, None, Mock()))
            self.assertIsNone(queue.next())
            self.assertEqual(fake.starts, 1)


class TeamWebTests(WebFixture):
    def test_new_basic_connection_defaults_to_friday_without_migrating_old_schedules(self):
        self.configure()
        self.post('/connect/save')
        saved=self.service.registry.active()[0]
        self.assertEqual(saved.config['team_notifications']['digest_weekday'],4)
        old=dict(saved.config,team_notifications={'digest':'weekly','digest_hour':16,'digest_weekday':2})
        self.service.registry.configure(saved.id,old,'1','1')
        response=self.browser.request('GET','/connect/team?binding='+saved.id)
        self.assertEqual(response[0],200)
        self.assertEqual(self.service.registry.get(saved.id).config,old)
        # The settings form displays the legacy weekly hour without saving a migration.
        self.assertIn('name="weekly_digest_hour"',response[2])
        self.assertIn('value="16" selected',response[2])

    def setup_directory_api(self):
        self.users.github = Mock(side_effect=lambda token, path: {'sha': 'root', 'tree': [
            {'path': 'web', 'type': 'tree', 'sha': 'websha'}, {'path': 'api', 'type': 'tree', 'sha': 'apisha'}]})

    def add_area(self, name, folder):
        self.assertEqual(self.post('/connect/team/select', {'path0': folder})[0], 303)
        self.assertEqual(self.post('/connect/team/group', {'name': name})[0], 303)

    def test_optional_split_setup_creates_channels_only_when_applied(self):
        self.configure()
        self.setup_directory_api()
        self.assertEqual(self.browser.request('GET', '/connect/team')[0], 200)
        self.assertEqual(self.users.github.call_count, 0)
        self.assertEqual(self.post('/connect/team/mode', {'mode': 'on'})[0], 303)
        self.assertEqual(self.browser.request('GET', '/connect/team')[0], 200)
        self.add_area('Frontend', 'web/')
        self.add_area('Backend', 'api/')
        self.assertEqual(self.bot.created_payloads, [])
        self.assertEqual(self.post('/connect/team/apply')[0], 303)
        saved = self.service.registry.active()[0]
        self.assertEqual(len(saved.config['routing_groups']), 2)
        self.assertEqual(len(self.bot.created_payloads), 8)
        self.assertEqual(saved.config['team_notifications'], {'digest_weekday':4,'weekly_digest_hour':9})

    def test_existing_connection_can_enable_options_without_changing_binding_or_queue(self):
        self.configure()
        self.assertEqual(self.post('/connect/save')[0], 303)
        original = self.service.registry.active()[0]
        self.service.queue(original).put('pending', 'issues', {'old': True})
        preferences = self.browser.request('GET', '/connect/team?lang=en&binding=' + original.id)
        self.assertEqual(preferences[0], 200)
        self.assertIn('Notification language', preferences[2])
        self.assertIn('Titles and comments stay in their original language.', preferences[2])
        self.assertEqual(self.service.registry.get(original.id).config['language'], 'ko')
        fields = {'notification_language':'en', 'review_hours': '24', 'batch_seconds': '60', 'digest': 'daily', 'digest_hour': '9',
                  'digest_weekday': '0', 'utc_offset': '9', 'quiet_start': '22', 'quiet_end': '8'}
        self.assertEqual(self.post('/connect/team/options', fields)[0], 303)
        self.assertEqual(self.post('/connect/team/apply')[0], 303)
        updated = self.service.registry.active()[0]
        self.assertEqual(updated.id, original.id)
        self.assertEqual(updated.config['team_notifications']['review_hours'], 24)
        self.assertEqual(updated.config['language'], 'en')
        self.assertEqual(self.service.queue(updated).next()[0], 'pending')

    def test_apply_button_saves_current_form_values_and_can_be_used_again(self):
        self.configure()
        self.browser.request('GET', '/connect/team')
        response = self.post('/connect/team/options', {'apply': '1', 'review_hours': '12'})
        self.assertEqual(response[0], 303)
        self.assertEqual(response[1]['Location'], '/connect/done')
        done = self.browser.request('GET', response[1]['Location'])
        self.assertEqual(done[0], 200)
        self.assertIn('연결이 완료됐어요.', done[2])
        saved = self.service.registry.active()[0]
        self.assertEqual(saved.config['team_notifications']['review_hours'], 12)
        self.assertEqual(self.browser.request('GET', '/connect/team?binding=' + saved.id)[0], 200)
        updated = self.post('/connect/team/options', {'apply': '1', 'review_hours': '24'})
        self.assertEqual(updated[0], 303)
        self.assertEqual(updated[1]['Location'], '/connect/done')
        self.assertIn('연결이 완료됐어요.', self.browser.request('GET', updated[1]['Location'])[2])
        self.assertEqual(self.service.registry.get(saved.id).config['team_notifications']['review_hours'], 24)

    def test_stale_settings_cannot_overwrite_a_newer_saved_configuration(self):
        self.configure()
        self.post('/connect/save')
        saved = self.service.registry.active()[0]
        self.browser.request('GET', '/connect/team?binding=' + saved.id)
        newer = dict(saved.config, workflow_names=['New build'])
        self.service.registry.configure(saved.id, newer, '1', '1')
        self.assertEqual(self.post('/connect/team/options', {'apply': '1', 'review_hours': '12'})[0], 409)
        self.assertEqual(self.service.registry.get(saved.id).config['workflow_names'], ['New build'])

    def test_unseen_directories_and_invalid_times_are_rejected(self):
        self.configure()
        self.browser.request('GET', '/connect/team')
        self.assertEqual(self.post('/connect/team/select', {'path0': '../private/'})[0], 400)
        self.assertEqual(self.post('/connect/team/options', {'quiet': '1', 'quiet_start': '8', 'quiet_end': '8'})[0], 400)
        self.assertEqual(self.bot.created_payloads, [])

    def test_name_only_group_shows_directory_hint_and_preserves_name(self):
        self.configure()
        self.setup_directory_api()
        self.browser.request('GET', '/connect/team')
        self.post('/connect/team/mode', {'mode': 'on'})
        self.browser.request('GET', '/connect/team')
        result = self.post('/connect/team/group', {'name': '프론트'})
        self.assertEqual(result[0], 303, result[2])
        page = self.browser.request('GET', result[1]['Location'])[2]
        self.assertIn('실제 폴더를 하나 이상 체크', page)
        self.assertIn('value="프론트"', page)
        self.assertNotIn('입력이 올바르지 않거나 연결 정보가 변경됐습니다.', page)
        self.assertEqual(self.service.registry.active(), [])

    def test_checked_directories_can_be_added_with_name_without_a_selection_save(self):
        self.configure()
        self.setup_directory_api()
        self.browser.request('GET', '/connect/team')
        self.post('/connect/team/mode', {'mode': 'on'})
        self.browser.request('GET', '/connect/team')
        self.assertEqual(self.post('/connect/team/group', {'name': 'Frontend', 'path0': 'web/'})[0], 303)
        group = self.session().data['team_draft']['groups'][0]
        self.assertEqual(group['paths'], ['web/'])
        self.assertEqual(group['name'], 'Frontend')

    def test_english_preferences_and_choices_are_translated(self):
        self.configure()
        result = self.browser.request('GET', '/connect/team?lang=en')
        self.assertEqual(result[0], 200)
        self.assertIn('Team notifications', result[2])
        self.assertIn('Recurring open PR reminders', result[2])
        self.assertIn('Every 4 hours', result[2])
        self.assertNotIn('나누지 않기', result[2])

    def test_directory_browsing_keeps_name_and_selection_and_can_remove_a_chip(self):
        self.configure()
        self.setup_directory_api()
        self.browser.request('GET', '/connect/team')
        self.post('/connect/team/mode', {'mode': 'on'})
        self.browser.request('GET', '/connect/team')
        response = self.post('/connect/team/browse', {'folder': 'web/', 'name': 'UI', 'path0': 'api/'})
        self.assertEqual(response[0], 303)
        self.assertIn('folder=web%2F', response[1]['Location'])
        self.browser.request('GET', response[1]['Location'])
        draft = self.session().data['team_draft']
        self.assertEqual((draft['pending_name'], draft['selected']), ('UI', ['api/']))
        self.assertEqual(self.post('/connect/team/unselect', {'remove_path': 'api/', 'name': 'UI'})[0], 303)
        self.assertEqual(self.session().data['team_draft']['selected'], [])
        self.assertEqual(self.post('/connect/team/browse', {'folder': 'secret/'})[0], 400)
        self.assertEqual(self.bot.created_payloads, [])

    def test_preview_explains_mixed_paths_and_limits_rendered_file_list(self):
        self.configure()
        self.setup_directory_api()
        self.browser.request('GET', '/connect/team')
        self.post('/connect/team/mode', {'mode': 'on'})
        self.browser.request('GET', '/connect/team')
        self.add_area('Frontend', 'web/')
        files = ['web/' + str(n) + '.js' for n in range(105)] + ['README.md']
        self.service.github = Mock(return_value=FilesApi([{'filename': p} for p in files]))
        response = self.post('/connect/team/preview', {'number': '7'})
        self.assertEqual(response[0], 303)
        preview = self.session().data['team_draft']['preview']
        self.assertEqual((preview['destination'], preview['reason'], preview['total'], preview['unmatched']),
                         ('공통 PR 채널', '미분류 경로 포함', 106, 1))
        self.assertEqual(preview['groups'], [{'name': 'Frontend', 'count': 105}])
        self.assertEqual(len(preview['paths']), 100)
        body = self.browser.request('GET', response[1]['Location'])[2]
        self.assertIn('<details class="preview-files">', body)
        self.assertNotIn('<details class="preview-files" open', body)
        self.assertIn('어느 작업 영역에도 속하지 않은 경로', body)
        self.assertEqual(self.bot.created_payloads, [])

    def test_settings_script_is_local_and_other_source_files_cannot_be_downloaded(self):
        asset = self.browser.request('GET', '/connect/assets/team-settings.js')
        self.assertEqual(asset[0], 200)
        self.assertEqual(asset[1]['Content-Type'], 'text/javascript; charset=utf-8')
        self.assertEqual(asset[1]['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.browser.request('GET', '/connect/assets/../credentials.json')[0], 404)
        self.configure()
        response = self.browser.request('GET', '/connect/team')
        csp = response[1]['Content-Security-Policy']
        self.assertIn("script-src 'self'; connect-src 'self'", csp)
        self.assertNotIn("script-src 'unsafe-inline'", csp)
        self.assertIn("script-src 'self'", self.browser.request('GET', '/connect')[1]['Content-Security-Policy'])

    def test_other_server_connection_cannot_be_configured(self):
        self.configure()
        other = self.service.registry.add('other', 22, 'other/repo', 202, 'app',
                   {'guild_id': '20', 'channels': {'issue': '201', 'pr': '202', 'discussion': '203'}})
        self.assertEqual(self.browser.request('GET', '/connect/team?binding=' + other)[0], 403)

    def test_notification_language_survives_advanced_navigation_and_site_language_change(self):
        self.configure()
        self.assertEqual(self.post('/connect/team/start',{'notification_language':'en'})[0],303)
        self.assertEqual(self.session().data['team_draft']['language'],'en')
        self.browser.language='ko'
        self.assertEqual(self.post('/connect/team/options',{'apply':'1'})[0],303)
        saved=self.service.registry.active()[0]
        self.assertEqual(saved.config['language'],'en')
        self.browser.language='en'
        self.browser.request('GET','/connect/team?binding='+saved.id)
        self.assertEqual(self.post('/connect/team/options',{'apply':'1','batch_seconds':'60'})[0],303)
        self.assertEqual(self.service.registry.get(saved.id).config['language'],'en')

    def test_basic_connection_language_is_explicit_and_back_link_follows_advanced_card(self):
        self.configure()
        html=self.browser.request('GET','/connect/channels')[2]
        self.assertLess(html.index('알림을 더 세밀하게 설정할까요?'),html.index('저장소 다시 선택'))
        self.assertIn('formaction="/connect/team/start"',html)
        self.assertEqual(self.post('/connect/save',{'notification_language':'en'})[0],303)
        self.assertEqual(self.service.registry.active()[0].config['language'],'en')

