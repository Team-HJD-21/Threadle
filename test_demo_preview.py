import unittest
from html.parser import HTMLParser
from product_web import demonstration
from web_locale import LANGUAGE


class Markup(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.ids = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])


class PreviewTests(unittest.TestCase):
    def test_five_channels_each_have_three_panes_and_no_remote_requests(self):
        html = demonstration()
        self.assertEqual(html.count('class="preview-case '), 5)
        for pane in ['preview-sidebar', 'preview-main', 'preview-detail']:
            self.assertEqual(html.count('class="' + pane + '"'), 5)
        markup = Markup(html)
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        self.assertNotIn('<script', html)
        self.assertNotIn('https://', html)

    def test_english_preview_translates_messages(self):
        token = LANGUAGE.set('en')
        try:
            html = demonstration()
            self.assertIn('Delivery received!', html)
            self.assertNotIn('새 댓글', html)
        finally:
            LANGUAGE.reset(token)


    def test_ci_preview_shows_workflow_progress_and_success_without_comments(self):
        html = demonstration().split('class="preview-case preview-ci"',1)[1].split('class="preview-case preview-alert"',1)[0]
        self.assertIn('github-ci', html)
        self.assertIn('Run #128', html)
        self.assertIn('Threadle CI and deployment', html)
        self.assertIn('Run tests', html)
        self.assertIn('preview-ci-running', html)
        self.assertIn('preview-ci-success', html)
        self.assertNotIn('preview-thread-card', html)
        self.assertNotIn('[CI #', html)
        self.assertNotIn('새 댓글', html)
        self.assertNotIn('Reply in thread', html)

    def test_sidebar_navigation_matches_rotation_order_and_omits_unrelated_sections(self):
        html = demonstration()
        first = html.split('class="preview-case preview-pr"', 1)[1].split('</aside>', 1)[0]
        markup = Markup(first)
        for removed in ['preview-tabs', 'Server Boosts', 'TEXT CHANNELS', '>Events<']:
            self.assertNotIn(removed, html)
        positions = [first.index('data-preview-key="' + key + '"') for key in ['pr','issue','discussion','ci']]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('🔔 Threadle', first)

    def test_alert_shows_scheduled_digest_and_on_demand_summary(self):
        html = demonstration().split('class="preview-case preview-alert"',1)[1]
        self.assertIn('/summary', html)
        self.assertIn('일일 PR · Issue 현황', html)
        self.assertIn('Discussion · 1개 열림', html)
        self.assertIn('7 remaining issues', html)
        self.assertNotIn('preview-thread-card', html)

    def test_starter_states_use_actual_production_titles_for_both_languages(self):
        from demo_web import starter_states
        from root_status import decorate
        from discord_threads import Topic
        for kind,state in [('pr',{'merged':True}),('issue',{'state':'closed'}),('discussion',{'isAnswered':True})]:
            topic=Topic('TeamHJD/threadle',kind,1,'Example','',{})
            self.assertIn(decorate(topic,{},dict(title='Example',**state))['title'],starter_states(kind,'1','Example'))
        self.assertIn('<code>Answered</code>',starter_states('discussion','1','Example'))
        self.assertIn('New comment',demonstration())
