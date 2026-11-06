import json
import io
import unittest
from unittest.mock import Mock,patch
from urllib.error import HTTPError

from delivery_metrics import ACTIVE
from discord_threads import Api,ApiError


class DeliveryReadTests(unittest.TestCase):
    def setUp(self):
        self.token=ACTIVE.set({'first':1})
        self.addCleanup(ACTIVE.reset,self.token)
        self.api=Api('Discord','https://discord.com/api/v10','test','Bot')

    def transport(self):
        response=Mock()
        response.__enter__=Mock(return_value=response)
        response.__exit__=Mock(return_value=False)
        response.headers={}
        response.read.return_value=json.dumps({'id':'123','type':11,'parent_id':'456','thread_metadata':{'locked':False}}).encode()
        return patch('discord_threads.urlopen',return_value=response)

    def test_repeated_channel_reads_reuse_response_across_message_posts(self):
        with self.transport() as request:
            for _ in range(8):
                channel=self.api.call('GET','/channels/123')
                self.assertFalse(channel['thread_metadata']['locked'])
                channel['thread_metadata']['locked']=True
                self.api.call('POST','/channels/123/messages',{'embeds':[{'description':'A review'}]})
        # Eight cards need eight writes, but their identical metadata GET only once.
        self.assertEqual(request.call_count,9)

    def test_new_delivery_and_idle_calls_always_read_fresh(self):
        with self.transport() as request:
            self.api.call('GET','/channels/123')
            token=ACTIVE.set({'first':1})
            try:self.api.call('GET','/channels/123')
            finally:ACTIVE.reset(token)
            token=ACTIVE.set(None)
            try:
                self.api.call('GET','/channels/123')
                self.api.call('GET','/channels/123')
            finally:ACTIVE.reset(token)
        self.assertEqual(request.call_count,4)

    def test_channel_updates_and_message_edits_invalidate_cached_reads(self):
        with self.transport() as request:
            for method,path in [('PATCH','/channels/123'),('PATCH','/channels/123/messages/789'),('DELETE','/channels/123/messages/789')]:
                self.api.call('GET','/channels/123')
                self.api.call(method,path,{} if method=='PATCH' else None)
                self.api.call('GET','/channels/123')
        self.assertEqual(request.call_count,7)

    def test_credentials_destinations_and_history_do_not_share_results(self):
        other=Api('Discord',self.api.base,'other-token','Bot')
        with self.transport() as request:
            self.api.call('GET','/channels/123')
            other.call('GET','/channels/123')
            self.api.call('GET','/channels/456')
            self.api.call('GET','/channels/123/messages?limit=100')
            self.api.call('GET','/channels/123/messages?limit=100')
        self.assertEqual(request.call_count,5)

    def test_memberships_and_roles_are_scoped_to_each_user_and_guild(self):
        with self.transport() as request:
            for path in ['/guilds/1/roles','/guilds/2/roles','/guilds/1/members/55','/guilds/1/members/56']:
                self.api.call('GET',path);self.api.call('GET',path)
        self.assertEqual(request.call_count,4)

    def test_failed_reads_are_not_cached(self):
        with self.transport() as request:
            response=request.return_value
            request.side_effect=[HTTPError('https://discord.com/api/v10/channels/123',403,'Forbidden',{},io.BytesIO(b'{"code":50013}')),response]
            with self.assertRaises(ApiError):self.api.call('GET','/channels/123')
            self.api.call('GET','/channels/123')
        self.assertEqual(request.call_count,2)
