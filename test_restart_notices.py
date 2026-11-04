import copy
import datetime as dt
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from discord_threads import ApiError
from registry import Registry
from restart_notices import RestartNotices
from test_service import config


class RestartApi:
    def __init__(self):
        self.calls=[];self.messages={};self.nonces={};self.lose_post=False;self.denied=set()

    def call(self,method,path,data=None):
        self.calls.append((method,path,copy.deepcopy(data)))
        if path=='/users/@me':return {'id':'900'}
        channel=path.split('/')[2]
        if channel in self.denied:raise ApiError('Discord',403,50001)
        if method=='GET' and '/messages/' not in path:
            return {'id':channel,'guild_id':str(int(channel)-4),'type':0}
        if method=='POST':
            if data['nonce'] in self.nonces:return copy.deepcopy(self.nonces[data['nonce']])
            row=dict(copy.deepcopy(data),id=str(100+len(self.messages)),channel_id=channel,author={'id':'900'})
            self.messages[row['id']]=row;self.nonces[data['nonce']]=row
            if self.lose_post:self.lose_post=False;raise ApiError('Discord','network')
            return copy.deepcopy(row)
        message=path.rsplit('/',1)[1]
        if message not in self.messages:raise ApiError('Discord',404,10008)
        if method=='GET':return copy.deepcopy(self.messages[message])
        if method=='PATCH':self.messages[message].update(copy.deepcopy(data));return copy.deepcopy(self.messages[message])
        raise AssertionError((method,path))


class RestartNoticeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.registry=Registry(Path(self.temp.name)/'registry.db')
        self.api=RestartApi();self.service=SimpleNamespace(registry=self.registry,discord=lambda:self.api)
        self.now=dt.datetime(2026,11,5,14,tzinfo=dt.timezone.utc).timestamp()

    def tearDown(self):self.temp.cleanup()

    def add(self,n,guild=100,language='ko',quiet=False):
        cfg=config(guild);cfg['language']=language
        if quiet:cfg['team_notifications']={'quiet':True,'quiet_start':22,'quiet_end':8,'utc_offset':9}
        return self.registry.add('team-'+str(n),n,'owner/repo'+str(n),1,'app',cfg)

    def notice(self,run='boot-one'):return RestartNotices(self.service,run=run,clock=lambda:self.now)

    def test_ten_repositories_in_one_guild_get_one_notice(self):
        for n in range(1,11):self.add(n)
        notice=self.notice();self.assertTrue(notice.check())
        self.assertEqual(len(self.api.messages),1)
        self.assertEqual(next(iter(self.api.messages.values()))['channel_id'],'104')
        notice.check();self.assertEqual(len(self.api.messages),1)

    def test_ready_edits_same_message_once_instead_of_posting_again(self):
        self.add(1);notice=self.notice();notice.check()
        identity=next(iter(self.api.messages))
        self.assertFalse(notice.check(ready=True));self.assertFalse(notice.check(ready=True))
        self.assertEqual(list(self.api.messages),[identity])
        self.assertEqual(self.api.messages[identity]['embeds'][0]['color'],0x2DA44E)
        self.assertEqual(sum(method=='PATCH' for method,_,_ in self.api.calls),1)

    def test_each_guild_gets_one_notice_in_its_own_language(self):
        self.add(1,100,'en');self.add(2,200,'ko');self.add(3,200,'ko')
        self.notice().check(ready=True)
        self.assertEqual(len(self.api.messages),2)
        messages={row['channel_id']:row for row in self.api.messages.values()}
        self.assertEqual(messages['104']['embeds'][0]['title'],'Threadle restarted')
        self.assertIn('다시 시작',messages['204']['embeds'][0]['title'])

    def test_lost_post_response_retries_with_same_nonce_without_duplicate(self):
        self.add(1);notice=self.notice();self.api.lose_post=True
        with self.assertLogs('restart-notices',level='ERROR'):self.assertTrue(notice.check())
        self.assertFalse(notice.check(ready=True))
        self.assertEqual(len(self.api.messages),1)
        nonces=[data['nonce'] for method,_,data in self.api.calls if method=='POST']
        self.assertEqual(len(set(nonces)),1)

    def test_same_run_survives_worker_recreation_but_new_restart_gets_new_notice(self):
        self.add(1);self.notice().check()
        self.notice().check(ready=True)
        self.assertEqual(len(self.api.messages),1)
        self.notice('boot-two').check(ready=True)
        self.assertEqual(len(self.api.messages),2)

    def test_dnd_is_silent_and_never_mentions_everyone(self):
        self.add(1,quiet=True);self.add(2)
        self.notice().check()
        row=next(iter(self.api.messages.values()))
        self.assertEqual(row['flags'],4096)
        self.assertEqual(row['allowed_mentions'],{'parse':[]})
        self.assertNotIn('footer',row['embeds'][0])

    def test_permission_failure_in_one_guild_does_not_block_other_guilds(self):
        self.add(1);self.add(2,200);self.api.denied.add('104')
        with self.assertLogs('restart-notices',level='WARNING'):self.assertFalse(self.notice().check(ready=True))
        self.assertEqual(len(self.api.messages),1)
        self.assertEqual(next(iter(self.api.messages.values()))['channel_id'],'204')

    def test_another_authors_message_cannot_be_edited(self):
        self.add(1);notice=self.notice();notice.check()
        next(iter(self.api.messages.values()))['author']['id']='999'
        with self.assertLogs('restart-notices',level='ERROR'):notice.check(ready=True)
        self.assertFalse(any(method=='PATCH' for method,_,_ in self.api.calls))

    def test_removed_connection_is_not_sent_a_restart_notice(self):
        self.add(1);notice=self.notice();notice.capture()
        self.registry.active=Mock(return_value=[])
        self.assertFalse(notice.check(ready=True));self.assertEqual(self.api.messages,{})

    def test_constructor_and_capture_do_not_send_messages(self):
        self.add(1);self.notice().capture()
        self.assertEqual(self.api.calls,[])
