from html.parser import HTMLParser
from urllib.parse import urlsplit

from test_connect import WebFixture


class Structure(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.anchors, self.forms, self.inputs = [], [], [], []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a':
            self.anchors.append(attrs.get('href', ''))
        if tag == 'form':
            self.forms.append(attrs)
        if tag == 'input':
            self.inputs.append(attrs)


class ProductPageTests(WebFixture):
    def test_public_page_has_working_sections_and_existing_oauth_entry(self):
        status, headers, body = self.browser.request('GET', '/connect')
        self.assertEqual(status, 200)
        self.assertIn('property="og:image" content="https://notify.example/connect/assets/threadle-banner.png', body)
        self.assertIn('name="twitter:card" content="summary_large_image"', body)
        markup = Structure(body)
        self.assertEqual(len(markup.ids), len(set(markup.ids)))
        for href in markup.anchors:
            if href.startswith('#'):
                self.assertIn(href[1:], markup.ids)
        self.assertEqual({f['action'] for f in markup.forms}, {'/connect/discord/start'})
        self.assertTrue(all(f['method'].lower() == 'post' for f in markup.forms))
        self.assertTrue(all(i['value'] == self.session().data['csrf'] for i in markup.inputs if i.get('name') == 'csrf'))
        self.assertIn('작동 예시입니다.', body)
        self.assertEqual(len([i for i in markup.inputs if i.get('type') == 'radio']), 5)
        self.assertIn('preview-detail', body)
        self.assertNotIn('href="#preview"', body)
        self.assertEqual(body.count('class="site-footer"'), 1)
        self.assertIn('style-src \'self\'', headers['Content-Security-Policy'])
        self.assertEqual(urlsplit(self.post('/connect/discord/start')[1]['Location']).netloc, 'discord.com')

    def test_english_page_has_natural_copy_and_no_unconfirmed_price(self):
        body = self.browser.request('GET', '/connect?lang=en')[2]
        self.assertIn('One PR. One thread.', body)
        self.assertIn('Illustrative preview.', body)
        self.assertNotIn('id="faq"', body)
        self.assertNotIn('₩4,900', body)
        self.assertNotIn('GitHub 활동을', body)
        self.assertNotIn('개발 소식을 한곳에.', body)
        self.assertNotIn('href="/checkout', body)

    def test_separate_public_pages_have_brand_metadata_and_preserve_language(self):
        for path in ('/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq'):
            status, headers, body = self.browser.request('GET', path + '?lang=en')
            self.assertEqual(status, 200, path)
            self.assertIn('<title>TeamHJD - Threadle | ', body)
            self.assertIn('name="robots" content="index,follow"', body)
            self.assertIn('rel="canonical"', body)
            self.assertIn('threadle-avatar.png', body)
            self.assertEqual(body.count('class="site-footer"'), 1)
        pricing = self.browser.request('GET', '/connect/pricing?lang=en')[2]
        self.assertIn('1 repository per Discord server', pricing)
        self.assertIn('5 repositories per Discord server', pricing)
        self.assertIn('planned launch offering', pricing)
        self.assertIn('button disabled', pricing)
        self.assertNotIn('lemonsqueezy.com/checkout', pricing)
        self.assertIn('Can I receive notifications in English?', self.browser.request('GET', '/connect/faq?lang=en')[2])

    def test_crawler_files_do_not_include_private_connection_routes(self):
        status, headers, body = self.browser.request('GET', '/connect/sitemap.xml')
        self.assertEqual(status, 200)
        self.assertIn('/connect/pricing', body)
        self.assertNotIn('/connect/server', body)
        self.assertNotIn('Set-Cookie', headers)
        self.assertIn('Disallow: /connect/server', self.browser.request('GET', '/robots.txt')[2])
        self.assertIn('noindex', self.browser.request('GET', '/connect/server')[1].get('X-Robots-Tag', ''))

    def test_public_css_is_served_without_exposing_other_files(self):
        result = self.browser.request('GET', '/connect/assets/landing.css?v=1')
        self.assertEqual(result[0], 200)
        self.assertIn('text/css', result[1]['Content-Type'])
        self.assertIn('@media', result[2])
        self.assertNotIn('Set-Cookie', result[1])
        for path in ('/connect/assets/landing_web.py', '/connect/assets/../runtime.json'):
            self.assertEqual(self.browser.request('GET', path)[0], 404)

    def test_new_connection_saves_language_but_visiting_does_not_change_it(self):
        self.browser.request('GET', '/connect?lang=en')
        self.configure()
        self.assertEqual(self.post('/connect/save')[0], 303)
        before = self.service.registry.active()[0]
        self.assertEqual(before.config['language'], 'en')
        self.browser.request('GET', '/connect?lang=ko')
        self.assertEqual(self.service.registry.get(before.id), before)

    def test_faq_support_and_updated_plan_comparison(self):
        from product_web import content,pricing
        from types import SimpleNamespace
        session=SimpleNamespace(data={'csrf':'test'})
        _,faq=content('/connect/faq',session)
        self.assertIn('https://discord.gg/6faZbNBdv',faq)
        self.assertIn('Threadle Support',faq)
        plans=pricing(session)
        self.assertEqual(plans.count('class="plan-actions"'),2)
        self.assertIn('PR 충돌·해결 알림',plans)
