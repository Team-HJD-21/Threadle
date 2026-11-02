import unittest
from unittest.mock import Mock
from discord_threads import ApiError,Threads,Topic

class SilentCreateTests(unittest.TestCase):
    def setUp(self):
        self.api=Mock();self.threads=Threads(self.api,'g',{'pr':'c'})
        self.topic=Topic('owner/repo','pr',1,'Title','https://github.com/owner/repo/pull/1')
    def test_dnd_creation_requests_silent_without_changing_thread_shape(self):
        self.threads.silent_notifications=True;self.threads.start('c','100',self.topic)
        data=self.api.call.call_args.args[2]
        self.assertEqual(data,{'name':self.topic.name,'auto_archive_duration':1440,'flags':4096})
    def test_normal_creation_is_unchanged(self):
        self.threads.start('c','100',self.topic)
        self.assertNotIn('flags',self.api.call.call_args.args[2])
    def test_rejected_flag_falls_back_once_to_the_same_source_message(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,50035),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        calls=self.api.call.call_args_list
        self.assertEqual(len(calls),2);self.assertEqual(calls[0].args[1],calls[1].args[1])
        self.assertIn('flags',calls[0].args[2]);self.assertNotIn('flags',calls[1].args[2])
    def test_duplicate_existing_thread_is_recovered_without_fallback(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,160004),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        self.assertEqual([c.args[0] for c in self.api.call.call_args_list],['POST','GET'])
    def test_duplicate_after_rejected_flag_still_recovers(self):
        self.threads.silent_notifications=True
        self.api.call.side_effect=[ApiError('Discord',400,50035),ApiError('Discord',400,160004),{'id':'100','parent_id':'c'}]
        self.assertEqual(self.threads.start('c','100',self.topic)['id'],'100')
        self.assertEqual([c.args[0] for c in self.api.call.call_args_list],['POST','POST','GET'])
    def test_permission_and_uncertain_network_errors_are_not_retried_without_flags(self):
        self.threads.silent_notifications=True
        for status,code in [(403,50013),('network',None),(400,160006)]:
            self.api.reset_mock();self.api.call.side_effect=ApiError('Discord',status,code)
            with self.assertRaises(ApiError):self.threads.start('c','100',self.topic)
            self.assertEqual(self.api.call.call_count,1)
