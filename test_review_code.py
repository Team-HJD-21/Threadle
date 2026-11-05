import unittest
from review_code import preview


def thread(hunk,line=33,**values):
    return {'path':'Assets/Scripts/Example.cs','line':line,'diffSide':'RIGHT',
            'comments':[{'diffHunk':hunk,'originalLine':line}],**values}


class ReviewCodeTests(unittest.TestCase):
    def test_code_before_review_line_and_line_numbers_are_visible(self):
        value=thread('@@ -30,4 +30,4 @@\n+    }, true, cancellation);\n+}\n+\n+public Task JoinLobbyAsync(string lobbyId)')
        shown=preview(value)
        self.assertIn('`Example.cs:33`',shown)
        self.assertIn('```diff\n',shown)
        self.assertIn('+   30 |     }, true, cancellation);',shown)
        self.assertIn('+   33 | public Task JoinLobbyAsync(string lobbyId)',shown)
        self.assertNotIn('Assets/Scripts',shown)

    def test_deleted_line_uses_left_side_numbers_and_preserves_diff_signs(self):
        value=thread('@@ -10,2 +10,2 @@\n same\n-old();\n+new();',11,diffSide='LEFT')
        shown=preview(value)
        self.assertIn('-   11 | old();',shown)
        self.assertNotIn('new();',shown)

    def test_original_anchor_is_used_for_outdated_and_shifted_reviews(self):
        value=thread('@@ -30 +30 @@\n+originalCode();',300,isOutdated=True)
        value['comments'][0]['originalLine']=30
        shown=preview(value)
        self.assertIn('Example.cs:30',shown)
        self.assertIn('Outdated code',shown)
        self.assertIn('originalCode();',shown)

    def test_missing_file_level_or_unmatched_hunks_do_not_invent_context(self):
        self.assertEqual(preview(thread('',None)),'')
        self.assertEqual(preview(thread('@@ -1 +1 @@\n+notTheTarget();',100)),'')

    def test_large_multiline_context_is_bounded_without_truncating_comments(self):
        value=thread('@@ -1,100 +1,100 @@\n'+'\n'.join('+'+'x'*500 for _ in range(100)),100)
        value['comments'][0]['originalStartLine']=1
        shown=preview(value)
        self.assertLess(len(shown),3000)
        self.assertIn('…',shown)
        self.assertIn('100 |',shown)
        self.assertIn('Example.cs:1–100',shown)

    def test_code_fences_urls_and_html_in_source_are_literal(self):
        from notification_layout import message_pages
        value=thread('@@ -1 +1 @@\n+var sample = "```<a href=\"https://example.com/pic.png\">";',1)
        pages=message_pages({'description':preview(value),'color':123})
        self.assertFalse(any(e.get('image') for p in pages for e in p))
        self.assertIn('<a href=',pages[0][0]['description'])
