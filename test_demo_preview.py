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
