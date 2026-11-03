import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError
from api_cooldowns import COOLDOWNS
from delivery import Queue
from delivery_metrics import begin,end
from discord_threads import Api,ApiError

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.q=Queue(Path(self.temp.name)/'q.db');self.q.put('one','workflow_run',{})
        self.sleep=Mock();self.api=Api('Discord','https://discord.com/api/v10','test-only','Bot',sleep=self.sleep)
        self.cooldowns=patch.dict(COOLDOWNS,{},clear=True);self.cooldowns.start();self.token=begin(self.q,['one'])
    def tearDown(self):end(self.token);self.cooldowns.stop();self.temp.cleanup()
    def error(self,status,body=None,headers=None):return HTTPError('https://discord.com/api/v10/channels/123/messages',status,'failure',headers or {},io.BytesIO(json.dumps(body or {}).encode()))
    def response(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False);response.read.return_value=b'[]';response.headers={};return response
    def test_server_failure_yields_after_one_request_without_sleep(self):
        with patch('discord_threads.urlopen',side_effect=self.error(503)) as request:
            with self.assertRaises(ApiError) as error:self.api.call('GET','/channels/123/messages')
        self.assertEqual(request.call_count,1);self.sleep.assert_not_called();self.assertIsNone(error.exception.retry_after)
    def test_network_error_yields_without_inline_retries(self):
        with patch('discord_threads.urlopen',side_effect=URLError('offline')) as request:
            with self.assertRaises(ApiError):self.api.call('GET','/channels/123/messages')
        self.assertEqual(request.call_count,1);self.sleep.assert_not_called()
    def test_long_retry_after_is_persisted_without_sixty_or_nine_hundred_second_caps(self):
        with patch('discord_threads.urlopen',side_effect=self.error(429,{'retry_after':1800})):
            with self.assertRaises(ApiError) as error:self.api.call('POST','/channels/123/messages',{'content':'x'})
        with patch('delivery.time.time',return_value=100):self.q.retry('one',0,retry_after=error.exception.retry_after)
        with self.q.connect() as db:self.assertEqual(db.execute('SELECT next FROM jobs').fetchone()[0],1900)
        self.sleep.assert_not_called()
    def test_channel_cooldown_does_not_block_other_channels(self):
        with patch('discord_threads.urlopen',side_effect=self.error(429,{'retry_after':120})):
            with self.assertRaises(ApiError):self.api.call('GET','/channels/123/messages')
        with patch('discord_threads.urlopen',return_value=self.response()) as request:
            with self.assertRaises(ApiError):self.api.call('GET','/channels/123/messages/777')
            self.assertEqual(request.call_count,0)
            self.api.call('GET','/channels/456/messages');self.assertEqual(request.call_count,1)
    def test_global_cooldown_is_shared_but_scoped_to_the_token(self):
        with patch('discord_threads.urlopen',side_effect=self.error(429,{'retry_after':120,'global':True})):
            with self.assertRaises(ApiError):self.api.call('GET','/channels/123/messages')
        with patch('discord_threads.urlopen',return_value=self.response()) as request:
            with self.assertRaises(ApiError):self.api.call('GET','/channels/456/messages')
            self.assertEqual(request.call_count,0)
            Api('Discord',self.api.base,'another-test-token','Bot').call('GET','/channels/456/messages')
            self.assertEqual(request.call_count,1)
    def test_github_primary_limit_uses_reset_header(self):
        api=Api('GitHub','https://api.github.com','test-github','Bearer',sleep=self.sleep)
        with patch('discord_threads.urlopen',side_effect=self.error(403,headers={'X-RateLimit-Remaining':'0','X-RateLimit-Reset':'5000'})),patch('api_cooldowns.time.time',return_value=100):
            with self.assertRaises(ApiError) as error:api.call('GET','/repos/owner/repo')
        self.assertEqual(error.exception.retry_after,4900);self.sleep.assert_not_called()
    def test_same_topic_order_is_preserved_while_other_work_runs(self):
        self.q.finish('one')
        payload={'repository':{'id':11,'full_name':'owner/repo'},'issue':{'number':1},'action':'opened'}
        self.q.put('first','issues',payload);self.q.put('second','issues',payload)
        self.q.put('other','issues',dict(payload,issue={'number':2}));self.q.put('ci','workflow_run',{})
        with patch('delivery.time.time',return_value=100):self.q.retry('first',0,retry_after=100)
        with patch('delivery.time.time',return_value=101):
            self.assertEqual(self.q.next()[0],'other');self.q.finish('other')
            self.assertEqual(self.q.next()[0],'ci');self.q.finish('ci');self.assertIsNone(self.q.next())
        with patch('delivery.time.time',return_value=200):
            self.assertEqual(self.q.next()[0],'first');self.q.finish('first');self.assertEqual(self.q.next()[0],'second')
    def test_invalid_retry_delays_fall_back_to_finite_backoff(self):
        for value in ('bad',float('inf'),float('nan'),-1):
            with patch('delivery.time.time',return_value=100):self.q.retry('one',0,retry_after=value)
            with self.q.connect() as db:self.assertEqual(db.execute('SELECT next FROM jobs').fetchone()[0],102)
    def test_non_delivery_callers_keep_inline_retry_behavior(self):
        end(self.token);self.token=begin(self.q,[])
        from delivery_metrics import ACTIVE
        bypass=ACTIVE.set(None)
        try:
            with patch('discord_threads.urlopen',side_effect=[self.error(503),self.response()]):self.api.call('GET','/channels/123/messages')
            self.sleep.assert_called_once_with(1)
        finally:ACTIVE.reset(bypass)
