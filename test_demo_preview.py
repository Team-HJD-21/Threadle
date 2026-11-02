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
    def test_four_tabs_each_have_three_panes_and_no_remote_requests(self):
        html = demonstration()
        self.assertEqual(html.count('class="preview-case '), 4)
        for pane in ['preview-sidebar', 'preview-main', 'preview-detail']:
            self.assertEqual(html.count('class="' + pane + '"'), 4)
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
        html = demonstration().split('class="preview-case preview-ci"',1)[1]
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
