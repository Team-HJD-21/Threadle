import copy
from contextlib import closing
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest

from command_sync import CommandSync
from data_retention import Retention, PERIOD
from product_web import content
from connect_web import page
from service import Service
from test_service import config
from web_locale import LANGUAGE, PAGE_PATH


class MemoryCommands:
    def __init__(self):
        self.rows = [{'id':'11','name':'connect','type':1,'description':'old'},
                     {'id':'12','name':'unrelated','type':1}]
        self.calls=[]

    def call(self, method,path,data=None):
        self.calls.append((method,path))
        if method=='GET':
            return copy.deepcopy(self.rows)
        if method=='PATCH':
            row=next(row for row in self.rows if row['id']==path.rsplit('/',1)[-1])
            row.update(copy.deepcopy(data))
            return copy.deepcopy(row)
        if method=='POST':
            row=dict(copy.deepcopy(data),id=str(100+len(self.rows)))
            self.rows.append(row)
            return copy.deepcopy(row)
        raise AssertionError(method)


class LegalAndLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.service=Service(self.root,{'DISCORD_BOT_TOKEN':'unused'})

    def connect(self,repo=11,guild=1000,user='22'):
        return self.service.registry.connect(repo,'team/repo'+str(repo),101,config(guild),'1',user)

    def expire(self,identity):
        self.service.registry.disable(identity)
        with self.service.registry.db() as db:
            db.execute('UPDATE connection_retention SET disconnected_at=? WHERE id=?',(time.time()-PERIOD-100,identity))

    def test_global_command_changes_sync_and_unrelated_commands_are_preserved(self):
        api=MemoryCommands()
        self.service.discord=lambda:api
        sync=CommandSync(self.service,SimpleNamespace(discord_id='9000'))
        sync.check()
        self.assertEqual({row['name'] for row in api.rows},{'connect','summary','settings','unrelated'})
        summary=next(row for row in api.rows if row['name']=='summary')
        self.assertEqual(summary['options'],[])
        settings=next(row for row in api.rows if row['name']=='settings')
        self.assertTrue(next(item for item in settings['options'] if item['name']=='repository')['autocomplete'])
        self.assertEqual(json.loads((self.root/'command-sync.json').read_text())['state'],'registered')
        api.calls.clear()
        sync.check()
        self.assertEqual(api.calls,[('GET','/applications/9000/commands')])

    def test_failed_command_registration_is_reported_and_retried_without_secret_logging(self):
        stop=threading.Event()
        class StopAfterWait:
            def is_set(self):
                return stop.is_set()
            def wait(self,delay):
                self.delay=delay
                stop.set()
        signal=StopAfterWait()
        self.service.discord=lambda:SimpleNamespace(call=lambda *_:(_ for _ in ()).throw(RuntimeError('secret-must-not-be-logged')))
        with self.assertLogs('command-sync',level='WARNING') as logs:
            CommandSync(self.service,SimpleNamespace(discord_id='9000')).run(signal)
        self.assertNotIn('secret-must-not-be-logged',''.join(logs.output))
        self.assertEqual(signal.delay,60)
        self.assertEqual(json.loads((self.root/'command-sync.json').read_text())['state'],'retrying')

    def test_expired_connection_queue_and_identity_records_are_removed_only_for_that_connection(self):
        first,second=self.connect(),self.connect(guild=2000,user='33')
        a,b=self.service.registry.get(first),self.service.registry.get(second)
        self.service.queue(a).put('one','issues',{'body':'private first data'})
        self.service.queue(b).put('two','issues',{'body':'other server data'})
        self.service.registry.save_guild_name('1000','First')
        self.service.registry.save_github_login('22','first',time.time())
        self.service.registry.save_github_login('33','second',time.time())
        self.expire(first)
        self.assertEqual(Retention(self.service).purge(),1)
        self.assertFalse(self.service.queue(a).path.exists() if first in self.service.queues else (self.root/'connections'/first/'queue.sqlite3').exists())
        self.assertIsNotNone(self.service.registry.get(second))
        self.assertIsNotNone(self.service.queue(b).next())
        with self.service.registry.db() as db:
            self.assertFalse(db.execute('SELECT 1 FROM bindings WHERE id=?',(first,)).fetchone())
            self.assertFalse(db.execute('SELECT 1 FROM github_profiles WHERE user=?',('22',)).fetchone())
            self.assertTrue(db.execute('SELECT 1 FROM github_profiles WHERE user=?',('33',)).fetchone())

    def test_installation_waiting_for_connection_is_not_deleted(self):
        with self.service.registry.db() as db:
            db.execute('INSERT INTO installations VALUES(202,1)')
        Retention(self.service).purge()
        with self.service.registry.db() as db:
            self.assertTrue(db.execute('SELECT 1 FROM installations WHERE id=202').fetchone())

    def test_recent_disconnect_and_paused_connections_are_preserved(self):
        recent,paused=self.connect(),self.connect(guild=2000)
        self.service.registry.disable(recent)
        with self.service.registry.db() as db:
            db.execute('INSERT INTO admin_paused VALUES(?)',(paused,))
        self.assertEqual(Retention(self.service).purge(),0)
        self.assertIsNotNone(self.service.registry.get(paused,include_paused=True))
        with self.service.registry.db() as db:
            self.assertTrue(db.execute('SELECT 1 FROM bindings WHERE id=?',(recent,)).fetchone())

    def test_reconnection_does_not_restart_old_retention_or_delete_new_connection(self):
        old=self.connect()
        queue=self.service.queue(self.service.registry.get(old))
        queue.put('old','issues',{'body':'old data'})
        self.expire(old)
        fresh=self.connect()
        self.assertNotEqual(old,fresh)
        self.assertEqual(Retention(self.service).purge(),1)
        self.assertIsNotNone(self.service.registry.get(fresh))
        self.assertFalse(queue.path.exists())
        with self.service.registry.db() as db:
            self.assertFalse(db.execute('SELECT 1 FROM retired_bindings WHERE id=?',(old,)).fetchone())

    def test_shared_inbox_and_layout_keep_other_live_server_data(self):
        first,second=self.connect(),self.connect(guild=2000,user='33')
        payload={'repository':{'id':11,'full_name':'team/repo11'},'installation':{'id':101},'issue':{'number':1,'body':'shared event'}}
        self.service.registry.receive('app','shared-delivery','issues',payload)
        import sqlite3
        with closing(sqlite3.connect(self.root/'channel-layouts.sqlite3')) as db:
            db.execute('CREATE TABLE layouts(guild TEXT PRIMARY KEY,data TEXT)')
            db.executemany('INSERT INTO layouts VALUES(?,?)',[('1000:11','old'),('2000:11','keep')])
            db.commit()
        self.expire(first)
        Retention(self.service).purge()
        row=self.service.registry.pending()[0]
        self.assertEqual(json.loads(row[-1]),[second])
        with closing(sqlite3.connect(self.root/'channel-layouts.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT * FROM layouts').fetchall(),[('2000:11','keep')])

    def test_unknown_old_disconnection_date_is_initialized_once(self):
        identity=self.connect()
        self.service.registry.disable(identity)
        with self.service.registry.db() as db:
            db.execute('DELETE FROM connection_retention WHERE id=?',(identity,))
        from registry import Registry
        before=time.time()
        Registry(self.root/'connections.sqlite3')
        with self.service.registry.db() as db:
            first=db.execute('SELECT disconnected_at FROM connection_retention WHERE id=?',(identity,)).fetchone()[0]
        self.assertGreaterEqual(first,before)
        Registry(self.root/'connections.sqlite3')
        with self.service.registry.db() as db:
            self.assertEqual(first,db.execute('SELECT disconnected_at FROM connection_retention WHERE id=?',(identity,)).fetchone()[0])

    def test_schema_keeps_original_binding_shape_for_file_rollback_compatibility(self):
        with self.service.registry.db() as db:
            self.assertEqual(len(db.execute('PRAGMA table_info(bindings)').fetchall()),10)
            self.assertEqual(len(db.execute('PRAGMA table_info(retired_bindings)').fetchall()),2)
        identity=self.connect()
        self.service.registry.disable(identity)
        with self.service.registry.db() as db:
            self.assertIsNotNone(db.execute('SELECT disconnected_at FROM connection_retention WHERE id=?',(identity,)).fetchone())

    def test_terms_and_privacy_are_public_bilingual_and_linked_from_every_footer(self):
        session=SimpleNamespace(data={'csrf':'preview'})
        for language in ('ko','en'):
            language_token=LANGUAGE.set(language)
            try:
                for kind in ('terms','privacy'):
                    path='/connect/'+kind
                    path_token=PAGE_PATH.set(path)
                    try:
                        title,body=content(path,session)
                        html=page(title,body,landing=True)
                        self.assertIn('<meta name="robots" content="index,follow">',html)
                        self.assertIn('href="/connect/terms"',html)
                        self.assertIn('href="/connect/privacy"',html)
                        self.assertIn('support@teamhjd.com',html)
                        self.assertIn('30',html)
                        self.assertIn('Terms of Service' if language=='en' and kind=='terms' else 'Privacy Policy' if language=='en' else '이용약관' if kind=='terms' else '개인정보처리방침',html)
                    finally:
                        PAGE_PATH.reset(path_token)
            finally:
                LANGUAGE.reset(language_token)
