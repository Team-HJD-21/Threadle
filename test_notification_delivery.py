import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from discord_threads import Threads,Topic
from alert_feed import AlertFeed
from delivery import Queue
from notification_media import media_embeds
from repository_summary import all_repository_summaries
from team_notifications import TeamNotifications
from test_team_features import binding

class DeliveryTests(unittest.TestCase):
    def test_same_event_in_two_servers_has_independent_root_and_detail_nonces(self):
        seen={};posts=[]
        def call(method,path,data):
            if data['nonce'] in seen:return seen[data['nonce']]
            row={'id':str(100+len(posts))};posts.append((path,data));seen[data['nonce']]=row;return row
        api=Mock();api.call.side_effect=call
        topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1')
        for channel in ('111','222'):
            threads=Threads(api,'guild',{'pr':channel});threads.bot={'id':'bot'}
            threads.find=Mock(return_value=None)
            threads.start=lambda channel,root,topic:{'id':root,'thread_metadata':{}}
            threads.delivered=Mock(return_value=False)
            threads.deliver(topic,{'description':'Content'},'same-webhook',lambda:False)
        self.assertEqual(len(posts),4)
        self.assertEqual(len(seen),4)
    def test_same_alert_event_has_a_different_nonce_per_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            api=Mock();api.call.side_effect=[{'id':'1'},{'id':'2'}]
            for channel in ('111','222'):
                feed=AlertFeed(api,{'alert_channel':channel},Queue(Path(folder)/(channel+'.db')))
                feed.send('topic','same-event','hello','title','description','https://github.com',1)
            calls=api.call.call_args_list
            self.assertNotEqual(calls[0].args[2]['nonce'],calls[1].args[2]['nonce'])
    def test_weekly_time_falls_back_to_existing_hour_and_respects_separate_hour(self):
        now=dt.datetime(2026,11,6,10,tzinfo=dt.timezone.utc).timestamp()
        with tempfile.TemporaryDirectory() as folder:
            for index,options,expected in [(0,{'digest_hour':9},1),(1,{'digest_hour':9,'weekly_digest_hour':17},0)]:
                options.update(digest='weekly',digest_weekday=4,utc_offset=0,enabled_at=now-86400)
                notices=TeamNotifications(binding(options),Queue(Path(folder)/(str(index)+'.db')),Mock(),clock=lambda:now)
                feed=Mock();notices.feed=Mock(return_value=feed)
                with patch('team_notifications.open_prs',return_value=[]),patch('team_notifications.open_issue_digest',return_value=(0,[])):
                    notices.digest(Mock())
                self.assertEqual(feed.send.call_count,expected)
    def test_settings_description_shows_effective_weekly_time(self):
        from command_jobs import CommandJobs
        text=CommandJobs.describe('owner/repo',{'digest':'weekly','digest_hour':9,'weekly_digest_hour':17,'digest_weekday':4})
        self.assertIn('On · Friday · 17:00',text)

    def test_summary_parentheses_have_no_visible_escape(self):
        item={'number':1,'title':'Fix (mobile) [screen]','createdAt':''}
        service=Mock()
        with patch('repository_summary.open_items',return_value=[item]):
            text=''.join(all_repository_summaries(service,[binding()]))
        self.assertIn('Fix (mobile)',text)
        self.assertNotIn(r'\(mobile\)',text)
    def test_markdown_preserves_code_and_standard_formatting(self):
        text='**Bold**\n# Heading\n- List\n```md\n![sample](https://example.com/code.png)\n- [x] literal\n```\n`<br>`'
        result=media_embeds({'description':text})
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['description'],text)
    def test_github_tasks_tables_details_and_real_image(self):
        text='- [x] done\n- [ ] next\n<details><summary>More</summary>Hello<br>World</details>\n| Name | State |\n| --- | --- |\n| Task | Done |\n![image](https://example.com/real.png)'
        result=media_embeds({'description':text})[0]
        self.assertIn('☑ done',result['description']);self.assertIn('☐ next',result['description'])
        self.assertIn('**More**',result['description']);self.assertIn('**Name**: Task',result['description'])
        self.assertEqual(result['image']['url'],'https://example.com/real.png')
