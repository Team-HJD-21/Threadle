import json
import unittest
from unittest.mock import Mock,patch
from alert_feed import AlertFeed
from discord_threads import Topic
from repository_summary import summary_item,summary_pages,pr_counts
from team_notifications import TeamNotifications
import test_team_features as team_fixture
from test_team_features import binding
from test_alert_feed import FakeAlertApi
from notification_text import units


class DigestReadabilityTests(unittest.TestCase):
    setUp=team_fixture.NotificationTests.setUp
    tearDown=team_fixture.NotificationTests.tearDown

    def test_entries_only_link_numbers_keep_literal_titles_and_stay_together(self):
        entries=[summary_item('owner/repo','pr',{'number':n,'title':'feat(ui): '+('long title '*18)+'(mobile) @everyone','requested_reviewers':[{'login':'reviewer'}]},False) for n in range(1,31)]
        pages=summary_pages(entries,pr_counts(30,30,0))
        self.assertGreater(len(pages),1)
        self.assertTrue(all(units(page)<=2700 for page in pages))
        for entry in entries:self.assertEqual(sum(entry in page for page in pages),1)
        self.assertIn('**[PR #1](https://github.com/owner/repo/pull/1)** · `리뷰 대기`\nfeat(ui):',pages[0])
        self.assertNotIn('[#1 feat',pages[0])

    def test_digest_and_reminder_share_layout_and_issue_count_is_localized(self):
        for en in (False,True):
            with self.subTest(en=en):
                self.api.messages=[]
                b=binding({'digest':'daily','digest_hour':0,'utc_offset':0,'enabled_at':1})
                b.config['language']='en' if en else 'ko'
                notices=TeamNotifications(b,self.queue,self.api,clock=lambda:2000000000+(86400 if en else 0))
                prs=[{'number':626,'title':'feat(encounter): actual PR title','requested_reviewers':[{'login':'reviewer'}]}]
                issues=[{'number':406,'title':'A literal [title] (with brackets)','url':'https://github.com/owner/repo/issues/406'}]
                with patch('team_notifications.open_prs',return_value=prs),patch('team_notifications.open_issue_digest',return_value=(50,issues)):notices.digest(Mock())
                cards=self.api.messages[0][1]['embeds']
                self.assertIn('**[PR #626]',cards[0]['description'])
                self.assertIn('\nfeat(encounter): actual PR title',cards[0]['description'])
                self.assertIn('**[Issue #406]',cards[1]['description'])
                self.assertIn('50',cards[1]['footer']['text'])
                self.assertIn('showing 1 oldest' if en else '오래된 순 1개 표시',cards[1]['footer']['text'])

    def test_merge_alert_includes_title_and_actual_merger_in_both_delivery_paths(self):
        topic=Topic('owner/repo','pr',626,'Old title','https://github.com/owner/repo/pull/626')
        payload={'action':'closed','sender':{'login':'webhook-bot'},'pull_request':{'title':'feat(encounter): actual PR title','merged':True,'merged_by':{'login':'actual-merger'}}}
        for language in ('ko','en'):
            b=binding();b.config['language']=language
            notices=TeamNotifications(b,self.queue,self.api,clock=lambda:2000000000)
            notices.topic(topic,'pull_request',payload,'merge-'+language,'thread',['104'])
            card=self.api.messages[-1][1]['embeds'][0]
            self.assertEqual(card['title'],'PR #626 · Merged')
            self.assertIn('**feat(encounter): actual PR title**',card['description'])
            self.assertIn('Merged by actual-merger',card['description'])
            self.assertNotIn('webhook-bot',card['description'])
        api=FakeAlertApi();feed=AlertFeed(api,{'guild_id':'guild','alert_channel':'alerts'},self.queue)
        feed.topic(topic,'threadle_bundle',{'_events':[{'event':'pull_request','payload':payload}]},'legacy-merge','thread')
        self.assertIn('actual PR title',api.posts[-1]['embeds'][0]['description'])
        self.assertIn('Merged by actual-merger',api.posts[-1]['embeds'][0]['description'])
