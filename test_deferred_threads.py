import copy
import datetime as dt
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from delivery import CachedThreads
from discord_threads import ApiError
from service import Service
from test_message_edits import EditingDiscord
from test_team_features import binding
from deferred_threads import wake

class DeferredApi(EditingDiscord):
    def call(self,method,path,data=None):
        if method=='GET' and path.startswith('/channels/') and '/messages' not in path and path.rsplit('/',1)[-1] not in ('c','g') and '/threads/' not in path:
            if path.rsplit('/',1)[-1] not in self.threads:raise ApiError('Discord',404,10003)
        if method=='PATCH' and path.startswith('/channels/c/messages/'):
            row=self.roots[path.rsplit('/',1)[-1]];row.update(copy.deepcopy(data));return copy.deepcopy(row)
        if method=='DELETE' and path.startswith('/channels/c/messages/'):
            self.roots.pop(path.rsplit('/',1)[-1],None);return None
        return super().call(method,path,data)

class DeferredThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Path(self.temp.name),{})
        self.now=dt.datetime(2026,11,3,23,tzinfo=dt.timezone.utc).timestamp()
        options={'quiet':True,'quiet_start':22,'quiet_end':8,'utc_offset':0}
        config=dict(binding(options).config,guild_id='g',channels={'pr':'c','issue':'c','discussion':'c'},routing_groups=[])
        identity=self.service.registry.add('tenant',11,'owner/repo',1,'app',binding(options).config)
        self.b=self.service.registry.get(identity);self.b.config.update(config);self.service.registry.get=lambda *args,**kwargs:self.b
        self.q=self.service.queue(self.b)
        self.api=DeferredApi();self.threads=CachedThreads(self.api,'g',config['channels'],self.q);self.threads.bot={'id':'bot'}
        self.current={'number':1,'title':'Title','state':'open','body':'Body','head':{'ref':'feature'},'base':{'ref':'main'}}
        self.github=Mock();self.github.pages.return_value=[];self.github.call.side_effect=lambda method,path,data=None: {'id':11} if path.startswith('/repositories/') else dict(self.current)
        self.service.github=lambda _:self.github
        self.notices=Mock()
    def tearDown(self):self.temp.cleanup()
    def value(self,action='opened',number=1):
        return {'repository':{'id':11,'full_name':'owner/repo'},'installation':{'id':1},'action':action,'pull_request':dict(self.current,number=number),'sender':{'login':'author'}}
    def process(self,window=0):
        with patch('service.time.time',side_effect=lambda:self.now):return self.service.process(self.b,self.threads,None,self.notices,window_seconds=window)
    def test_new_threads_are_immediate_and_messages_silent_during_dnd(self):
        self.q.put('one','pull_request',self.value());self.assertTrue(self.process())
        self.assertEqual(self.api.starts,1)
        self.assertTrue(self.api.posts)
        for row in next(iter(self.api.posts.values())):self.assertEqual(row.get('flags',0)&4096,4096)
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT count(*) FROM deferred_thread_jobs').fetchone()[0],0)

    def test_legacy_pending_jobs_wake_even_during_dnd_and_reuse_original_root(self):
        from root_status import starter_embed
        from discord_threads import Topic
        topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1',self.current)
        root=self.api.call('POST','/channels/c/messages',{'embeds':[starter_embed(topic)],'flags':4096,'nonce':'legacy-root'})
        self.q.put('one','pull_request',self.value())
        with self.q.connect() as db:
            db.execute('INSERT INTO root_index VALUES(?,?,?)',(topic.key,'c',root['id']))
            db.execute('INSERT INTO deferred_threads VALUES(?,?,?)',(topic.key,'c',root['id']))
            db.execute('INSERT INTO deferred_thread_jobs VALUES(?,?)',('one',topic.key))
            db.execute('INSERT INTO deferred_thread_pace VALUES(1,?)',(self.now+99999,))
            db.execute('UPDATE jobs SET next=?',(self.now+99999,))
        wake(self.q);self.assertTrue(self.process())
        self.assertEqual(self.api.starts,1)
        self.assertEqual(list(self.api.threads),[root['id']])
        self.assertEqual([row['id'] for row in self.api.roots.values()
                          if any(embed.get('footer',{}).get('text')==topic.key for embed in row['embeds'])],
                         [root['id']])
        with self.q.connect() as db:
            self.assertEqual(db.execute('SELECT done FROM jobs').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT count(*) FROM deferred_thread_jobs').fetchone()[0],0)
