import smtplib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from operator_mail import MAX_ATTEMPTS, OperatorMail, enqueue
from registry import Registry


class OperatorMailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.clock = Mock(return_value=1000)
        self.sender = Mock()
        self.service = SimpleNamespace(root=Path(self.temp.name), env={}, registry=Registry(Path(self.temp.name)/'registry.db'))
        self.service.operator = SimpleNamespace(identity=object(), settings={'enabled':True, 'emails':[]})
        self.mail = OperatorMail(self.service, clock=self.clock, sender=self.sender)

    def tearDown(self): self.temp.cleanup()

    def connect(self, identity='connection', repo='owner/repo', guild='123'):
        with self.service.registry.db() as db: enqueue(db, identity, repo, guild)

    def rows(self):
        with self.service.registry.db() as db:
            return db.execute('SELECT binding,email,status,attempts,next,last_error FROM operator_mail_outbox ORDER BY binding,email').fetchall()

    def test_no_mail_before_opt_in_or_historical_backfill(self):
        self.connect()
        self.mail.subscribe('admin@example.com', 'verified-subject', True)
        self.assertEqual(self.rows(), [])
        self.assertFalse(self.mail.process_one())
        self.sender.assert_not_called()

    def test_enqueue_only_current_subscribers_and_snapshot_server(self):
        self.mail.subscribe('Admin@example.com', 'verified-subject', True)
        self.mail.subscribe('disabled@example.com', 'other-subject', False)
        with self.service.registry.db() as db:
            db.execute('INSERT INTO guild_profiles VALUES(?,?)', ('123', 'Test server'))
        self.connect(); self.connect()
        self.assertEqual(len(self.rows()), 1)
        self.assertTrue(self.mail.process_one())
        message = self.sender.call_args.args[0]
        self.assertEqual(message['To'], 'admin@example.com')
        self.assertIn('owner/repo', message.get_content())
        self.assertIn('Test server', message.get_content())
        self.assertFalse(self.mail.process_one())
        self.assertEqual(self.rows()[0][2], 'sent')

    def test_unsubscribe_cancels_pending_and_resubscribe_does_not_backfill(self):
        self.mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.mail.subscribe('admin@example.com', 'subject', False)
        self.assertFalse(self.mail.process_one())
        self.mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.assertFalse(self.mail.process_one())
        self.assertEqual(self.rows()[0][2], 'canceled')
        self.connect('new-connection')
        self.assertTrue(self.mail.process_one())
        self.assertEqual(self.sender.call_count, 1)

    def test_failed_smtp_is_bounded_and_does_not_log_addresses_or_credentials(self):
        self.mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.sender.side_effect = smtplib.SMTPAuthenticationError(535, b'admin@example.com password=private')
        with self.assertLogs('operator-mail', level='WARNING') as logs:
            for attempt in range(MAX_ATTEMPTS):
                self.assertTrue(self.mail.process_one())
                row = self.rows()[0]
                self.assertEqual(row[3], attempt + 1)
                self.clock.return_value = row[4]
        self.assertEqual(self.rows()[0][2], 'failed')
        self.assertFalse(self.mail.process_one())
        output = '\n'.join(logs.output)
        self.assertNotIn('admin@example.com', output); self.assertNotIn('private', output)
        self.assertEqual(self.rows()[0][5], 'authentication')

    def test_retry_reuses_message_id_and_waits_for_backoff(self):
        self.mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.sender.side_effect = [OSError('network'), None]
        with self.assertLogs('operator-mail', level='WARNING'): self.mail.process_one()
        first = self.sender.call_args.args[0]['Message-ID']
        self.assertFalse(self.mail.process_one())
        self.clock.return_value = self.rows()[0][4]
        self.assertTrue(self.mail.process_one())
        self.assertEqual(self.sender.call_args.args[0]['Message-ID'], first)
        self.assertEqual(self.rows()[0][2], 'sent')

    def test_expired_send_lease_recovers_but_attempt_limit_is_terminal(self):
        self.mail.subscribe('admin@example.com', 'subject', True); self.connect()
        with self.service.registry.db() as db:
            db.execute("UPDATE operator_mail_outbox SET status='sending',attempts=1,next=1100")
        self.assertFalse(self.mail.process_one())
        self.clock.return_value = 1100
        self.assertTrue(self.mail.process_one())
        self.connect('other')
        with self.service.registry.db() as db:
            db.execute("UPDATE operator_mail_outbox SET status='sending',attempts=?,next=1100 WHERE binding='other'", (MAX_ATTEMPTS,))
        self.assertFalse(self.mail.process_one())
        self.assertEqual(self.rows()[1][2], 'failed')

    def test_unconfigured_transport_preserves_pending_jobs(self):
        mail = OperatorMail(self.service, clock=self.clock)
        mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.assertFalse(mail.ready()); self.assertFalse(mail.process_one())
        self.assertEqual(self.rows()[0][2:4], ('pending', 0))

    def test_removed_admin_or_disabled_access_cannot_receive_mail(self):
        self.mail.subscribe('admin@example.com', 'subject', True)
        for identity, settings in [(object(), {'enabled':True, 'emails':['other@example.com']}),
                                   (object(), {'enabled':False, 'emails':[]}),
                                   (None, {'enabled':True, 'emails':[]})]:
            with self.subTest(settings=settings, identity=identity):
                binding = str(len(self.rows()))
                self.connect(binding)
                self.service.operator = SimpleNamespace(identity=identity, settings=settings)
                self.assertTrue(self.mail.process_one())
                self.assertEqual(self.rows()[-1][2], 'canceled')
        self.sender.assert_not_called()

    def test_panel_uses_verified_email_and_has_no_recipient_input(self):
        session = SimpleNamespace(data={'csrf':'csrf-value'})
        panel = OperatorMail(self.service).panel(session, {'email':'admin@example.com'})
        self.assertIn('admin@example.com', panel)
        self.assertIn('이메일 발송 설정 대기 중', panel)
        self.assertIn('/connect/admin/email-subscription', panel)
        self.assertIn('name="csrf"', panel)
        self.assertIn('id="connection-email"', panel)
        self.assertNotIn('name="email"', panel)
        self.assertNotIn('THREADLE_SMTP', panel)

    def test_registry_connect_queues_only_new_connections_after_opt_in(self):
        from test_service import config
        registry = self.service.registry
        old = registry.connect(11, 'owner/old', 101, config(1000), 'discord-user', 'github-user')
        self.mail.subscribe('admin@example.com', 'subject', True)
        self.assertEqual(registry.connect(11, 'owner/old', 101, config(1000), 'discord-user', 'github-user'), old)
        self.assertEqual(self.rows(), [])
        new = registry.connect(22, 'owner/new', 101, config(1000), 'discord-user', 'github-user')
        self.assertEqual(registry.connect(22, 'owner/new', 101, config(1000), 'discord-user', 'github-user'), new)
        self.assertEqual([(row[0], row[1]) for row in self.rows()], [(new, 'admin@example.com')])

    def test_registry_connection_and_mail_enqueue_roll_back_together(self):
        from test_service import config
        self.mail.subscribe('admin@example.com', 'subject', True)
        def fail_after_enqueue(db, *args):
            enqueue(db, *args)
            raise RuntimeError('interrupted connection')
        with patch('operator_mail.enqueue', side_effect=fail_after_enqueue):
            with self.assertRaises(RuntimeError):
                self.service.registry.connect(99, 'owner/interrupted', 101, config(1000), 'discord-user', 'github-user')
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM bindings WHERE repo_id=99').fetchone()[0], 0)
        self.assertEqual(self.rows(), [])

    def test_header_injection_and_invalid_subscription_are_rejected(self):
        for email in ('admin@example.com\r\nBcc: other@example.com', '', 'invalid',
                      'local,other@example.com', 'local;other@example.com'):
            with self.assertRaises(ValueError): self.mail.subscribe(email, 'subject', True)
        with self.assertRaises(ValueError): self.mail.subscribe('admin@example.com', '', True)
        with self.assertRaises(ValueError): self.mail.subscribe('admin@example.com', 'subject', 'yes')

    def test_smtp_uses_tls_before_authentication(self):
        self.service.env.update(THREADLE_SMTP_HOST='smtp.example.com', THREADLE_SMTP_FROM='Threadle <bot@example.com>', THREADLE_SMTP_USER='user', THREADLE_SMTP_PASSWORD='secret')
        mail = OperatorMail(self.service, clock=self.clock)
        mail.subscribe('admin@example.com', 'subject', True); self.connect()
        self.assertTrue(mail.ready())
        with patch('operator_mail.smtplib.SMTP') as smtp:
            self.assertTrue(mail.process_one())
            client = smtp.return_value
            self.assertEqual([call[0] for call in client.method_calls], ['ehlo','starttls','ehlo','login','send_message'])
        self.service.env['THREADLE_SMTP_PORT'] = '25'
        self.assertFalse(mail.ready())

    def test_ssl_port_and_partial_credentials(self):
        self.service.env.update(THREADLE_SMTP_HOST='smtp.example.com', THREADLE_SMTP_PORT='465', THREADLE_SMTP_FROM='bot@example.com')
        mail = OperatorMail(self.service, clock=self.clock)
        mail.subscribe('admin@example.com', 'subject', True); self.connect()
        with patch('operator_mail.smtplib.SMTP_SSL') as smtp:
            self.assertTrue(mail.process_one())
            smtp.assert_called_once()
            smtp.return_value.starttls.assert_not_called()
        self.service.env['THREADLE_SMTP_USER'] = 'user'
        self.assertFalse(mail.ready())


