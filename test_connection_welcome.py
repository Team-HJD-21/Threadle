import copy
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

from connection_welcome import ConnectionWelcome
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
