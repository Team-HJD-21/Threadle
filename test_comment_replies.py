import copy
import hashlib
import hmac
import json
from unittest import TestCase
from unittest.mock import Mock

from delivery import CachedThreads
from discord_threads import ApiError, Topic, nonce
from message_edits import MutableMessages
from review_conversations import ReviewConversations
import test_accounts as account_fixtures
from test_review_conversations import comment, conversation, response
from test_service import config


class ReplyTests(TestCase):
    link = account_fixtures.AccountTests.link
    job = account_fixtures.AccountTests.job

    def setUp(self):
        account_fixtures.AccountTests.setUp(self)
        self.messages = {}
        self.deleted = []
        self.nonces = {}
        self.seq = 9100
        self.forbid_delete = False
        self.lose_patch = False
        self.fail_patch = False
        self.reply_comments = []
        self.quote_body = 'Original **GitHub** text.'
        self.current = [conversation(comments=[comment(10, 'Please explain this.')])]
        self.current[0]['comments']['nodes'][0]['author']['databaseId'] = 99
        self.topic_obj = Topic('one/repo', 'pr', 7, 'Title', 'https://github.com/one/repo/pull/7', {'number': 7, 'title': 'Title', 'state': 'open'})
        self.messages[('7777', '9001')] = {'id': '9001', 'author': {'id': '9000', 'bot': True},
            'embeds': [{'title': 'PR #7 · Review thread', 'description': 'Old card', 'url': 'https://github.com/one/repo/pull/7#discussion_r10', 'color': 1}]}
        self.messages[('1002', '7777')] = {'id': '7777', 'author': {'id': '9000', 'bot': True},
            'embeds': [{'title': 'PR #7 · Title', 'url': self.topic_obj.url, 'description': '**Current Status** `Open`', 'footer': {'text': self.topic}}]}
        self.threads = CachedThreads(self.api, '1000', self.binding.config['channels'], self.service.queue(self.binding))
        self.threads.bot = {'id': '9000', 'bot': True}
        MutableMessages(self.threads)
        ReviewConversations(self.threads, self.github)
        with self.service.queue(self.binding).connect() as db:
            source = 'github-source:' + nonce(self.topic + ':comment:pull_request_review_comment:10')
            db.execute('INSERT INTO message_sources VALUES(?,?,?,?)', (source, '7777', 1, 'seed'))
            db.execute('INSERT INTO source_pages VALUES(?,?,?,?)', (source, 0, '9001', None))
            db.execute('INSERT INTO review_conversations VALUES(?,?,?)', (self.topic, 'T1', '10'))
        self.service.github = lambda binding: self.github
        self.link()

    def discord_call(self, method, path, data=None):
        if path == '/users/@me':
            return {'id': '9000', 'bot': True}
        if '/messages?' in path:
            channel = path.split('/')[2]
            return [copy.deepcopy(value) for (parent, _), value in self.messages.items() if parent == channel]
        if '/reactions/' in path:
            return None
        if '/messages/' in path:
            channel, identity = path.split('/')[2], path.rsplit('/', 1)[1]
            value = self.messages.get((channel, identity))
            if value is None:
                raise ApiError('Discord', 404, 10008)
            if method == 'GET':
                return copy.deepcopy(value)
            if method == 'PATCH':
                if self.fail_patch:
                    self.fail_patch = False
                    raise ApiError('Discord', 'network')
                value.update(copy.deepcopy(data))
                if self.lose_patch:
                    self.lose_patch = False
                    raise ApiError('Discord', 'network')
                return copy.deepcopy(value)
            if method == 'DELETE':
                if self.forbid_delete:
                    raise ApiError('Discord', 403)
                self.deleted.append(identity)
                del self.messages[(channel, identity)]
                return None
        if method == 'POST' and path.endswith('/messages'):
            key = path, str(data.get('nonce', ''))
            if key in self.nonces:
                return self.nonces[key]
            self.seq += 1
            value = dict(copy.deepcopy(data), id=str(self.seq), author={'id': '9000', 'bot': True})
            self.messages[(path.split('/')[2], value['id'])] = value
            self.nonces[key] = value
            return copy.deepcopy(value)
        if method == 'GET' and path == '/channels/7777':
            return {'id': '7777', 'guild_id': '1000', 'parent_id': '1002', 'type': 11, 'owner_id': '9000', 'name': 'PR #7', 'thread_metadata': {}}
        if method == 'PATCH' and path == '/channels/7777':
            return {'id': '7777'}
        return account_fixtures.AccountTests.discord_call(self, method, path, data)

    def github_call(self, method, path, data=None):
        if path == '/user/92':
            return {'id': 92, 'login': 'renamed-github-user'}
        if path.startswith('/repos/one/repo/issues/comments/'):
            identity = int(path.rsplit('/', 1)[1])
            return {'id': identity, 'issue_url': 'https://api.github.com/repos/one/repo/issues/7', 'body': self.quote_body, 'user': {'id': 99, 'login': 'reviewer'}}
        if path == '/repos/one/repo/pulls/comments/10':
            return {'id': 10, 'pull_request_url': 'https://api.github.com/repos/one/repo/pulls/7', 'body': 'Root', 'user': {'id': 99, 'login': 'reviewer'}}
        if path == '/repos/one/repo/pulls/7':
            return dict(self.topic_obj.metadata, html_url=self.topic_obj.url, head={'ref': 'feature'}, base={'ref': 'main'})
        if path.startswith('/repos/one/repo/pulls/7/comments?'):
            return copy.deepcopy(self.reply_comments)
        if method == 'POST' and path == '/repos/one/repo/pulls/7/comments/10/replies':
            self.writes.append(dict(data, path=path))
            value = {'id': 44, 'body': data['body'], 'user': {'id': 91, 'login': 'alice'}, 'in_reply_to_id': 10,
                     'html_url': 'https://github.com/one/repo/pull/7#discussion_r44'}
            self.reply_comments.append(value)
            return copy.deepcopy(value)
        if path == '/graphql' and 'reviewThreads' in data['query']:
            return response(self.current)
        result = account_fixtures.AccountTests.github_call(self, method, path, data)
        if method == 'POST' and path.endswith('/comments'):
            self.writes[-1]['path'] = path
        return result

    def send(self, reference='9001', identity='8000', content='My reply', mentions=None):
        value = dict(self.message, id=identity, content=content, reference={'message_id': reference, 'channel_id': '7777', 'guild_id': '1000', 'type': 0})
        if mentions is not None:
            value['mentions'] = mentions
        self.messages[('7777', identity)] = {'id': identity, 'content': content, 'attachments': [], 'author': {'id': '55'}}
        self.assertTrue(self.bridge.ingest(value))
        self.bridge.tick()

    def reflect(self, actor=91, node='T1'):
        value = self.reply_comments[-1]
        reply = comment(44, value['body'])
        reply['author'] = {'login': 'alice', 'databaseId': actor}
        reply['replyTo'] = {'databaseId': 10}
        self.current[0]['id'] = node
        self.current[0]['comments']['nodes'].append(reply)

    def webhook(self, actor=91, feed=None):
        value = copy.deepcopy(self.reply_comments[-1])
        value['user']['id'] = actor
        payload = {'action': 'created', 'repository': {'id': 11, 'full_name': 'one/repo'},
                   'installation': {'id': 101}, 'pull_request': self.topic_obj.metadata, 'comment': value}
        body = json.dumps(payload).encode()
        signature = 'sha256=' + hmac.new(b'app', body, hashlib.sha256).hexdigest()
        self.assertEqual(self.service.receive('/github/app-webhook', signature, 'pull_request_review_comment', 'b' * 32, body), 202)
        self.service.dispatch()
        self.assertTrue(self.service.process(self.binding, self.threads, feed))

    def retry_webhook(self):
        with self.service.queue(self.binding).connect() as db:
            db.execute('UPDATE jobs SET next=0 WHERE done=0')
        self.assertTrue(self.service.process(self.binding, self.threads, None))

    def flags(self):
        with self.accounts.db() as db:
            return db.execute('SELECT mode,applied,cleaned FROM bridge_replies').fetchone()

    def quote_card(self):
        self.messages[('7777', '9002')] = {'id': '9002', 'author': {'id': '9000', 'bot': True},
            'embeds': [{'title': 'PR #7 · New comment', 'url': 'https://github.com/one/repo/pull/7#issuecomment-20'}]}

    def test_ordinary_reply_posts_a_markdown_quote_and_keeps_the_input(self):
        self.quote_card()
        self.send('9002')
        self.assertEqual(self.writes[0]['path'], '/repos/one/repo/issues/7/comments')
        self.assertIn('> [reviewer](https://github.com/one/repo/pull/7#issuecomment-20)', self.writes[0]['body'])
        self.assertIn('> Original **GitHub** text.', self.writes[0]['body'])
        self.assertIn('\n\nMy reply', self.writes[0]['body'])
        self.assertEqual(self.flags()[0], 'quote')
        self.assertFalse(self.deleted)

    def test_a_quoted_previous_receipt_does_not_confuse_echo_detection(self):
        self.quote_card()
        self.quote_body += self.bridge.marker('a' * 32)
        self.send('9002')
        body = self.writes[0]['body']
        self.assertEqual(body.count('threadle-comment:'), 1)
        self.assertNotIn('a' * 32, body)

    def test_review_reply_uses_actual_review_endpoint_and_waits_for_card_update(self):
        self.send()
        self.assertEqual(self.writes[0]['path'], '/repos/one/repo/pulls/7/comments/10/replies')
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(self.flags(), ('review', 0, 0))
        self.bridge.tick()
        self.assertFalse(self.deleted)
        self.assertIn(('7777', '8000'), self.messages)

    def test_unknown_review_post_result_reconciles_without_a_second_write(self):
        original = self.github_call
        def call(method, path, data=None):
            result = original(method, path, data)
            if method == 'POST' and path.endswith('/replies'):
                raise ApiError('GitHub', 'network')
            return result
        self.github.call.side_effect = call
        self.send()
        self.assertEqual(self.job()['state'], 'uncertain')
        self.clock.return_value += 20
        self.bridge.tick()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(len(self.writes), 1)
        self.assertFalse(self.deleted)
        self.reflect()
        self.webhook()
        self.bridge.tick()
        self.assertEqual(self.deleted, ['8000'])

    def test_early_review_webhook_can_confirm_a_lost_post_response_safely(self):
        original = self.github_call
        def call(method, path, data=None):
            result = original(method, path, data)
            if method == 'POST' and path.endswith('/replies'):
                self.reflect()
                self.webhook()
                raise ApiError('GitHub', 'network')
            return result
        self.github.call.side_effect = call
        self.send()
        self.assertEqual(self.job()['state'], 'done')
        self.assertEqual(len(self.writes), 1)
        self.assertEqual(self.deleted, ['8000'])

    def test_webhook_edits_aggregate_before_deleting_only_the_input(self):
        self.send()
        self.reflect()
        self.webhook()
        self.assertEqual(self.flags(), ('review', 1, 0))
        card = self.messages[('7777', '9001')]
        self.assertIn('My reply', card['embeds'][0]['description'])
        self.assertNotIn('threadle-comment:', card['embeds'][0]['description'])
        self.assertFalse(self.deleted)
        self.bridge.tick()
        self.assertEqual(self.deleted, ['8000'])
        self.assertIn(('7777', '9001'), self.messages)
        writes = [(call.args[0], call.args[1]) for call in self.api.call.call_args_list]
        self.assertLess(writes.index(('PATCH', '/channels/7777/messages/9001')), writes.index(('DELETE', '/channels/7777/messages/8000')))
        self.assertEqual(len(self.writes), 1)

    def test_review_reply_refreshes_the_card_without_an_alert_echo_in_its_origin(self):
        from alert_policy import should_alert
        feed = Mock()
        self.send()
        self.reflect()
        self.webhook(feed=feed)
        feed.topic.assert_called_once()
        topic, event, payload, *_ = feed.topic.call_args.args
        self.assertFalse(should_alert(event, payload))
        self.assertIn('My reply', self.messages[('7777', '9001')]['embeds'][0]['description'])
        other = self.service.registry.get(self.service.registry.add('other', 11, 'one/repo', 101, 'app', config(2000)))
        value = copy.deepcopy(self.reply_comments[-1])
        foreign = {'action': 'created', 'pull_request': {'number': 7}, 'comment': value}
        self.assertFalse(self.bridge.echo(other, 'pull_request_review_comment', foreign))
        self.assertTrue(should_alert('pull_request_review_comment', foreign))

    def test_stale_snapshot_retries_without_deleting_input_or_losing_the_reply(self):
        self.send()
        self.webhook()
        self.assertEqual(self.flags(), ('review', 0, 0))
        self.assertFalse(self.deleted)
        with self.service.queue(self.binding).connect() as db:
            self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0], 0)
        self.reflect()
        self.retry_webhook()
        self.bridge.tick()
        self.assertEqual(self.deleted, ['8000'])
        self.assertIn('My reply', self.messages[('7777', '9001')]['embeds'][0]['description'])

    def test_lost_patch_response_preserves_input_until_retry_confirms_card(self):
        self.send()
        self.reflect()
        self.lose_patch = True
        self.webhook()
        self.assertEqual(self.flags(), ('review', 0, 0))
        self.bridge.tick()
        self.assertFalse(self.deleted)
        self.retry_webhook()
        self.bridge.tick()
        self.assertEqual(self.deleted, ['8000'])
        self.assertEqual(len(self.writes), 1)

    def test_missing_manage_messages_keeps_input_and_can_resume_after_permission_fix(self):
        self.send()
        self.reflect()
        self.webhook()
        self.forbid_delete = True
        self.bridge.tick()
        self.assertFalse(self.deleted)
        self.assertEqual(self.flags(), ('review', 1, 0))
        notices = [v for v in self.messages.values() if '메시지 관리' in v.get('content', '')]
        self.assertEqual(len(notices), 1)
        self.assertTrue(notices[0]['flags'] & 4096)
        self.bridge.tick()
        self.assertEqual(len([v for v in self.messages.values() if '메시지 관리' in v.get('content', '')]), 1)
        self.forbid_delete = False
        self.clock.return_value += 61
        self.bridge.tick()
        self.assertEqual(self.deleted, ['8000'])

    def test_edited_discord_input_is_never_deleted_as_an_already_mirrored_reply(self):
        self.send()
        self.reflect()
        self.webhook()
        self.messages[('7777', '8000')]['content'] = 'Edited text that was not posted'
        self.bridge.tick()
        self.assertFalse(self.deleted)
        self.assertIn(('7777', '8000'), self.messages)

    def test_disconnect_cancels_pending_cleanup_after_the_github_write(self):
        self.send()
        self.accounts.disconnect(discord='55')
        self.reflect()
        self.webhook()
        self.bridge.tick()
        self.assertFalse(self.deleted)
        self.assertIn(('7777', '8000'), self.messages)

    def test_another_guild_card_cannot_authorize_deleting_the_original_input(self):
        self.send()
        self.reflect()
        other = self.service.registry.get(self.service.registry.add('other', 11, 'one/repo', 101, 'app', config(2000)))
        context = self.bridge.replies.context(other)
        snapshot = copy.deepcopy(self.current[0])
        snapshot['comments'] = snapshot['comments']['nodes']
        clean = context.clean(self.topic_obj, snapshot)
        context.applied(self.topic_obj, clean, '8888')
        self.bridge.tick()
        self.assertEqual(self.flags(), ('review', 0, 0))
        self.assertFalse(self.deleted)

    def test_copied_marker_with_another_author_cannot_authorize_deletion(self):
        self.send()
        self.reflect(actor=92)
        self.webhook(actor=92)
        self.bridge.tick()
        self.assertEqual(self.flags(), ('review', 0, 0))
        self.assertFalse(self.deleted)

    def test_legacy_review_cards_without_page_mapping_use_scoped_github_lookup(self):
        with self.service.queue(self.binding).connect() as db:
            db.execute('DELETE FROM source_pages')
        self.send()
        self.assertEqual(self.writes[0]['path'], '/repos/one/repo/pulls/7/comments/10/replies')

    def test_discord_only_messages_are_not_quoted_into_github(self):
        self.messages[('7777', '9003')] = {'id': '9003', 'author': {'id': '56'}, 'content': 'Discord-only private detail'}
        self.send('9003')
        self.assertEqual(self.job()['state'], 'failed')
        self.assertFalse(self.writes)
        self.assertFalse(self.deleted)

    def test_reply_to_a_previously_forwarded_human_message_quotes_github(self):
        original = dict(self.message, id='8000', content='Earlier message')
        self.messages[('7777', '8000')] = {'id': '8000', 'content': 'Earlier message', 'author': {'id': '55'}, 'attachments': []}
        self.bridge.ingest(original)
        self.bridge.tick()
        self.send('8000', identity='8001', content='Follow-up')
        self.assertEqual(len(self.writes), 2)
        self.assertIn('> Original **GitHub** text.', self.writes[1]['body'])
        self.assertIn('Follow-up', self.writes[1]['body'])
        self.assertFalse(self.deleted)

    def test_issue_comment_quotes_keep_the_correct_item_kind(self):
        self.topic = 'github-thread:one/repo:issue:7'
        with self.service.queue(self.binding).connect() as db:
            db.execute('UPDATE links SET topic=?', (self.topic,))
        self.quote_card()
        self.messages[('7777', '9002')]['embeds'][0]['url'] = 'https://github.com/one/repo/issues/7#issuecomment-20'
        original = self.github_call
        self.github.call.side_effect = lambda method, path, data=None: {'number': 7, 'locked': False} if method == 'GET' and path == '/repos/one/repo/issues/7' else original(method, path, data)
        self.send('9002')
        self.assertEqual(self.job()['state'], 'done')
        self.assertIn('/issues/7#issuecomment-20', self.writes[0]['body'])

    def test_discussion_comment_quotes_are_resolved_under_the_same_repository(self):
        self.topic = 'github-thread:one/repo:discussion:7'
        with self.service.queue(self.binding).connect() as db:
            db.execute('UPDATE links SET topic=?', (self.topic,))
        self.quote_card()
        url = 'https://github.com/one/repo/discussions/7#discussioncomment-20'
        self.messages[('7777', '9002')]['embeds'][0]['url'] = url
        original = self.github_call
        def call(method, path, data=None):
            if path == '/graphql' and 'discussion(number:' in data['query']:
                return {'data': {'repository': {'databaseId': 11, 'discussion': {
                    'id': 'D1', 'number': 7, 'locked': False, 'body': 'Description', 'url': url.split('#')[0], 'author': {'login': 'author'},
                    'comments': {'nodes': [{'id': 'DC20', 'databaseId': 20, 'body': 'Discussion quote', 'url': url, 'author': {'login': 'reviewer'},
                        'replies': {'nodes': [], 'pageInfo': {'hasNextPage': False}}}], 'pageInfo': {'hasNextPage': False}}}}}}
            return original(method, path, data)
        self.github.call.side_effect = call
        self.send('9002')
        self.assertEqual(self.job()['state'], 'done')
        self.assertIn('> Discussion quote', self.writes[0]['variables']['input']['body'])

    def test_a_foreign_issue_url_cannot_be_quoted_under_the_current_pr(self):
        self.quote_card()
        original = self.github_call
        self.github.call.side_effect = lambda method, path, data=None: {'id': 20, 'body': 'Other private issue', 'issue_url': 'https://api.github.com/repos/one/repo/issues/8'} if path == '/repos/one/repo/issues/comments/20' else original(method, path, data)
        self.send('9002')
        self.assertEqual(self.job()['state'], 'failed')
        self.assertFalse(self.writes)

    def test_another_bots_card_cannot_route_to_a_review_thread(self):
        self.messages[('7777', '9001')]['author']['id'] = '9999'
        self.send()
        self.assertEqual(self.job()['state'], 'failed')
        self.assertFalse(self.writes)

    def test_cross_channel_reference_is_rejected_before_enqueue(self):
        value = dict(self.message, reference={'message_id': '9001', 'channel_id': '8888', 'guild_id': '1000'})
        with self.assertRaises(ValueError):
            self.bridge.ingest(value)
        self.assertFalse(self.writes)

    def test_outgoing_real_mentions_use_current_github_login_even_if_incoming_pings_are_off(self):
        self.link(discord='56', profile={'id': 92, 'login': 'old-github-name'}, mentions=False)
        self.quote_card()
        self.send('9002', content='@양현석 <@56> `<@56>` ```text\n<@56>\n```', mentions=['56'])
        body = self.writes[0]['body']
        self.assertIn('@양현석 @renamed-github-user', body)
        self.assertIn('`<@56>`', body)
        self.assertIn('```text\n<@56>\n```', body)
        self.assertEqual(self.accounts.get('56')['login'], 'renamed-github-user')

    def test_unlinked_mentions_and_non_native_mentions_do_not_guess_another_identity(self):
        self.quote_card()
        self.send('9002', content='@양현석 <@56>', mentions=[])
        self.assertIn('@양현석 <@56>', self.writes[0]['body'])
        self.assertNotIn('@renamed-github-user', self.writes[0]['body'])

    def test_input_fingerprint_ignores_attachment_signatures_but_detects_replacement(self):
        value = {'content': 'body', 'attachments': [{'url': 'https://cdn.discordapp.com/attachments/1/a.png?sig=old', 'filename': 'a.png'}]}
        renewed = copy.deepcopy(value)
        renewed['attachments'][0]['url'] = 'https://cdn.discordapp.com/attachments/1/a.png?sig=new'
        self.assertEqual(self.bridge.replies.fingerprint(value), self.bridge.replies.fingerprint(renewed))
        renewed['content'] = 'edited'
        self.assertNotEqual(self.bridge.replies.fingerprint(value), self.bridge.replies.fingerprint(renewed))
