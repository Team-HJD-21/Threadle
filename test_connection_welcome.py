import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from connection_welcome import ConnectionWelcome, guide_text
from delivery import Queue
from registry import Registry


class WelcomeApi:
    def __init__(self):
        self.posts=[];self.lose=False
    def call(self,method,path,data=None):
        if method=='POST':
            row=dict(copy.deepcopy(data),id=str(len(self.posts)+1),channel_id=path.split('/')[2]);self.posts.append(row)
            if self.lose:
                self.lose=False
                raise RuntimeError('Response lost')
            return row
        if method=='GET' and '/messages?' in path:
            return list(reversed(copy.deepcopy(self.posts)))
        if method=='GET' and path.startswith('/channels/'):
            return {'id':path.split('/')[2],'guild_id':'10','type':0}
        raise AssertionError((method,path))


class WelcomeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.registry=Registry(Path(self.temp.name)/'connections.sqlite3')
        self.service=SimpleNamespace(registry=self.registry)
        self.config={'guild_id':'10','channels':{'pr':'101','issue':'102','discussion':'103'},'alert_channel':'104','ci_channel':'105','language':'en'}
        self.api=WelcomeApi();self.queue=Queue(Path(self.temp.name)/'queue.sqlite3')
    def tearDown(self):
        self.temp.cleanup()
    def connect(self):
        identity=self.registry.connect(11,'owner/repo',12,self.config,'1','2')
        return self.registry.get(identity)
    def welcome(self,binding):
        return ConnectionWelcome(self.service,binding,self.api,self.queue)
    def test_first_connection_greets_and_summarizes_once(self):
        binding=self.connect()
        with patch('connection_welcome.open_items',return_value=[]):
            self.welcome(binding).tick(Mock())
            self.welcome(binding).tick(Mock())
            self.connect()
            self.welcome(binding).tick(Mock())
        self.assertEqual(len(self.api.posts),2)
        self.assertIn('Threadle',self.api.posts[0]['content'])
        self.assertIn('owner/repo',self.api.posts[0]['content'])
        self.assertIn('Current roundup',self.api.posts[1]['embeds'][0]['title'])
        self.assertTrue(all(row['channel_id']=='104' for row in self.api.posts))
    def test_existing_users_do_not_get_upgrade_welcome(self):
        identity=self.registry.add('old',11,'owner/repo',12,'app',self.config)
        self.assertFalse(self.welcome(self.registry.get(identity)).pending())

    def test_guide_sent_once_across_repositories_workers_and_restarts(self):
        self.config['guide_channel']='106'
        first=self.connect()
        other_id=self.registry.connect(22,'owner/other',12,self.config,'1','2')
        second=self.registry.get(other_id)
        with patch('connection_welcome.open_items',return_value=[]):
            self.welcome(second).tick(Mock())
            self.welcome(first).tick(Mock())
            self.welcome(first).tick(Mock())
        guides=[row for row in self.api.posts if row['channel_id']=='106']
        self.assertEqual(len(guides),1)
        self.assertEqual(guides[0]['flags'],4096)
        self.assertIn('/account connect',guides[0]['embeds'][0]['description'])
        self.assertEqual(guides[0]['allowed_mentions'],{'parse':[]})
        self.assertFalse(self.welcome(first).pending())
        self.assertEqual(len(self.api.posts),5)

    def test_lost_guide_response_is_recovered_before_marking_done(self):
        self.config['guide_channel']='106';binding=self.connect();self.api.lose=True
        with self.assertRaises(RuntimeError):self.welcome(binding).tick(Mock())
        self.assertTrue(self.welcome(binding).guide_pending())
        with patch('connection_welcome.open_items',return_value=[]):self.welcome(binding).tick(Mock())
        self.assertEqual(len([r for r in self.api.posts if r['channel_id']=='106']),1)
        self.assertFalse(self.welcome(binding).guide_pending())

    def test_guide_transfer_if_first_repository_disconnected_before_delivery(self):
        self.config['guide_channel']='106';first=self.connect()
        self.registry.disconnect(first.id,'10','1')
        other=self.registry.connect(22,'owner/other',12,self.config,'1','2')
        with patch('connection_welcome.open_items',return_value=[]):self.welcome(self.registry.get(other)).tick(Mock())
        self.assertEqual(len([r for r in self.api.posts if r['channel_id']=='106']),1)

    def test_guide_has_both_languages_and_fits_one_embed(self):
        for english in (False,True):
            text=guide_text(english)
            self.assertLess(len(text),4096)
            self.assertIn('/settings',text)
            self.assertIn('https://discord.gg/6faZbNBdv',text)
    def test_lost_response_and_summary_retry_do_not_repeat_greeting(self):
        binding=self.connect();self.api.lose=True
        with self.assertRaises(RuntimeError):self.welcome(binding).tick(Mock())
        with patch('connection_welcome.open_items',side_effect=RuntimeError('GitHub unavailable')):
            with self.assertRaises(RuntimeError):self.welcome(binding).tick(Mock())
        with patch('connection_welcome.open_items',return_value=[]):self.welcome(binding).tick(Mock())
        self.assertEqual(len(self.api.posts),2)
    def test_korean_snapshot_uses_saved_language_and_safe_titles(self):
        self.config['language']='ko';binding=self.connect()
        with patch('connection_welcome.open_items',return_value=[{'number':1,'title':'@everyone [title]'}]):self.welcome(binding).tick(Mock())
        self.assertIn('안녕하세요',self.api.posts[0]['content'])
        self.assertIn('현재 현황',self.api.posts[1]['embeds'][0]['title'])
        self.assertNotIn('@everyone',self.api.posts[1]['embeds'][0]['description'])
