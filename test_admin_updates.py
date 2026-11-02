import base64
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
import zipfile
from unittest.mock import Mock, patch

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from admin_updates import Updates, canonical, verify_package
from discord_threads import ApiError
from service import Service
from test_service import config
from connect_store import Sessions
from connect_auth import ConnectError
from cryptography.fernet import Fernet


class AdminUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()

    def package(self, changed=None, corrupt=False, unsigned=False):
        import hashlib
        payload = {'service.py':b'VALUE = "new"\n','admin_updates.py':b'VALUE = "update"\n'}
        payload.update(changed or {})
        manifest = {name:hashlib.sha256(value).hexdigest() for name,value in payload.items()}
        payload['onboarding-manifest.json'] = json.dumps(manifest).encode()
        files = {name:hashlib.sha256(value).hexdigest() for name,value in payload.items()}
        release = {'product':'Threadle','format':1,'version':'2026.11.01-test','files':files}
        signed = {'release':release,'signature':base64.b64encode(self.private.sign(canonical(release))).decode()}
        output = io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            for name,value in payload.items():
                archive.writestr(name,b'changed' if corrupt and name=='service.py' else value)
            if not unsigned:
                archive.writestr('release.json',json.dumps(signed))
        return output.getvalue()

    def verified(self):
        original = verify_package
        return patch('admin_updates.verify_package',side_effect=lambda raw:original(raw,self.public))

    def test_unsigned_wrong_signature_modified_file_and_protected_paths_are_rejected(self):
        for raw in [self.package(unsigned=True),self.package(corrupt=True),self.package({'../outside.py':b'pass'}),
                    self.package({'credentials.json':b'{}'}),self.package({'web_assets/../../runtime.json':b'{}'})]:
            with self.subTest():
                with self.assertRaises(ValueError):
                    verify_package(raw,self.public)
        with self.assertRaises(ValueError):
            verify_package(self.package(),Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex())

    def test_stage_does_not_change_service_and_cancel_allows_another_upload(self):
        (self.root/'service.py').write_text('VALUE = "old"')
        manager = Updates(self.root)
        with self.verified():
            identity,_ = manager.stage(self.package(),'owner@example.com')
            self.assertEqual((self.root/'service.py').read_text(),'VALUE = "old"')
            self.assertEqual(manager.latest()['state'],'staged')
            with self.assertRaisesRegex(ValueError,'already pending'):
                manager.stage(self.package(),'owner@example.com')
            manager.cancel(identity)
            self.assertEqual(manager.latest()['state'],'cancelled')
            manager.stage(self.package(),'owner@example.com')

    def test_apply_and_rollback_preserve_state_files_and_remove_only_new_payload_files(self):
        (self.root/'service.py').write_bytes(b'VALUE = "old"\n')
        (self.root/'credentials.json').write_bytes(b'PRIVATE CREDENTIALS')
        (self.root/'queue.sqlite3').write_bytes(b'EXISTING QUEUE')
        manager = Updates(self.root)
        with self.verified():
            identity,_ = manager.stage(self.package({'web_assets/test.css':b'body{}'}),'owner@example.com')
            manager.apply(identity)
            self.assertEqual(manager.latest()['state'],'restarting')
            self.assertEqual((self.root/'service.py').read_bytes(),b'VALUE = "new"\n')
            manager.restore(identity)
        self.assertEqual((self.root/'service.py').read_bytes(),b'VALUE = "old"\n')
        self.assertFalse((self.root/'web_assets/test.css').exists())
        self.assertEqual((self.root/'credentials.json').read_bytes(),b'PRIVATE CREDENTIALS')
        self.assertEqual((self.root/'queue.sqlite3').read_bytes(),b'EXISTING QUEUE')
        self.assertEqual(manager.latest()['state'],'rolled-back')

    def test_identical_native_files_are_not_replaced_or_backed_up(self):
        native=self.root/'vendor-presence-v1/example.pyd'
        native.parent.mkdir();native.write_bytes(b'IDENTICAL NATIVE')
        before=native.stat().st_mtime_ns
        manager=Updates(self.root)
        with self.verified():
            identity,_=manager.stage(self.package({'vendor-presence-v1/example.pyd':b'IDENTICAL NATIVE'}),'owner')
            manager.apply(identity)
        self.assertEqual(native.stat().st_mtime_ns,before)
        self.assertNotIn('vendor-presence-v1/example.pyd',json.loads((manager.job(identity)/'previous.json').read_text()))

    def test_backup_failure_does_not_publish_recovery_manifest_or_replace_service(self):
        original=b'VALUE="old"'
        (self.root/'service.py').write_bytes(original)
        manager=Updates(self.root)
        with self.verified():
            identity,_=manager.stage(self.package(),'owner')
            with patch('admin_updates.shutil.copy2',side_effect=OSError('backup unavailable')):
                with self.assertRaises(OSError):manager.apply(identity)
        self.assertFalse((manager.job(identity)/'previous.json').exists())
        self.assertEqual((self.root/'service.py').read_bytes(),original)

    def test_missing_backup_refuses_rollback_before_changing_any_file(self):
        (self.root/'service.py').write_bytes(b'VALUE="old"')
        manager=Updates(self.root)
        with self.verified():
            identity,_=manager.stage(self.package(),'owner');manager.apply(identity)
        (manager.job(identity)/'backup/service.py').unlink()
        with self.assertRaisesRegex(ValueError,'backup is missing'):manager.restore(identity)
        self.assertEqual((self.root/'service.py').read_bytes(),b'VALUE = "new"\n')
        self.assertEqual(manager.latest()['state'],'restarting')

    def test_legacy_backup_skips_identical_files_and_verifies_before_marking_restored(self):
        manager=Updates(self.root)
        with self.verified():identity,_=manager.stage(self.package(),'owner')
        job=manager.job(identity);(job/'backup').mkdir()
        (self.root/'service.py').write_bytes(b'old')
        (job/'backup/service.py').write_bytes(b'old')
        (job/'previous.json').write_text(json.dumps({'service.py':True}))
        with patch('admin_updates.shutil.copy2',side_effect=AssertionError('Unchanged file must not be copied')):
            manager.restore(identity)
        self.assertEqual(manager.latest()['state'],'rolled-back')

    def test_invalid_python_is_rejected_before_staging(self):
        with self.assertRaises(ValueError):
            verify_package(self.package({'service.py':b'def broken(\n'}),self.public)
        self.assertFalse((self.root/'admin-updates').exists())

    def test_web_upload_requires_admin_origin_csrf_and_separate_confirmation(self):
        service = Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        sessions = Sessions(self.root/'connect.sqlite3',Fernet.generate_key().decode())
        session = sessions.create({'admin_subject':'owner'})
        web = SimpleNamespace(settings=SimpleNamespace(base='https://threadle.example'),sessions=sessions,
                              client_key=lambda _: 'local-test',throttle=lambda _:None,
                              send=lambda request,status,*args,**kwargs:status)
        service.web = web
        operator = service.operator
        operator.authenticate = Mock(return_value={'sub':'owner','email':'owner@example.com'})
        def request(path,body,kind='application/x-www-form-urlencoded',origin='https://threadle.example'):
            return SimpleNamespace(command='POST',path=path,rfile=io.BytesIO(body),headers={
                'Origin':origin,'Cookie':'__Host-threadle-admin='+session.key,'Content-Type':kind,'Content-Length':str(len(body))})
        boundary='threadle-test'
        def multipart(csrf):
            return (f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\n{csrf}\r\n'
                    f'--{boundary}\r\nContent-Disposition: form-data; name="package"; filename="update.zip"\r\nContent-Type: application/zip\r\n\r\n').encode()+self.package()+f'\r\n--{boundary}--\r\n'.encode()
        for csrf,origin in [('wrong','https://threadle.example'),(session.data['csrf'],'https://evil.example')]:
            with self.assertRaises(ConnectError) as error:
                operator.handle(request('/connect/admin/update/upload',multipart(csrf),'multipart/form-data; boundary='+boundary,origin))
            self.assertEqual(error.exception.status,403)
        self.assertIsNone(operator.updates.latest())
        with self.verified():
            status=operator.handle(request('/connect/admin/update/upload',multipart(session.data['csrf']),'multipart/form-data; boundary='+boundary))
        self.assertEqual(status,303)
        identity=operator.updates.latest()['id']
        operator.updates.launch=Mock()
        from urllib.parse import urlencode
        body=urlencode({'csrf':session.data['csrf'],'job':identity,'confirmation':'wrong'}).encode()
        with self.assertRaises(ConnectError):
            operator.handle(request('/connect/admin/update/apply',body))
        operator.updates.launch.assert_not_called()
        body=urlencode({'csrf':session.data['csrf'],'job':identity,'confirmation':'UPDATE'}).encode()
        self.assertEqual(operator.handle(request('/connect/admin/update/apply',body)),303)
        operator.updates.launch.assert_called_once_with(identity)
        self.assertEqual(operator.authenticate.call_count,5)


class GuildLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = Service(self.root,{'DISCORD_BOT_TOKEN':'test'})
        self.first = self.service.registry.connect(11,'team/repo',101,config(1000),'1','22')
        self.second = self.service.registry.connect(11,'team/repo',101,config(2000),'2','33')
        self.api = Mock()
        self.service.discord = lambda:self.api
        self.service.guilds.profiles = Mock()

    def test_removal_disconnects_all_connections_only_in_that_guild_and_keeps_queue(self):
        extra = self.service.registry.connect(12,'team/other',101,config(1000),'1','22')
        queue = self.service.queue(self.service.registry.get(self.first))
        queue.put('saved','issues',{'keep':True})
        with self.service.registry.db() as db:
            db.execute('INSERT INTO admin_paused VALUES(?)',(extra,))
        self.service.guilds.removed(SimpleNamespace(id=1000,unavailable=False))
        self.assertIsNone(self.service.registry.get(self.first))
        self.assertIsNone(self.service.registry.get(extra,include_paused=True))
        self.assertIsNotNone(self.service.registry.get(self.second))
        self.assertIsNotNone(queue.next())
        self.assertEqual(self.service.registry.remove_guild('1000'),0)
        fresh = self.service.registry.connect(12,'team/other',101,config(1000),'1','22')
        self.assertNotEqual(fresh,extra)

    def test_temporary_unavailability_network_errors_and_permission_errors_do_not_disconnect(self):
        self.service.guilds.removed(SimpleNamespace(id=1000,unavailable=True))
        for error in [ApiError('Discord',500),ApiError('Discord',429),ApiError('Discord','network'),
                      ApiError('Discord',403,50001),ApiError('Discord',404,10003)]:
            self.api.call.side_effect = error
            self.service.guilds.check()
            self.assertIsNotNone(self.service.registry.get(self.first))
            self.assertIsNotNone(self.service.registry.get(self.second))

    def test_membership_reconciliation_handles_removal_while_receiver_was_offline(self):
        def call(method,path):
            if path=='/guilds/1000':
                raise ApiError('Discord',404,10004)
            return {'id':'2000','name':'Remaining server'}
        self.api.call.side_effect=call
        self.service.guilds.check()
        self.assertIsNone(self.service.registry.get(self.first))
        self.assertIsNotNone(self.service.registry.get(self.second))
        self.assertEqual(self.service.operator.rows()[1]['guild_name'],'Remaining server')

    def test_forbidden_guild_is_removed_only_after_complete_membership_absence_is_confirmed(self):
        def call(method,path):
            if path == '/guilds/1000':
                raise ApiError('Discord',403,50001)
            if path.startswith('/users/@me/guilds?'):
                return [{'id':'2000','name':'Remaining'}]
            return {'id':'2000','name':'Remaining'}
        self.api.call.side_effect = call
        self.service.guilds.check()
        self.assertIsNone(self.service.registry.get(self.first))
        self.assertIsNotNone(self.service.registry.get(self.second))

    def test_admin_names_are_cached_and_old_membership_result_cannot_disable_new_connection(self):
        self.service.registry.save_guild_name('1000','Development server')
        self.service.registry.save_github_login('22','developer',12345)
        row = next(row for row in self.service.operator.rows() if row['id']==self.first)
        self.assertEqual((row['guild_name'],row['github_login']),('Development server','developer'))
        previous = [self.first]
        self.service.registry.disable(self.first)
        fresh = self.service.registry.connect(11,'team/repo',101,config(1000),'1','22')
        self.service.registry.remove_guild('1000',expected=previous)
        self.assertIsNotNone(self.service.registry.get(fresh))

