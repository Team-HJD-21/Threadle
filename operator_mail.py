"""Opt-in operational emails, delivered independently through a durable outbox."""
import hashlib
import logging
import re
import smtplib
import ssl
import threading
import time
from email.message import EmailMessage
from email.utils import formatdate, parseaddr

LOG = logging.getLogger('operator-mail')
MAX_ATTEMPTS = 6


def schema(db):
    db.execute('CREATE TABLE IF NOT EXISTS operator_mail_subscriptions(email TEXT PRIMARY KEY,subject TEXT NOT NULL,enabled INTEGER NOT NULL,updated REAL NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS operator_mail_outbox(binding TEXT NOT NULL,email TEXT NOT NULL,repo TEXT NOT NULL,guild TEXT NOT NULL,server_name TEXT NOT NULL,created REAL NOT NULL,status TEXT NOT NULL DEFAULT \'pending\',attempts INTEGER NOT NULL DEFAULT 0,next REAL NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT \'\',PRIMARY KEY(binding,email))')
    db.execute('CREATE INDEX IF NOT EXISTS operator_mail_due ON operator_mail_outbox(status,next)')


def address(value):
    if (not isinstance(value, str) or len(value) > 254
            or not re.fullmatch(r'[^@\s<>]+@[^@\s<>]+\.[^@\s<>]+', value)
            or parseaddr(value) != ('', value)):
        raise ValueError('Invalid operator email')
    return value.casefold()


def enqueue(db, binding_id, repo, guild):
    """Call only for a new connection, inside the registry's existing transaction."""
    guild = str(guild)
    server = ''
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='guild_profiles'").fetchone():
        row = db.execute('SELECT name FROM guild_profiles WHERE guild=?', (guild,)).fetchone()
        if row: server = row[0]
    db.execute('INSERT OR IGNORE INTO operator_mail_outbox(binding,email,repo,guild,server_name,created) '
               'SELECT ?,email,?,?,?,? FROM operator_mail_subscriptions WHERE enabled=1',
               (binding_id, repo, guild, server, time.time()))


