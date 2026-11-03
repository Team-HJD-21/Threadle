import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from api_usage import ApiUsage
from discord_threads import Api


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.usage=ApiUsage(Path(self.temp.name))

    def service(self,bindings):
        return SimpleNamespace(registry=SimpleNamespace(active=lambda **kw:bindings),
                               tokens=SimpleNamespace(token=Mock(return_value='super-secret')),env={})

    def test_manual_snapshot_survives_restart_and_observation_does_not_update_it(self):
        service=self.service([])
        self.usage.observe('POST','/channels/123/messages',{'X-RateLimit-Bucket':'bucket','X-RateLimit-Remaining':'4'})
        self.assertIsNone(self.usage.read())
        self.usage.refresh(service)
        self.usage.observe('POST','/channels/123/messages',{'X-RateLimit-Bucket':'bucket','X-RateLimit-Remaining':'3'})
        self.assertEqual(self.usage.read()['discord'][0]['remaining'],4)
        restarted=ApiUsage(self.usage.path.parent)
        self.assertEqual(restarted.read()['discord'][0]['remaining'],4)
        restarted.refresh(service)
        self.assertEqual(restarted.read()['discord'],[])

    def test_installation_quota_queried_once_not_per_binding_and_disabled_scope_stays_guarded(self):
        bindings=[SimpleNamespace(auth='app',installation=11,repo_id=n) for n in (1,2,3)]
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'resources':{'core':{'remaining':123,'limit':5000,'used':4877,'reset':2000000000}}}).encode()
        service=self.service(bindings)
        with patch('api_usage.urlopen',return_value=response) as call:
            self.assertTrue(self.usage.refresh(service))
        call.assert_called_once()
        self.assertEqual(self.usage.read()['github'][0]['resources']['core']['remaining'],123)
        self.assertNotIn('super-secret',self.usage.path.read_text())
        from auth import RepositoryApi
        with self.assertRaises(ValueError):RepositoryApi(SimpleNamespace(repo='one/repo',repo_id=1)).call('GET','/rate_limit')

    def test_bucket_bound_major_parameters_and_secret_paths(self):
        for n in range(100):
            self.usage.observe('POST',f'/channels/{n}/messages',{'X-RateLimit-Bucket':'same','X-RateLimit-Remaining':'1'})
        self.assertEqual(len(self.usage.buckets),64)
        self.usage.observe('POST','/webhooks/123/TOKEN?secret=yes',{},429,{'global':True,'retry_after':3})
        self.usage.refresh(self.service([]))
        value=self.usage.path.read_text()
        self.assertNotIn('TOKEN',value)
        self.assertNotIn('secret',value)
        self.assertTrue(self.usage.read()['global_hit'])

    def test_failure_does_not_reuse_old_quota_or_leak_exception(self):
        b=SimpleNamespace(auth='app',installation=11,repo_id=1)
        with patch('api_usage.urlopen',side_effect=RuntimeError('super-secret')):
            self.usage.refresh(self.service([b]))
        self.assertTrue(self.usage.read()['github'][0]['error'])
        self.assertNotIn('super-secret',self.usage.path.read_text())

    def test_response_observer_does_not_break_successful_delivery(self):
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"id":"123"}'
        response.headers={'X-RateLimit-Remaining':'1','X-RateLimit-Bucket':'abc'}
        api=Api('Discord','https://example.com','secret','Bot')
        api.usage_observer=self.usage.observe
        with patch('discord_threads.urlopen',return_value=response):
            self.assertEqual(api.call('POST','/channels/123/messages',{})['id'],'123')
        self.assertEqual(len(self.usage.buckets),1)
        api.usage_observer=Mock(side_effect=RuntimeError('observer failed'))
        with patch('discord_threads.urlopen',return_value=response):
            self.assertEqual(api.call('POST','/channels/123/messages',{})['id'],'123')