from test_connect import WebFixture


class OperatorMailWebTests(WebFixture):
    def setUp(self):
        super().setUp()
        import time
        from cryptography.hazmat.primitives.asymmetric import rsa
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.claims = {'sub':'operator-one', 'email':'owner@example.com', 'aud':'a'*64,
                       'iss':'https://example.cloudflareaccess.com', 'iat':int(time.time()), 'exp':int(time.time())+300}
        self.cookie = None
        from test_operator_web import OperatorTests
        OperatorTests.enable(self)
        self.service.operator.settings['enabled'] = True

    def request(self, *args, **kwargs):
        from test_operator_web import OperatorTests
        return OperatorTests.request(self, *args, **kwargs)

    def form(self, **values):
        return {'enabled':'yes', 'csrf':self.web.sessions.get(self.cookie).data['csrf'], **values}

    def test_subscription_uses_verified_claims_and_never_form_recipient(self):
        self.assertEqual(self.request()[0], 200)
        result = self.request('POST', '/connect/admin/email-subscription', self.form(email='attacker@example.com', subject='forged-subject'))
        self.assertEqual(result[0], 303)
        self.assertEqual(result[1]['Location'], '/connect/admin#connection-email')
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT email,subject,enabled FROM operator_mail_subscriptions').fetchall(), [('owner@example.com','operator-one',1)])
            self.assertEqual(db.execute('SELECT count(*) FROM operator_mail_outbox').fetchone()[0], 0)
        self.assertFalse(self.service.operator_mail.ready())

    def test_subscription_requires_csrf_origin_and_authorized_identity(self):
        self.assertEqual(self.request()[0], 200)
        path = '/connect/admin/email-subscription'
        self.assertEqual(self.request('POST', path, self.form(csrf='wrong'))[0], 403)
        self.assertEqual(self.request('POST', path, self.form(), headers={'Origin':'https://evil.example'})[0], 403)
        self.assertEqual(self.request('POST', path, self.form(), claims=dict(self.claims,email='other@example.com'))[0], 403)
        self.assertEqual(self.request('POST', path, self.form(enabled='invalid'))[0], 400)
        self.assertFalse(self.service.operator_mail.subscribed('owner@example.com'))

    def test_http_unsubscribe_cancels_new_connection_mail(self):
        from test_service import config
        self.assertEqual(self.request()[0], 200)
        path = '/connect/admin/email-subscription'
        self.assertEqual(self.request('POST', path, self.form())[0], 303)
        self.service.registry.connect(22, 'owner/new', 101, config(1000), 'discord-user', 'github-user')
        self.assertEqual(self.request('POST', path, self.form(enabled='no'))[0], 303)
        with self.service.registry.db() as db:
            self.assertEqual(db.execute('SELECT status FROM operator_mail_outbox').fetchall(), [('canceled',)])


if __name__ == '__main__': unittest.main()