class OperatorMail:
    def __init__(self, service, clock=time.time, sender=None):
        self.service, self.clock, self.sender = service, clock, sender
        self._worker = None
        with service.registry.db() as db: schema(db)

    def subscribed(self, email):
        email = address(email)
        with self.service.registry.db() as db:
            row = db.execute('SELECT enabled FROM operator_mail_subscriptions WHERE email=?', (email,)).fetchone()
        return bool(row and row[0])

    def subscribe(self, email, subject, enabled):
        # The HTTP layer supplies both values from verified Access claims, never form fields.
        email = address(email)
        if not isinstance(subject, str) or not subject or len(subject) > 500 or type(enabled) is not bool:
            raise ValueError('Invalid operator subscription')
        with self.service.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO operator_mail_subscriptions VALUES(?,?,?,?) ON CONFLICT(email) DO UPDATE SET subject=excluded.subject,enabled=excluded.enabled,updated=excluded.updated',
                       (email, subject, int(enabled), self.clock()))
            if not enabled:
                db.execute("UPDATE operator_mail_outbox SET status='canceled',last_error='' WHERE email=? AND status IN ('pending','retry','sending')", (email,))

    def _settings(self):
        env = self.service.env
        host = str(env.get('THREADLE_SMTP_HOST', '')).strip()
        sender = str(env.get('THREADLE_SMTP_FROM', '')).strip()
        try: port = int(env.get('THREADLE_SMTP_PORT', '587'))
        except (ValueError, TypeError): return None
        user, password = env.get('THREADLE_SMTP_USER', ''), env.get('THREADLE_SMTP_PASSWORD', '')
        if not host or any(c.isspace() for c in host) or port not in (465, 587) or '\r' in sender or '\n' in sender or bool(user) != bool(password):
            return None
        try: address(parseaddr(sender)[1])
        except ValueError: return None
        return host, port, sender, user, password

    def ready(self):
        return self.sender is not None or self._settings() is not None

    def eligible(self, email):
        operator = getattr(self.service, 'operator', None)
        settings = getattr(operator, 'settings', {})
        if getattr(operator, 'identity', None) is None or settings.get('enabled') is not True:
            return False
        allowed = settings.get('emails', [])
        return isinstance(allowed, list) and (not allowed or email.casefold() in [item.casefold() for item in allowed if isinstance(item, str)])

    def _send(self, message):
        if self.sender is not None: return self.sender(message)
        host, port, sender, user, password = self._settings()
        context = ssl.create_default_context()
        smtp = smtplib.SMTP_SSL(host, port, timeout=20, context=context) if port == 465 else smtplib.SMTP(host, port, timeout=20)
        with smtp:
            if port == 587:
                smtp.ehlo(); smtp.starttls(context=context); smtp.ehlo()
            if user: smtp.login(user, password)
            smtp.send_message(message, from_addr=parseaddr(sender)[1])

    def process_one(self):
        if not self.ready(): return False
        now = self.clock()
        with self.service.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE operator_mail_outbox SET status='failed',last_error='attempt-limit' WHERE status='sending' AND attempts>=? AND next<=?", (MAX_ATTEMPTS, now))
            row = db.execute("SELECT o.binding,o.email,o.repo,o.guild,o.server_name,o.created,o.attempts FROM operator_mail_outbox o JOIN operator_mail_subscriptions s ON s.email=o.email AND s.enabled=1 WHERE o.status IN ('pending','retry','sending') AND o.attempts<? AND o.next<=? ORDER BY o.created,o.rowid LIMIT 1", (MAX_ATTEMPTS, now)).fetchone()
            if not row: return False
            binding, email, repo, guild, server, created, attempts = row
            if not self.eligible(email):
                db.execute("UPDATE operator_mail_outbox SET status='canceled',last_error='not-authorized' WHERE binding=? AND email=?", (binding, email))
                return True
            attempts += 1
            db.execute("UPDATE operator_mail_outbox SET status='sending',attempts=?,next=? WHERE binding=? AND email=?", (attempts, now + 300, binding, email))
        # Recheck opt-out after acquiring the job, immediately before the SMTP handoff.
        with self.service.registry.db() as db:
            if not db.execute("SELECT 1 FROM operator_mail_outbox o JOIN operator_mail_subscriptions s ON s.email=o.email WHERE o.binding=? AND o.email=? AND o.status='sending' AND s.enabled=1", (binding, email)).fetchone(): return True
        if not self.eligible(email):
            with self.service.registry.db() as db:
                db.execute("UPDATE operator_mail_outbox SET status='canceled',last_error='not-authorized' WHERE binding=? AND email=?", (binding, email))
            return True
        message = EmailMessage()
        settings = self._settings()
        message['From'] = settings[2] if settings else 'Threadle <notifications@threadle.teamhjd.com>'
        message['To'] = email
        message['Subject'] = 'Threadle · 새로운 저장소 연결'
        message['Date'] = formatdate(created, usegmt=True)
        token = hashlib.sha256((binding + ':' + email).encode()).hexdigest()
        message['Message-ID'] = '<connection-' + token + '@threadle.teamhjd.com>'
        message.set_content('새 GitHub 저장소가 Discord 서버에 연결되었습니다.\n\n저장소: ' + repo + '\nGitHub: https://github.com/' + repo + '\nDiscord 서버: ' + (server or guild) + '\n서버 ID: ' + guild + '\n\n구독 해제: Threadle 관리자 페이지의 새 저장소 연결 이메일 알림을 꺼 주세요.\n')
        try:
            self._send(message)
        except Exception as error:
            kind = 'authentication' if isinstance(error, smtplib.SMTPAuthenticationError) else 'recipient' if isinstance(error, smtplib.SMTPRecipientsRefused) else 'transport'
            status = 'failed' if attempts >= MAX_ATTEMPTS else 'retry'
            delay = min(3600, 60 * 2 ** (attempts - 1))
            with self.service.registry.db() as db:
                db.execute("UPDATE operator_mail_outbox SET status=?,next=?,last_error=? WHERE binding=? AND email=? AND status='sending'", (status, self.clock() + delay, kind, binding, email))
            LOG.warning('Connection email delivery failed category=%s attempt=%s state=%s', kind, attempts, status)
        else:
            with self.service.registry.db() as db:
                db.execute("UPDATE operator_mail_outbox SET status='sent',last_error='' WHERE binding=? AND email=? AND status='sending'", (binding, email))
        return True

    def start(self, stop):
        if self._worker is not None and self._worker.is_alive(): return
        def run():
            while not stop.is_set():
                try: handled = self.process_one()
                except Exception:
                    LOG.warning('Connection email worker will retry')
                    handled = False
                stop.wait(1 if handled else 30)
        self._worker = threading.Thread(target=run, name='operator-mail', daemon=True)
        self._worker.start()

    def panel(self, session, claims):
        from connect_web import esc, form
        email = address(claims['email'])
        enabled = self.subscribed(email)
        with self.service.registry.db() as db:
            pending = db.execute("SELECT count(*) FROM operator_mail_outbox WHERE email=? AND status IN ('pending','retry','sending')", (email,)).fetchone()[0]
            failed = db.execute("SELECT count(*) FROM operator_mail_outbox WHERE email=? AND status='failed'", (email,)).fetchone()[0]
        body = '<section id="connection-email" class="card"><h2>새 저장소 연결 이메일</h2><p>구독 후 새로 연결되는 저장소를 로그인한 관리자 이메일로 알려드립니다.</p><p>' + esc(email) + ' · ' + ('구독 중' if enabled else '구독 안 함') + '</p>'
        body += '<p class="muted">' + ('발송 설정 완료' if self.ready() else '이메일 발송 설정 대기 중') + ' · 발송 대기 ' + str(pending) + '건 · 발송 실패 ' + str(failed) + '건</p>'
        body += form('/connect/admin/email-subscription', session, '<input type="hidden" name="enabled" value="' + ('no' if enabled else 'yes') + '"><button class="secondary">' + ('구독 해제' if enabled else '이메일 알림 구독') + '</button>')
        return body + '</section>'
