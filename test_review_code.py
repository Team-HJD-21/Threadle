import unittest
from review_code import preview


def thread(hunk,line=33,**values):
    return {'path':'Assets/Scripts/Example.cs','line':line,'diffSide':'RIGHT',
            'comments':[{'diffHunk':hunk,'originalLine':line}],**values}


class ReviewCodeTests(unittest.TestCase):
    def test_code_before_review_line_keeps_compact_real_line_numbers(self):
        value=thread('@@ -30,4 +30,4 @@\n+    }, true, cancellation);\n+}\n+\n+public Task JoinLobbyAsync(string lobbyId)')
        shown=preview(value)
        self.assertIn('`Example.cs:33`',shown)
        self.assertIn('```csharp\n',shown)
        self.assertIn('30     }, true, cancellation);',shown)
        self.assertIn('\n32\n33 public Task JoinLobbyAsync(string lobbyId)',shown)
        self.assertNotIn(' | ',shown)
        self.assertNotIn('Assets/Scripts',shown)

    def test_deleted_line_shows_left_side_code_and_anchor(self):
        value=thread('@@ -10,2 +10,2 @@\n same\n-old();\n+new();',11,diffSide='LEFT')
        shown=preview(value)
        self.assertIn('Example.cs:11',shown)
        self.assertIn('\n10 same\n11 old();\n',shown)
        self.assertNotIn('new();',shown)

    def test_original_anchor_is_used_for_outdated_and_shifted_reviews(self):
        value=thread('@@ -30 +30 @@\n+originalCode();',300,isOutdated=True)
        value['comments'][0]['originalLine']=30
        shown=preview(value)
        self.assertIn('Example.cs:30',shown)
        self.assertIn('Outdated code',shown)
        self.assertIn('originalCode();',shown)
        self.assertIn('\n30 originalCode();\n',shown)
        self.assertNotIn('\n300 ',shown)

    def test_missing_file_level_or_unmatched_hunks_do_not_invent_context(self):
        self.assertEqual(preview(thread('',None)),'')
        self.assertEqual(preview(thread('@@ -1 +1 @@\n+notTheTarget();',100)),'')

    def test_large_multiline_context_is_bounded_without_truncating_comments(self):
        value=thread('@@ -1,100 +1,100 @@\n'+'\n'.join('+'+'x'*500 for _ in range(100)),100)
        value['comments'][0]['originalStartLine']=1
        shown=preview(value)
        self.assertLess(len(shown),3000)
        self.assertIn('…',shown)
        self.assertNotIn(' | ',shown)
        self.assertIn('Example.cs:1–100',shown)
        self.assertIn('\n100 '+('x'*199)+'…\n',shown)

    def test_code_fences_urls_and_html_in_source_are_literal(self):
        from notification_layout import message_pages
        value=thread('@@ -1 +1 @@\n+var sample = "```<a href=\"https://example.com/pic.png\">";',1)
        pages=message_pages({'description':preview(value),'color':123})
        self.assertFalse(any(e.get('image') for p in pages for e in p))
        self.assertIn('<a href=',pages[0][0]['description'])

    def test_right_side_omits_deleted_code_and_dedents_without_reformatting(self):
        value=thread('@@ -173,1 +159,3 @@\n-            unrelatedOldCode();\n+            // Shared indentation\n+            if (ready)\n+                Run("    keep literal spaces");',161)
        shown=preview(value)
        self.assertIn('```csharp\n159 // Shared indentation\n160 if (ready)\n161     Run("    keep literal spaces");\n```',shown)
        self.assertNotIn('unrelatedOldCode',shown)
        self.assertNotIn('173',shown)

    def test_hunk_gaps_do_not_borrow_context_and_tabs_keep_relative_indent(self):
        value=thread('@@ -1 +1 @@\n+unrelated();\n@@ -30,2 +30,2 @@\n+\t\tif (ready)\n+\t\t\tRun();',31)
        shown=preview(value)
        self.assertIn('\n30 if (ready)\n31 \tRun();\n',shown)
        self.assertNotIn('unrelated',shown)

    def test_common_indentation_is_removed_before_optional_width_limit(self):
        shown=preview(thread('@@ -1 +1 @@\n+'+' '*220+'Run();',1))
        self.assertIn('\n1 Run();\n',shown)
        self.assertNotIn('…',shown)

    def test_language_is_allowlisted_and_source_cannot_close_the_fence(self):
        for path,tag in [('file.py','python'),('file.TS','typescript'),('file.unknown','')]:
            shown=preview(thread('@@ -1 +1 @@\n+code();',1,path=path))
            self.assertIn('\n```'+tag+'\n1 code();\n```',shown)
        from notification_layout import message_pages
        shown=preview(thread('@@ -1,3 +1,3 @@\n+const text = `\n+```\n+<!-- literal -->`;',3))
        self.assertEqual(sum(line=='```' for line in shown.splitlines()),1)
        pages=message_pages({'description':shown})
        self.assertIn('<!-- literal -->',pages[0][0]['description'])

    def test_right_side_numbers_ignore_removed_rows_and_align_digits(self):
        value=thread('@@ -9,3 +9,3 @@\n same();\n-old();\n+new();\n next();',11)
        self.assertIn('```csharp\n 9 same();\n10 new();\n11 next();\n```',preview(value))

    def test_hunk_gap_and_truncated_range_never_invent_line_numbers(self):
        value=thread('@@ -1 +1 @@\n+first();\n@@ -100 +100 @@\n+last();',100)
        value['comments'][0]['originalStartLine']=1
        shown=preview(value)
        self.assertIn('\n  1 first();\n    …\n100 last();\n',shown)
        self.assertNotIn('\n  2 ',shown)
