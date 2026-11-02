import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from ci_channel_migration import CiChannelMigration
from discord_threads import ApiError
from registry import Registry


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=Mock()
        self.service.registry=Registry(Path(self.temp.name)/'registry.sqlite3')
        self.channels={
            '101':{'id':'101','guild_id':'10','type':0,'name':'🔄github-ci','topic':'GitHub Notify managed channel | layout=abcd; kind=ci'},
            '102':{'id':'102','guild_id':'10','type':0,'name':'github-ci','topic':'Unrelated user channel'}}
        self.calls=[];self.lose=False
        self.api=Mock();self.api.call.side_effect=self.call
        self.service.discord.return_value=self.api

    def tearDown(self):
        self.temp.cleanup()

    def call(self,method,path,data=None):
        self.calls.append((method,path,copy.deepcopy(data)))
        if path.startswith('/users/@me/guilds'):
            return [{'id':'10'}]
        if path=='/guilds/10/channels':
            return list(copy.deepcopy(self.channels).values())
        row=self.channels[path.split('/')[2]]
        if method=='PATCH':
            row.update(data)
            if self.lose:
                self.lose=False
                raise ApiError('Discord','network')
        return dict(row)

    def test_rename_once_preserves_id_topic_and_later_manual_rename(self):
        self.assertTrue(CiChannelMigration(self.service).check())
        self.assertEqual(self.channels['101']['name'],'🔄github-ci-cd')
        self.assertEqual(self.channels['102']['name'],'github-ci')
        self.channels['101']['name']='my-ci'
        self.assertTrue(CiChannelMigration(self.service).check())
        self.assertEqual(self.channels['101']['name'],'my-ci')
        self.assertEqual(len([c for c in self.calls if c[0]=='PATCH']),1)

    def test_response_lost_after_rename_does_not_repeat_patch(self):
        self.lose=True
        migration=CiChannelMigration(self.service)
        self.assertFalse(migration.check())
        self.assertTrue(CiChannelMigration(self.service).check())
        self.assertEqual(len([c for c in self.calls if c[0]=='PATCH']),1)

    def test_new_channel_after_snapshot_is_not_migrated_again(self):
        migration=CiChannelMigration(self.service)
        self.assertTrue(migration.check())
        self.channels['103']=dict(self.channels['101'],id='103',name='github-ci')
        self.assertTrue(CiChannelMigration(self.service).check())
        self.assertEqual(self.channels['103']['name'],'github-ci')
