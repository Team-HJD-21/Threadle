import copy
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from delivery import Queue
from discord_threads import Api, marker
from message_tracking import MessageTracking, bind, clean
from discord_threads import Topic, nonce
from message_edits import MutableMessages
from types import SimpleNamespace


def message_id():
    return str(int((time.time() - 1420070400) * 1000) << 22)


class TrackingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(Path(self.temp.name) / 'queue.sqlite3')
        self.store = MessageTracking(self.queue)
        self.store.set_owner('900')
        self.payload = {'embeds': [{'title': 'A comment', 'url': 'https://github.com/owner/repo/issues/1#issuecomment-20',
                                   'description': 'Keep this visible.',
                                   'footer': {'text': 'github-event:receipt\ngithub-source:comment:page:0\ngithub-source:comment:revision:10.0'}}]}

    def received(self, payload, identity=None, owner='900'):
        return dict(copy.deepcopy(payload), id=identity or message_id(), author={'id': owner})

    def test_transport_hides_markers_but_recovers_them_after_restart(self):
        api = Api('Discord', 'https://discord.invalid', 'test-token', 'Bot')
        bind(api, self.queue)
        requests = []
        saved = {}
        def respond(request, timeout):
            if request.method == 'POST':
                requests.append(json.loads(request.data))
                saved.update(self.received(requests[-1]))
            response = io.BytesIO(json.dumps(saved).encode())
            response.headers = {}
            return response
        with patch('discord_threads.urlopen', side_effect=respond):
            sent = api.call('POST', '/channels/123/messages', self.payload)
            self.assertNotIn('footer', requests[0]['embeds'][0])
            self.assertEqual('Keep this visible.', requests[0]['embeds'][0]['description'])
            self.assertEqual(['github-event:receipt'], marker(sent))
            restarted = Api('Discord', 'https://discord.invalid', 'test-token', 'Bot')
            bind(restarted, self.queue)
            fetched = restarted.call('GET', '/channels/123/messages/' + saved['id'])
        self.assertEqual(self.payload['embeds'][0]['footer'], fetched['embeds'][0]['footer'])

    def test_crash_after_post_is_recovered_from_committed_expected_payload(self):
        wire, operation = self.store.prepare('POST', '/channels/123/messages', self.payload)
        received = self.received(wire)
        # Simulate process death before remember() or source_pages was committed.
        restarted = MessageTracking(self.queue)
        recovered = restarted.restore('/channels/123/messages?limit=100', [received], None)[0]
        self.assertEqual(['github-event:receipt'], marker(recovered))
        self.assertIn('github-source:comment:revision:10.0', recovered['embeds'][0]['footer']['text'])
        with self.queue.connect() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM pending_message_markers').fetchone()[0])

    def test_recovery_never_adopts_another_author_or_an_old_identical_alert(self):
        wire, _ = self.store.prepare('POST', '/channels/123/messages', self.payload)
        human = self.received(wire, owner='901')
        old = self.received(wire, identity=str(int((time.time() - 3600 - 1420070400) * 1000) << 22))
        for row in (human, old):
            self.assertNotIn('footer', self.store.restore('/channels/123/messages?limit=100', [row], None)[0]['embeds'][0])

    def test_ambiguous_unmapped_sources_are_not_guessed(self):
        wire, _ = self.store.prepare('POST', '/channels/123/messages', self.payload)
        second = copy.deepcopy(self.payload)
        second['embeds'][0]['footer']['text'] = 'github-event:other\ngithub-source:other:page:0'
        self.store.prepare('POST', '/channels/123/messages', second)
        recovered = self.store.restore('/channels/123/messages?limit=100', [self.received(wire)], None)[0]
        self.assertNotIn('footer', recovered['embeds'][0])

    def test_patch_crash_recovers_new_revision_even_with_old_mapping(self):
        wire, op = self.store.prepare('POST', '/channels/123/messages', self.payload)
        original = self.received(wire)
        self.store.remember(op, original)
        changed = copy.deepcopy(self.payload)
        changed['embeds'][0]['description'] = 'Edited comment.'
        changed['embeds'][0]['footer']['text'] = 'github-event:new\ngithub-source:comment:page:0\ngithub-source:comment:revision:11.0'
        wire, _ = self.store.prepare('PATCH', '/channels/123/messages/' + original['id'], changed)
        received = self.received(wire, identity=original['id'])
        recovered = MessageTracking(self.queue).restore('/channels/123/messages/' + original['id'], received, None)
        self.assertEqual(['github-event:new'], marker(recovered))
        self.assertIn('revision:11.0', recovered['embeds'][0]['footer']['text'])

    def test_known_page_mapping_cannot_be_stolen_by_identical_pending_post(self):
        first = copy.deepcopy(self.payload)
        first['embeds'][0].pop('url')
        first['embeds'][0].pop('description')
        first['embeds'][0]['image'] = {'url': 'https://example.com/same-image.png'}
        wire, op = self.store.prepare('POST', '/channels/123/messages', first)
        known = self.received(wire)
        self.store.remember(op, known)
        second = copy.deepcopy(first)
        second['embeds'][0]['footer']['text'] = 'github-event:second\ngithub-source:other:page:1'
        self.store.prepare('POST', '/channels/123/messages', second)
        recovered = self.store.restore('/channels/123/messages?limit=100', [known], None)[0]
        self.assertEqual(['github-event:receipt'], marker(recovered))
        with self.queue.connect() as db:
            self.assertEqual(1, db.execute('SELECT count(*) FROM pending_message_markers').fetchone()[0])

    def test_nonce_disambiguates_identical_pending_pages_and_mismatch_is_rejected(self):
        first = copy.deepcopy(self.payload)
        first['nonce'] = 'first-nonce'
        wire, _ = self.store.prepare('POST', '/channels/123/messages', first)
        second = copy.deepcopy(first)
        second['nonce'] = 'second-nonce'
        second['embeds'][0]['footer']['text'] = 'github-event:second\ngithub-source:other:page:0'
        self.store.prepare('POST', '/channels/123/messages', second)
        self.assertNotIn('footer', self.store.restore('/channels/123/messages', [self.received(dict(wire, nonce='unrelated'))], None)[0]['embeds'][0])
        recovered = self.store.restore('/channels/123/messages', [self.received(dict(wire, nonce='second-nonce'))], None)[0]
        self.assertEqual(['github-event:second'], marker(recovered))

    def test_orphan_post_without_nonce_does_not_adopt_a_future_identical_message(self):
        wire, _ = self.store.prepare('POST', '/channels/123/messages', self.payload)
        with self.queue.connect() as db:
            db.execute('UPDATE pending_message_markers SET created=?', (time.time() - 3600,))
        received = self.received(wire)
        self.assertNotIn('footer', self.store.restore('/channels/123/messages', [received], None)[0]['embeds'][0])

    def test_unmapped_image_only_post_requires_nonce(self):
        payload = {'nonce': 'image-page', 'embeds': [{'image': {'url': 'https://example.com/image.png'}, 'footer': {'text': 'github-event:image-page'}}]}
        wire, _ = self.store.prepare('POST', '/channels/123/messages', payload)
        received = self.received(wire)
        received.pop('nonce')
        self.assertNotIn('footer', self.store.restore('/channels/123/messages', [received], None)[0]['embeds'][0])
        received['nonce'] = 'image-page'
        self.assertEqual(['github-event:image-page'], marker(self.store.restore('/channels/123/messages', [received], None)[0]))

    def test_retained_attachment_patch_recovers_new_revision(self):
        payload = copy.deepcopy(self.payload)
        payload['embeds'][0]['image'] = {'url': 'attachment://threadle-image-0.png'}
        wire, op = self.store.prepare('POST', '/channels/123/messages', payload)
        received = self.received(wire)
        image_url = 'https://cdn.discordapp.com/attachments/123/456/threadle-image-0.png?ex=signed'
        received['embeds'][0]['image'] = {'url': image_url, 'width': 10, 'height': 10}
        received['attachments'] = [{'id': '456', 'filename': 'threadle-image-0.png', 'url': image_url}]
        self.store.remember(op, received)
        changed = copy.deepcopy(payload)
        changed['embeds'][0]['image'] = {'url': image_url}
        changed['embeds'][0]['footer']['text'] = 'github-event:attachment-edit\ngithub-source:comment:page:0\ngithub-source:comment:revision:12.0'
        changed['attachments'] = [{'id': '456'}]
        self.store.prepare('PATCH', '/channels/123/messages/' + received['id'], changed)
        recovered = self.store.restore('/channels/123/messages/' + received['id'], received, None)
        self.assertEqual(['github-event:attachment-edit'], marker(recovered))

    def test_legacy_visible_markers_and_human_footer_remain_supported(self):
        legacy = self.received(self.payload)
        self.assertEqual(self.payload['embeds'][0]['footer'], self.store.restore('/channels/123/messages/' + legacy['id'], legacy, None)['embeds'][0]['footer'])
        mixed = copy.deepcopy(self.payload)
        mixed['embeds'][0]['footer']['text'] += '\n7 remaining issues…'
        sanitized, _ = clean(mixed)
        self.assertEqual('7 remaining issues…', sanitized['embeds'][0]['footer']['text'])
        self.assertIn('github-event:', self.payload['embeds'][0]['footer']['text'])

    def test_attachment_response_recovers_expected_marker(self):
        payload = copy.deepcopy(self.payload)
        payload['embeds'][0]['image'] = {'url': 'attachment://threadle-image-0.png'}
        wire, _ = self.store.prepare('POST', '/channels/123/messages', payload)
        received = self.received(wire)
        received['embeds'][0]['type'] = 'rich'
        received['embeds'][0]['image'] = {'url': 'https://cdn.discordapp.com/attachments/123/456/threadle-image-0.png', 'width': 10, 'height': 10}
        received['attachments'] = [{'filename': 'threadle-image-0.png', 'url': received['embeds'][0]['image']['url']}]
        recovered = self.store.restore('/channels/123/messages?limit=100', [received], None)[0]
        self.assertEqual(['github-event:receipt'], marker(recovered))

    def test_delete_removes_mapping(self):
        wire, op = self.store.prepare('POST', '/channels/123/messages', self.payload)
        received = self.received(wire)
        self.store.remember(op, received)
        self.store.deleted('/channels/123/messages/' + received['id'])
        with self.queue.connect() as db:
            self.assertEqual(0, db.execute('SELECT count(*) FROM hidden_message_markers').fetchone()[0])

    def test_mutable_comment_page_recovers_and_deletes_without_visible_markers(self):
        topic = Topic('owner/repo', 'issue', 1, 'A topic', 'https://github.com/owner/repo/issues/1')
        kind = 'comment:issue_comment:20'
        source = 'github-source:' + nonce(topic.key + ':' + kind)
        payload = copy.deepcopy(self.payload)
        payload['embeds'][0]['footer']['text'] = 'github-event:receipt\n' + source + ':page:0\n' + source + ':revision:10.0'
        api = Api('Discord', 'https://discord.invalid', 'test-token', 'Bot')
        bind(api, self.queue)
        stored = {}
        def respond(request, timeout):
            if request.method == 'POST':
                data = json.loads(request.data)
                self.assertNotIn('footer', data['embeds'][0])
                stored.update(self.received(data))
                result = stored
            elif request.method == 'DELETE':
                stored.clear()
                result = None
            elif '?' in request.full_url:
                result = [stored] if stored else []
            else:
                result = stored
            response = io.BytesIO(json.dumps(result).encode() if result is not None else b'')
            response.headers = {}
            return response
        with patch('discord_threads.urlopen', side_effect=respond):
            api.call('POST', '/channels/123/messages', payload)
            threads = SimpleNamespace(queue=self.queue, api=api, bot={'id': '900'},
                                      messages=lambda thread: api.call('GET', '/channels/' + thread + '/messages?limit=100'))
            mutable = MutableMessages(threads)
            version = mutable.recover(topic, source, '123', kind, {'html_url': payload['embeds'][0]['url']})
            self.assertEqual(10.0, version)
            with self.queue.connect() as db:
                self.assertEqual(stored['id'], db.execute('SELECT message FROM source_pages WHERE source=?', (source,)).fetchone()[0])
                db.execute('INSERT INTO links VALUES(?,?,?)', (topic.key, '111', '123'))
            mutable.delete(topic, kind, {'html_url': payload['embeds'][0]['url']})
            self.assertEqual({}, stored)


if __name__ == '__main__':
    unittest.main()
