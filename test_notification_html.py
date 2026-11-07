import unittest

from notification_markdown import adapt
from notification_media import media_embeds
from notification_layout import message_pages
from notification_text import units


CLOUDFLARE = '''## Deploying gambungi-notes with &nbsp;<a href="https://pages.dev"><img src="https://example.com/cloudflare.png" width="32" alt="Cloudflare Pages"></a> &nbsp;Cloudflare Pages

<table><tr><td><strong>Latest commit:</strong></td><td>
<code>93cdd36</code>
</td></tr>
<tr><td><strong>Status:</strong></td><td>&nbsp;✅&nbsp; Deploy successful!</td></tr>
<tr><td><strong>Preview URL:</strong></td><td>
<a href='https://dd2702d4.gambungi-notes.pages.dev'>https://dd2702d4.gambungi-notes.pages.dev</a>
</td></tr>
<tr><td><strong>Branch Preview URL:</strong></td><td>
<a href='https://fix-italic-overflow-20261107.gambungi-notes.pages.dev'>https://fix-italic-overflow-20261107.gambungi-notes.pages.dev</a>
</td></tr>
</table>

[View logs](https://dash.cloudflare.com/logs)
'''


class HtmlNotificationTests(unittest.TestCase):
    def test_cloudflare_table_links_and_entities_are_readable_after_logo_split(self):
        cards = media_embeds({'title': 'PR #5 · New comment', 'description': CLOUDFLARE, 'color': 0x8B949E})
        body = '\n'.join(card.get('description', '') for card in cards)
        self.assertEqual(cards[0]['image']['url'], 'https://example.com/cloudflare.png')
        self.assertIn('**Latest commit:** `93cdd36`', body)
        self.assertIn('**Status:** ✅  Deploy successful!', body)
        for prefix in ('dd2702d4', 'fix-italic-overflow-20261107'):
            url = 'https://' + prefix + '.gambungi-notes.pages.dev'
            self.assertIn('[' + url + '](' + url + ')', body)
        self.assertIn('[View logs](https://dash.cloudflare.com/logs)', body)
        for raw in ('<a ', '</a>', '<table>', '<tr>', '<td>', '<code>', '&nbsp;', '<strong>'):
            self.assertNotIn(raw, body)

    def test_generic_header_table_preserves_every_column(self):
        body = adapt('<table><thead><tr><th>Name</th><th>Status</th><th>Logs</th></tr></thead><tbody>'
                     '<tr><td>Build</td><td><b>Passed</b></td><td><a href="https://example.com/log">View</a></td></tr>'
                     '<tr><td>Deploy</td><td>Running</td><td>None</td></tr></tbody></table>')
        self.assertIn('**Name**: Build · **Status**: **Passed** · **Logs**: [View](https://example.com/log)', body)
        self.assertIn('**Name**: Deploy', body)

    def test_code_examples_stay_literal_and_never_become_media(self):
        body = ('```html\n<table><tr><td>&nbsp;<a href="https://example.com">Link</a></td></tr></table>\n'
                '<img src="https://example.com/example.png">\n```\n`<code>&amp;</code>`')
        cards = media_embeds({'description': body})
        self.assertEqual(cards, [{'description': body}])
        self.assertEqual(adapt(body), body)

    def test_html_code_blocks_keep_indentation_entities_and_literal_images(self):
        source = ('<pre><code class="language-html">&lt;img src="https://example.com/code.png"&gt;\n'
                  '  &lt;!-- example --&gt;\n- [x] literal</code></pre>')
        cards = media_embeds({'description': source})
        self.assertEqual(len(cards), 1)
        self.assertNotIn('image', cards[0])
        self.assertIn('```html\n<img src="https://example.com/code.png">\n  <!-- example -->\n- [x] literal\n```', cards[0]['description'])

    def test_inline_html_code_is_not_parsed_as_an_image_or_link(self):
        cards = media_embeds({'description': '<code>&lt;img src="https://example.com/code.png"&gt;</code>'})
        self.assertEqual(cards, [{'description': '`<img src="https://example.com/code.png">`'}])

    def test_standard_markdown_and_autolinks_keep_their_meaning(self):
        source = '# Heading\n**Bold** *Italic* ~~Deleted~~\n> Quote\n- Item\n<https://example.com/path>\n`List<T>`'
        self.assertEqual(media_embeds({'description': source}), [{'description': source}])

    def test_markdown_url_query_parameters_are_not_decoded_as_html_entities(self):
        source = '[Logs](https://example.com/log?run=1&notification=2&copy=3)\nhttps://example.com/log?a=1&not=2'
        self.assertEqual(adapt(source), source)
        body = adapt('<a href="https://example.com/log?a=1&amp;b=2">Logs &amp; details</a>')
        self.assertIn('[Logs & details](https://example.com/log?a=1&b=2)', body)
        body = adapt('<a href="https://example.com/?a=1&amp;b=2">https://example.com/?a=1&amp;b=2</a>')
        self.assertIn('[https://example.com/?a=1&b=2](https://example.com/?a=1&b=2)', body)

    def test_code_fences_inside_html_code_do_not_expose_an_image(self):
        cards = media_embeds({'description': '<pre><code>```md\n![example](https://example.com/literal.png)\n```</code></pre>'})
        self.assertEqual(len(cards), 1)
        self.assertNotIn('image', cards[0])
        self.assertIn('````\n```md', cards[0]['description'])

    def test_nested_html_attributes_entities_and_line_breaks(self):
        body = adapt('<div><h3>Result &amp; links</h3><p><strong class="label">Build</strong><br />'
                     '<em>Ready</em> &lt; 10 &gt; 2 &#128640;</p>'
                     '<details open><summary>More</summary><p>Details</p></details></div>')
        for expected in ('### Result & links', '**Build**\n*Ready* < 10 > 2 🚀', '**More**', 'Details'):
            self.assertIn(expected, body)
        self.assertNotIn('<div>', body)

    def test_unsafe_link_schemes_and_hidden_content_are_not_forwarded(self):
        body = adapt('<a href="javascript:alert(1)">Visible link</a> '
                     '<a href="data:text/html,secret">Other link</a>'
                     '<script>SECRET SCRIPT</script><style>SECRET STYLE</style><!-- SECRET COMMENT -->'
                     '<img src="javascript:alert(1)" alt="Missing image">')
        self.assertIn('Visible link', body)
        self.assertIn('Other link', body)
        self.assertIn('Missing image', body)
        for hidden in ('javascript:', 'data:', 'SECRET', '<script', '<style'):
            self.assertNotIn(hidden, body)

    def test_image_between_text_and_table_keeps_reading_order_and_final_fields(self):
        cards = media_embeds({'description': 'Before\n' + CLOUDFLARE + '\nAfter', 'fields': [{'name': 'Details', 'value': 'Done'}]})
        self.assertIn('Before', cards[0]['description'])
        self.assertNotIn('Latest commit', cards[0]['description'])
        self.assertIn('Latest commit', cards[1]['description'])
        self.assertTrue(cards[-1]['description'].endswith('After'))
        self.assertEqual(cards[-1]['fields'], [{'name': 'Details', 'value': 'Done'}])

    def test_large_html_tables_are_split_without_losing_last_row(self):
        source = '<table>' + ''.join(f'<tr><td>Step {n}</td><td>Result {n} is complete.</td></tr>' for n in range(150)) + '</table>'
        pages = message_pages({'description': source})
        body = '\n'.join(card.get('description', '') for page in pages for card in page)
        self.assertIn('**Step 149:** Result 149 is complete.', body)
        self.assertNotIn('<td>', body)
        for page in pages:
            for card in page:
                self.assertLessEqual(units(card.get('description', '')), 4096)

    def test_incomplete_html_keeps_visible_text_without_dangling_known_tags(self):
        body = adapt('</a><table><tr><td>Commit</td><td><code>abc123</code></td></tr></table><p>Final text')
        self.assertIn('**Commit:** `abc123`', body)
        self.assertTrue(body.endswith('Final text'))
        self.assertNotIn('</a>', body)

    def test_deeply_nested_input_stays_bounded(self):
        self.assertIn('Still visible', adapt('<div>' * 1000 + 'Still visible' + '</div>' * 1000))

    def test_html_comment_conversion_is_shared_by_pr_issue_and_discussion(self):
        from discord_threads import Topic, make_embed
        for kind in ('pr', 'issue', 'discussion'):
            with self.subTest(kind=kind):
                obj = {'number': 5, 'title': 'Deploy notes', 'body': 'Original description'}
                payload = {'action': 'created', 'comment': {'body': CLOUDFLARE, 'user': {'login': 'cloudflare[bot]'}}}
                topic = Topic('owner/repo', kind, 5, obj['title'], 'https://github.com/owner/repo', obj)
                pages = message_pages(make_embed(topic, 'discussion_comment' if kind == 'discussion' else 'issue_comment', payload))
                body = '\n'.join(card.get('description', '') for page in pages for card in page)
                self.assertIn('**Latest commit:** `93cdd36`', body)
                self.assertNotIn('<table>', body)
                self.assertNotIn('</a>', body)
