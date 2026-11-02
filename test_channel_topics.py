import json
import unittest
from unittest.mock import patch
import test_channel_layouts as fixtures
from discord_threads import ApiError


class TopicTests(unittest.TestCase):
    setUp=fixtures.ChannelLayoutTests.setUp
    tearDown=fixtures.ChannelLayoutTests.tearDown
    def test_saved_ids_have_human_topics_and_manual_edits_survive(self):
        config=self.layouts.ensure(self.bot,'10',11,'owner/repo')
        channel=self.bot.created[config['channels']['pr']]
        self.assertTrue(channel['topic'].startswith('Threadle ·'))
        self.assertNotIn('layout=',channel['topic'])
        channel['topic']='Our custom PR rules'
        self.layouts.clean_pending_topics(self.bot)
        self.layouts.ensure(self.bot,'10',11,'owner/repo')
        self.assertEqual(channel['topic'],'Our custom PR rules')
    def test_existing_old_markers_are_cleaned_only_when_mapping_matches(self):
        config=self.layouts.ensure(self.bot,'10',11,'owner/repo')
        with self.layouts.db() as db:
            data=json.loads(db.execute('SELECT data FROM layouts').fetchone()[0])
            db.execute('DELETE FROM topic_cleanup')
        channel=self.bot.created[config['channels']['pr']]
        channel['topic']='GitHub Notify managed channel | layout='+data['token']+'; kind=pr'
        other=self.bot.created[config['channels']['issue']]
        other['topic']='GitHub Notify managed channel | layout=someone-else; kind=issue'
        self.layouts.clean_pending_topics(self.bot)
        self.assertNotIn('layout=',channel['topic'])
        self.assertIn('someone-else',other['topic'])
    def test_failed_topic_cleanup_does_not_fail_connection_creation(self):
        original=self.bot.call
        def fail_topic(method,path,data=None):
            if method=='PATCH' and data and 'topic' in data:raise ApiError('Discord',403,50013)
            return original(method,path,data)
        with patch.object(self.bot,'call',side_effect=fail_topic):
            config=self.layouts.ensure(self.bot,'10',11,'owner/repo')
        self.assertTrue(config['alert_channel'])
        self.assertEqual(len(self.bot.created_payloads),6)
        with self.layouts.db() as db:db.execute('UPDATE topic_cleanup SET next=0')
        self.layouts.clean_pending_topics(self.bot)
        self.assertNotIn('layout=',self.bot.created[config['channels']['pr']]['topic'])
