import unittest
from unittest.mock import Mock
from commit_notifications import enrich_commits
from discord_threads import Topic,make_embed


class CommitTests(unittest.TestCase):
    def test_titles_and_links_replace_pr_description(self):
        github=Mock();github.call.return_value={'total_commits':2,'commits':[{'sha':'a'*40,'commit':{'message':'Fix movement\nDetails'}},{'sha':'b'*40,'commit':{'message':'Update UI'}}]}
        payload={'action':'synchronize','before':'c'*40,'after':'b'*40,'pull_request':{'body':'Do not repeat this PR description'}}
        result=enrich_commits(github,'owner/repo','pull_request',payload)
        embed=make_embed(Topic('owner/repo','pr',1,'PR','https://github.com/owner/repo/pull/1'),'pull_request',result,'ko')
        self.assertIn('Fix movement',embed['description']);self.assertIn('Update UI',embed['description'])
        self.assertNotIn('Do not repeat',embed['description']);self.assertNotIn('Details',embed['description'])
        self.assertIn('/commit/'+'a'*40,embed['description'])
    def test_failed_comparison_keeps_link_without_repeating_body(self):
        github=Mock();github.call.side_effect=RuntimeError('unavailable')
        payload={'action':'synchronize','before':'a'*40,'after':'b'*40,'pull_request':{'body':'Do not repeat'}}
        result=enrich_commits(github,'owner/repo','pull_request',payload)
        embed=make_embed(Topic('owner/repo','pr',1,'PR','url'),'pull_request',result,'en')
        self.assertIn('compare/',embed['description']);self.assertNotIn('Do not repeat',embed['description'])
