import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from service import Service
from merge_conflicts import ConflictMonitor,key
from test_service import config
from test_team_features import MessageApi

class ConflictApi(MessageApi):
    def call(self,method,path,data=None):
        if path=='/users/@me':return {'id':'bot','bot':True}
        if '/messages/' in path:
            channel=path.split('/')[2];identity=path.rsplit('/',1)[-1]
            row=next((msg for dest,msg in self.messages if dest==f'/channels/{channel}/messages' and msg['id']==identity),None)
            if row is None:
                from discord_threads import ApiError
                raise ApiError('Discord',404,10008)
            if method=='GET':return copy.deepcopy(row)
            if method=='PATCH':row.update(copy.deepcopy(data));return copy.deepcopy(row)
        result=super().call(method,path,data)
        if method=='POST':result['author']={'id':'bot'}
        return result

class ConflictTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.service=Service(Path(self.temp.name),{})
        settings=dict(config(1000),language='ko',team_notifications={'quiet':True,'quiet_start':0,'quiet_end':23,'batch_seconds':300})
        self.identity=self.service.registry.add('tenant',11,'owner/repo',1,'app',settings)
        self.binding=self.service.registry.get(self.identity);self.api=ConflictApi();self.service.discord=lambda:self.api
        self.now=100000;self.monitor=ConflictMonitor(self.service,clock=lambda:self.now);self.service.conflicts=self.monitor
        self.rows=[{'number':7,'title':'Fix mobile','mergeable':'CONFLICTING','headRefName':'feature/mobile','baseRefName':'main'}]
        self.github=Mock();self.github.call.side_effect=lambda *args:self.response(self.rows)
        self.service.github=lambda _:self.github
        self.monitor.signal(self.binding,'initial','pull_request',{'action':'opened'})
        self.now+=5
    def tearDown(self):self.temp.cleanup()
    def response(self,rows,more=False,cursor=None):
        return {'data':{'repository':{'databaseId':11,'pullRequests':{'nodes':copy.deepcopy(rows),'pageInfo':{'hasNextPage':more,'endCursor':cursor}}}}}
    def again(self):
        self.now+=301
        self.monitor.signal(self.binding,'event:'+str(self.now),'pull_request',{'action':'synchronize'})
        self.now+=5;self.monitor.tick()
    def test_conflict_is_urgent_once_even_during_dnd_and_batching(self):
        self.monitor.tick();self.again()
        self.assertEqual(len(self.api.messages),1)
        path,msg=self.api.messages[0];self.assertEqual(path,'/channels/1004/messages');self.assertNotIn('flags',msg)
        self.assertIn('Merge conflict',msg['embeds'][0]['title'])
        for text in ('owner/repo','feature/mobile','main','/pull/7'):self.assertIn(text,msg['embeds'][0]['description'])
        self.assertEqual(msg['allowed_mentions'],{'parse':[]})
    def test_resolution_replaces_thumbnail_on_existing_message(self):
        with patch('app_emojis.thumbnail', side_effect=lambda api, name: {'url': name}):
            self.monitor.tick()
            self.assertEqual(self.api.messages[0][1]['embeds'][0]['thumbnail']['url'], 'threadle_404')
            self.rows[0]['mergeable'] = 'MERGEABLE'
            self.again()
        self.assertEqual(len(self.api.messages), 1)
        self.assertEqual(self.api.messages[0][1]['embeds'][0]['thumbnail']['url'], 'threadle_fixing')

    def test_resolution_rearms_but_unknown_does_not(self):
        self.monitor.tick();self.rows[0]['mergeable']='UNKNOWN';self.again()
        self.rows[0]['mergeable']='CONFLICTING';self.again();self.assertEqual(len(self.api.messages),1)
        self.rows[0]['mergeable']='MERGEABLE';self.again()
        self.rows[0]['mergeable']='CONFLICTING';self.again();self.assertEqual(len(self.api.messages),2)
    def test_initial_unknown_and_mergeable_are_not_conflicts(self):
        self.rows[0]['mergeable']='UNKNOWN';self.monitor.tick();self.assertFalse(self.api.messages)
        self.rows[0]['mergeable']='MERGEABLE';self.again();self.assertFalse(self.api.messages)
    def test_restart_preserves_episode_receipt(self):
        self.monitor.tick();self.monitor=ConflictMonitor(self.service,clock=lambda:self.now);self.again()
        self.assertEqual(len(self.api.messages),1)
    def test_shared_repo_is_fetched_once_for_two_servers(self):
        settings=dict(config(2000),language='en')
        self.service.registry.add('another',11,'owner/repo',1,'app',settings)
        self.monitor.tick();self.assertEqual(self.github.call.call_count,1);self.assertEqual(len(self.api.messages),2)
        self.assertIn('This PR conflicts',next(msg for path,msg in self.api.messages if path=='/channels/2004/messages')['embeds'][0]['description'])
    def test_one_server_send_failure_does_not_block_others_and_lost_response_recovers(self):
        self.service.registry.add('another',11,'owner/repo',1,'app',config(2000))
        original=self.api.call;lost=[]
        def call(method,path,data=None):
            result=original(method,path,data)
            if method=='POST' and path=='/channels/1004/messages' and not lost:lost.append(True);raise RuntimeError('lost response')
            return result
        self.api.call=call;self.monitor.tick();self.assertEqual(len(self.api.messages),2)
        self.now+=31;self.monitor.tick();self.assertEqual(len(self.api.messages),2)
    def test_partial_graphql_error_does_not_clear_existing_conflict(self):
        self.monitor.tick();self.github.call.side_effect=lambda *args:{'data':{'repository':None},'errors':[{'message':'denied'}]}
        self.again();self.github.call.side_effect=lambda *args:self.response(self.rows)
        self.again();self.assertEqual(len(self.api.messages),1)
    def test_all_pages_are_checked_and_invalid_scope_is_rejected(self):
        self.github.call.side_effect=[self.response([],True,'next'),self.response(self.rows)]
        self.monitor.tick();self.assertEqual(len(self.api.messages),1)
        result=self.response([]);result['data']['repository']['databaseId']=22
        self.github.call.side_effect=lambda *args:result
        with self.assertRaises(RuntimeError):self.monitor.snapshot(self.binding)
    def test_signals_are_shared_deduplicated_and_scan_other_prs_after_merge(self):
        self.rows=[];self.monitor.tick();self.assertEqual(self.github.call.call_count,1)
        self.now+=20;value={'action':'closed','pull_request':{'number':9,'merged':True}}
        self.monitor.signal(self.binding,'merge9','pull_request',value)
        self.monitor.signal(self.binding,'merge9','pull_request',value)
        self.rows=[{'number':7,'title':'Older PR','mergeable':'CONFLICTING','headRefName':'older','baseRefName':'main'}]
        self.now+=5;self.monitor.tick();self.assertEqual(len(self.api.messages),1)
        with self.service.registry.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM conflict_signals WHERE delivery<>'initial'").fetchone()[0],1)
    def test_push_dispatch_schedules_check_without_new_channel_or_thread(self):
        payload={'installation':{'id':1},'repository':{'id':11,'full_name':'owner/repo'},'ref':'refs/heads/main'}
        self.assertEqual(self.service.registry.receive('app','push1','push',payload),202)
        self.service.dispatch()
        with self.service.registry.db() as db:self.assertEqual(db.execute("SELECT count(*) FROM conflict_signals WHERE delivery<>'initial'").fetchone()[0],1)
        self.assertFalse(self.api.messages)
    def test_closed_pr_reopens_as_new_conflict_episode(self):
        self.monitor.tick();previous=self.rows;self.rows=[];self.again()
        self.rows=previous;self.again();self.assertEqual(len(self.api.messages),2)
    def test_disconnected_and_paused_bindings_are_not_scanned(self):
        self.service.registry.disable(self.identity);self.monitor.tick()
        self.assertEqual(self.github.call.call_count,0);self.assertFalse(self.api.messages)

    def link_thread(self):
        from discord_threads import Topic
        topic=Topic('owner/repo','pr',7,'','')
        with self.service.queue(self.binding).connect() as db:db.execute('INSERT OR REPLACE INTO links VALUES(?,?,?)',(topic.key,'1002','777'))
    def test_alert_and_pr_thread_update_same_messages_when_resolved(self):
        self.link_thread();self.monitor.tick();self.assertEqual(len(self.api.messages),2)
        ids=[msg['id'] for _,msg in self.api.messages]
        thread=next(msg for path,msg in self.api.messages if path=='/channels/777/messages')
        self.assertEqual(thread['flags'],4096)
        self.rows[0]['mergeable']='MERGEABLE';self.again()
        self.assertEqual([msg['id'] for _,msg in self.api.messages],ids)
        for _,msg in self.api.messages:
            self.assertIn('Conflict resolved',msg['embeds'][0]['title'])
            self.assertNotIn('충돌을 해결한 뒤',msg['embeds'][0]['description'])
        self.again();self.assertEqual(len(self.api.messages),2)
    def test_thread_pending_during_dnd_gets_latest_resolved_status(self):
        self.monitor.tick();self.assertEqual(len(self.api.messages),1)
        self.rows[0]['mergeable']='MERGEABLE';self.again()
        self.link_thread();self.again()
        self.assertEqual(len(self.api.messages),2)
        self.assertTrue(all('Conflict resolved' in msg['embeds'][0]['title'] for _,msg in self.api.messages))
    def test_lost_resolution_patch_is_retried_without_new_messages(self):
        self.link_thread();self.monitor.tick();original=self.api.call;lost=[]
        def call(method,path,data=None):
            result=original(method,path,data)
            if method=='PATCH' and not lost:lost.append(True);raise RuntimeError('lost edit response')
            return result
        self.api.call=call;self.rows[0]['mergeable']='MERGEABLE';self.again();self.again()
        self.assertEqual(len(self.api.messages),2)
        self.assertTrue(all('Conflict resolved' in msg['embeds'][0]['title'] for _,msg in self.api.messages))
    def test_unknown_does_not_mark_warnings_resolved(self):
        self.link_thread();self.monitor.tick();self.rows[0]['mergeable']='UNKNOWN';self.again()
        self.assertTrue(all('Merge conflict' in msg['embeds'][0]['title'] for _,msg in self.api.messages))

    def test_idle_and_restart_do_not_query_github_again(self):
        self.monitor.tick();self.assertEqual(self.github.call.call_count,1)
        self.now+=86400;self.monitor.tick()
        self.monitor=ConflictMonitor(self.service,clock=lambda:self.now);self.monitor.tick()
        self.assertEqual(self.github.call.call_count,1)
    def test_no_event_means_no_initial_scan(self):
        with self.service.registry.db() as db:
            db.execute('DELETE FROM conflict_scans');db.execute('DELETE FROM conflict_signals')
        self.monitor.tick();self.assertEqual(self.github.call.call_count,0)
    def test_unknown_retries_are_bounded_then_wait_for_next_event(self):
        self.rows[0]['mergeable']='UNKNOWN'
        for _ in range(10):self.monitor.tick();self.now+=31
        self.assertEqual(self.github.call.call_count,6)
        self.now+=86400;self.monitor.tick();self.assertEqual(self.github.call.call_count,6)
        self.again();self.assertEqual(self.github.call.call_count,7)
    def test_old_periodic_schedule_is_retired_but_pending_event_is_preserved(self):
        self.monitor.tick()
        with self.service.registry.db() as db:
            db.execute('DELETE FROM conflict_policy');db.execute('UPDATE conflict_scans SET due=0,retries=0')
        self.monitor=ConflictMonitor(self.service,clock=lambda:self.now)
        self.now+=86400;self.monitor.tick();self.assertEqual(self.github.call.call_count,1)
        self.monitor.signal(self.binding,'pending','pull_request',{'action':'synchronize'})
        with self.service.registry.db() as db:db.execute('DELETE FROM conflict_policy')
        self.monitor=ConflictMonitor(self.service,clock=lambda:self.now);self.now+=5;self.monitor.tick()
        self.assertEqual(self.github.call.call_count,2)
