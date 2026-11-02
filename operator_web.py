import hashlib
import hmac
import json
import re
import shutil
import sqlite3
import time
import threading
from email.parser import BytesParser
from email import policy
from contextlib import closing
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from connect_auth import ConnectError


COOKIE = '__Host-threadle-admin'


def revision(row):
    values = {key: row[key] for key in ('id', 'config', 'enabled', 'paused', 'installation_enabled')}
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


class AccessIdentity:
    def __init__(self, settings):
        import jwt
        domain, audience = settings.get('team_domain', ''), settings.get('audience', '')
        if not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.cloudflareaccess\.com', domain):
            raise ValueError('Invalid Cloudflare team domain')
        if not re.fullmatch(r'[a-fA-F0-9]{64}', audience):
            raise ValueError('Invalid Cloudflare application audience')
        self.issuer, self.audience = 'https://' + domain, audience
        self.keys = jwt.PyJWKClient(self.issuer + '/cdn-cgi/access/certs', cache_keys=True, lifespan=300, timeout=3)

    def verify(self, token):
        import jwt
        key = self.keys.get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=['RS256'], audience=self.audience, issuer=self.issuer,
                            options={'require': ['exp', 'iat', 'sub', 'iss', 'aud', 'email']})
        if not isinstance(claims['email'], str) or not claims['email'] or len(claims['email']) > 254:
            raise ValueError('Invalid Access identity')
        return claims


class OperatorWeb:
    def __init__(self, service):
        self.service = service
        from admin_updates import Updates
        self.updates = Updates(service.root)
        self.identity = None
        self.settings = {}
        path = service.root / 'admin-access.json'
        if path.is_file():
            try:
                self.settings = json.loads(path.read_text(encoding='utf-8-sig'))
                if self.settings.get('enabled') is True:
                    self.identity = AccessIdentity(self.settings)
            except (ValueError, TypeError, KeyError):
                self.settings = {}

    def authenticate(self, request):
        token = request.headers.get('Cf-Access-Jwt-Assertion', '')
        if self.identity is None or not token or len(token) > 16384:
            raise ConnectError('관리자 인증이 필요합니다.', 403)
        try:
            claims = self.identity.verify(token)
            emails = self.settings.get('emails', [])
            if not isinstance(emails, list) or (emails and claims['email'].casefold() not in [v.casefold() for v in emails]):
                raise ValueError('Identity is not allowed')
            return claims
        except Exception:
            raise ConnectError('관리자 인증이 필요합니다.', 403) from None

    def rows(self):
        with self.service.registry.db() as db:
            db.row_factory = sqlite3.Row
            return [dict(row) for row in db.execute('SELECT b.*,i.enabled AS installation_enabled, '
                    'g.name AS guild_name, p.login AS github_login, '
                    '(SELECT github_user FROM connection_audit a WHERE a.binding=b.id AND a.action=\'connected\' ORDER BY a.id DESC LIMIT 1) AS github_user, '
                    'EXISTS(SELECT 1 FROM admin_paused s WHERE s.id=b.id) AS paused '
                    'FROM bindings b JOIN installations i ON i.id=b.installation '
                    'LEFT JOIN guild_profiles g ON g.guild=b.guild LEFT JOIN github_profiles p ON p.user='
                    '(SELECT github_user FROM connection_audit a WHERE a.binding=b.id AND a.action=\'connected\' ORDER BY a.id DESC LIMIT 1) '
                    'ORDER BY b.guild,b.repo')]

    def counts(self, row):
        path = self.service.root / 'queue.sqlite3' if row['legacy_state'] else self.service.root / 'connections' / row['id'] / 'queue.sqlite3'
        if not path.is_file():
            return 0, 0, 0
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
            jobs, retrying = db.execute('SELECT count(*),coalesce(sum(attempts>0),0) FROM jobs WHERE done=0').fetchone()
            notices = 0
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='notice_events'").fetchone():
                notices = db.execute('SELECT count(*) FROM notice_events WHERE sent=0').fetchone()[0]
        return jobs, retrying, notices

    def audit(self):
        with self.service.registry.db() as db:
            return db.execute('SELECT happened,actor,action,binding FROM admin_audit ORDER BY id DESC LIMIT 30').fetchall()

    def mutate(self, identity, action, expected, actor):
        if action not in ('pause', 'resume', 'disconnect') or not re.fullmatch('[a-f0-9]{32}', identity):
            raise ConnectError('올바른 관리 요청이 아닙니다.', 400)
        with self.service.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.row_factory = sqlite3.Row
            row = db.execute('SELECT b.*,i.enabled AS installation_enabled, '
                             'EXISTS(SELECT 1 FROM admin_paused p WHERE p.id=b.id) AS paused '
                             'FROM bindings b JOIN installations i ON i.id=b.installation WHERE b.id=?', (identity,)).fetchone()
            if row is None or not hmac.compare_digest(revision(row), expected):
                raise ConnectError('연결 상태가 변경됐습니다. 새로고침 후 다시 확인해 주세요.', 409)
            if not row['enabled'] or (action in ('pause', 'resume') and not row['installation_enabled']) or (action == 'pause' and row['paused']) or (action == 'resume' and not row['paused']):
                raise ConnectError('현재 상태에서 실행할 수 없습니다.', 409)
            if action == 'pause':
                db.execute('INSERT INTO admin_paused VALUES(?)', (identity,))
            else:
                db.execute('DELETE FROM admin_paused WHERE id=?', (identity,))
                if action == 'disconnect':
                    db.execute('UPDATE bindings SET enabled=0 WHERE id=?', (identity,))
            db.execute('INSERT INTO admin_audit(actor,action,binding) VALUES(?,?,?)', (actor, action, identity))

    @staticmethod
    def form(session, row, action, label):
        from connect_web import esc, form
        from web_locale import LANGUAGE
        if LANGUAGE.get() == 'en':
            label = {'pause': 'Pause', 'resume': 'Resume', 'disconnect': 'Disconnect'}[action]
        return form('/connect/admin/confirm', session, '<input type="hidden" name="binding" value="' + esc(row['id'])
                    + '"><input type="hidden" name="revision" value="' + revision(row) + '"><input type="hidden" name="action" value="'
                    + action + '"><button class="secondary">' + esc(label) + '</button>')

    def dashboard(self, session, claims, query):
        from connect_web import esc
        rows = self.rows()
        selected = [r for r in rows if query.casefold() in (r['repo'] + ' ' + r['guild'] + ' ' + (r.get('guild_name') or '') + ' ' + (r.get('github_login') or '')).casefold()]
        enabled = [r for r in rows if r['enabled'] and r['installation_enabled']]
        body = '<div class="admin-heading"><p>Threadle 운영 관리</p><p class="muted">' + esc(claims['email']) + '</p></div>'
        body += '<div class="admin-stats">' + ''.join('<div class="card"><strong>' + str(value) + '</strong><span>' + label + '</span></div>' for value, label in [(len({r['guild'] for r in enabled}), '연결된 서버'), (len({r['repo_id'] for r in enabled}), '연결된 저장소'), (len(enabled), '서버별 연결'), (sum(r['paused'] for r in enabled), '일시중지')]) + '</div>'
        body += '<form method="get" action="/connect/admin" class="admin-search"><label for="admin-query">저장소 또는 서버 ID 검색</label><input id="admin-query" name="q" maxlength="100" value="' + esc(query) + '"><button>검색</button><a class="button secondary" href="/connect/admin">전체 보기</a></form>'
        body += '<p class="muted">일시중지 중에도 새 웹후크는 대기열에 보관합니다. 재개하면 순서대로 처리합니다. 이미 전송 중인 요청 하나는 완료될 수 있습니다.</p>'
        for heading, group in [('현재 연결', [r for r in selected if r['enabled']]), ('연결 해제 기록', [r for r in selected if not r['enabled']])]:
            body += '<section class="admin-connection-group"><h2>' + heading + ' (' + str(len(group)) + ')</h2>'
            if len(group) > 50:
                body += '<p class="muted">처음 50건을 표시합니다. 검색으로 범위를 좁혀 주세요.</p>'
            if not group:
                body += '<p class="muted">표시할 연결이 없습니다.</p>'
            for row in group[:50]:
                status = '연결 해제' if not row['enabled'] else 'GitHub 설치 중지' if not row['installation_enabled'] else '일시중지' if row['paused'] else '동작 중'
                body += '<article id="connection-' + esc(row['id']) + '" class="card admin-binding"><div class="admin-binding-title"><h2>' + esc(row['repo']) + '</h2><span class="admin-status">' + status + '</span></div><p class="muted">Discord 서버 ID ' + esc(row['guild']) + ' · ' + esc(row['auth']) + '</p>'
                try:
                    pending, retries, notices = self.counts(row)
                    body += '<p>대기 이벤트 <b>' + str(pending) + '</b> · 재시도 중 <b>' + str(retries) + '</b> · 예약/묶음 알림 <b>' + str(notices) + '</b></p>'
                except sqlite3.Error:
                    body += '<p class="error">대기열 상태를 읽지 못했습니다.</p>'
                body += '<p>서버: <b>' + esc(row.get('guild_name') or '이름 확인 대기') + '</b> · GitHub: <b>' + esc(('@' + row['github_login']) if row.get('github_login') else ('ID ' + row['github_user']) if row.get('github_user') else '확인할 수 없음') + '</b></p>'
                body += '<p class="muted">연결 ID <code>' + esc(row['id']) + '</code></p>'
                if row['enabled']:
                    body += '<div class="admin-actions">'
                    if row['installation_enabled']:
                        body += self.form(session, row, 'resume' if row['paused'] else 'pause', '재개' if row['paused'] else '일시중지')
                    body += self.form(session, row, 'disconnect', '연결 해제') + '</div>'
                body += '</article>'
            body += '</section>'
        with self.service.registry.db() as db:
            inbox = db.execute('SELECT count(*) FROM inbox WHERE done=0').fetchone()[0]
        body += '<p class="muted">분배 대기 웹후크 ' + str(inbox) + ' · 디스크 여유 ' + str(round(shutil.disk_usage(self.service.root).free / 1024**3, 1)) + ' GB · 조회 시각 ' + time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()) + '</p>'
        body += self.update_panel(session)
        body += '<details class="card"><summary>최근 관리자 작업</summary><ul class="admin-audit">'
        for happened, actor, action, binding in self.audit():
            body += '<li>' + esc(happened) + ' UTC · ' + esc(actor) + ' · ' + esc(action) + ' · <code>' + esc(binding) + '</code></li>'
        return body + '</ul></details>'

    def update_panel(self, session):
        from connect_web import esc, form
        job = self.updates.latest()
        body = '<section id="service-update" class="card"><h2>Threadle 업데이트</h2><p>서명된 업데이트 ZIP을 올려 주세요. 인증 정보·연결·대기열을 보존하고, 적용 실패 시 이전 파일을 복구합니다. 적용 중 서비스가 잠깐 재시작됩니다.</p>'
        try:
            sync = json.loads((self.service.root/'command-sync.json').read_text(encoding='utf-8'))
            label = 'Discord 등록 확인됨' if sync.get('state') == 'registered' else 'Discord 명령어 등록 재시도 중'
            body += '<p>명령어 동기화: <b>' + label + '</b></p>'
        except (OSError,ValueError):
            body += '<p class="muted">명령어 동기화 확인 대기</p>'
        if job:
            body += '<p data-update-status="' + esc(job['state']) + '">' + esc(job['state']) + ' · ' + esc(job['version']) + '</p><p data-update-detail>' + esc(job['detail']) + '</p>'
            if job['state'] == 'staged':
                hidden = '<input type="hidden" name="job" value="' + esc(job['id']) + '">'
                body += form('/connect/admin/update/apply',session,hidden + '<label>적용하려면 UPDATE 입력<input name="confirmation" required pattern="UPDATE" autocomplete="off"></label><button>이 버전 적용</button>')
                body += form('/connect/admin/update/cancel',session,hidden + '<button class="secondary">이 업데이트 취소</button>')
        if not job or job['state'] not in ('staged','scheduled','applying','restarting'):
            body += '<form method="post" action="/connect/admin/update/upload" enctype="multipart/form-data"><input type="hidden" name="csrf" value="' + esc(session.data['csrf']) + '"><label for="update-package">업데이트 ZIP</label><input id="update-package" type="file" name="package" accept=".zip,application/zip" required><button>업로드 및 검사</button></form>'
        return body + '<p class="muted">업로드 후 버전을 확인하고 적용합니다. 재시작 중에는 이 화면에서 상태를 기다려 주세요.</p></section><script src="/connect/assets/admin-update.js"></script>'

    def update_request(self, request, session, claims, path):
        from admin_updates import MAX_UPLOAD
        web = self.service.web
        if request.command == 'GET' and path == '/connect/admin/update/status':
            return web.send(request,200,json.dumps({'job': self.updates.latest()}),content_type='application/json; charset=utf-8')
        if request.command != 'POST' or not session or request.headers.get('Origin') != web.settings.base or request.headers.get('Transfer-Encoding'):
            raise ConnectError('요청을 확인할 수 없습니다.',403)
        length = int(request.headers.get('Content-Length',0))
        limit = MAX_UPLOAD + 65536 if path.endswith('/upload') else 4096
        if not 0 < length <= limit:
            raise ConnectError('업로드 크기를 확인해 주세요.',413)
        raw = request.rfile.read(length)
        if len(raw) != length:
            raise ConnectError('업로드가 완료되지 않았습니다.',400)
        try:
            if path == '/connect/admin/update/upload':
                content_type = request.headers.get('Content-Type','')
                if not content_type.startswith('multipart/form-data;') or '\r' in content_type or '\n' in content_type:
                    raise ValueError('Use the update ZIP upload form.')
                message = BytesParser(policy=policy.default).parsebytes(('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode()+raw)
                if not message.is_multipart():
                    raise ValueError('Invalid upload format.')
                parts = list(message.iter_parts())
                fields = {}
                for part in parts:
                    name = part.get_param('name',header='content-disposition')
                    if part.get_content_disposition() != 'form-data' or name not in ('csrf','package') or name in fields or part.is_multipart():
                        raise ValueError('Invalid or duplicate upload fields.')
                    fields[name] = part.get_payload(decode=True)
                if set(fields) != {'csrf','package'} or not hmac.compare_digest(session.data['csrf'].encode(),fields['csrf']):
                    raise ConnectError('관리자 화면을 새로고침 후 다시 시도해 주세요.',403)
                self.updates.stage(fields['package'],claims['email'])
            else:
                if not request.headers.get('Content-Type','').startswith('application/x-www-form-urlencoded'):
                    raise ValueError('Invalid update request.')
                fields = parse_qs(raw.decode(),max_num_fields=5,keep_blank_values=True)
                if any(len(value)!=1 for value in fields.values()):
                    raise ValueError('Duplicate update fields.')
                fields = {key:value[0] for key,value in fields.items()}
                if not hmac.compare_digest(session.data['csrf'],fields.get('csrf','')):
                    raise ConnectError('관리자 화면을 새로고침 후 다시 시도해 주세요.',403)
                latest = self.updates.latest()
                if not latest or latest['id'] != fields.get('job') or latest['state'] != 'staged':
                    raise ValueError('This update is no longer waiting for confirmation.')
                if path == '/connect/admin/update/cancel':
                    self.updates.cancel(latest['id'])
                elif path == '/connect/admin/update/apply' and fields.get('confirmation') == 'UPDATE':
                    self.updates.launch(latest['id'])
                else:
                    raise ValueError('Type UPDATE to confirm this release.')
        except ValueError as error:
            raise ConnectError(str(error),400) from None
        return web.send(request,303,location='/connect/admin#service-update')

    def handle(self, request):
        from connect_web import esc, form, page
        web = self.service.web
        web.throttle('operator:' + web.client_key(request))
        claims = self.authenticate(request)
        cookies = SimpleCookie()
        cookies.load(request.headers.get('Cookie', ''))
        session = web.sessions.get(cookies[COOKIE].value) if COOKIE in cookies else None
        if session and session.data.get('admin_subject') != claims['sub']:
            session = None
        path = urlsplit(request.path).path
        if path.startswith('/connect/admin/update/'):
            return self.update_request(request,session,claims,path)
        if request.command == 'GET' and path == '/connect/admin':
            session = session or web.sessions.create({'admin_subject': claims['sub']})
            query = parse_qs(urlsplit(request.path).query, max_num_fields=2).get('q', [''])[0][:100]
            body = page('운영 관리자', self.dashboard(session, claims, query))
            data = body.encode()
            request.send_response(200)
            for name, value in {'Content-Type': 'text/html; charset=utf-8', 'Content-Length': str(len(data)), 'Cache-Control': 'no-store', 'X-Robots-Tag': 'noindex, nofollow', 'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin', 'Content-Security-Policy': "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; script-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'", 'Set-Cookie': COOKIE + '=' + session.key + '; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age=1800'}.items():
                request.send_header(name, value)
            if getattr(request, 'ui_language_choice', None):
                from web_locale import COOKIE as LANGUAGE_COOKIE
                request.send_header('Set-Cookie', LANGUAGE_COOKIE + '=' + request.ui_language_choice + '; Path=/; Secure; SameSite=Lax; Max-Age=31536000')
            request.end_headers()
            request.wfile.write(data)
            return
        if request.command != 'POST' or path not in ('/connect/admin/confirm', '/connect/admin/action'):
            raise ConnectError('요청을 찾을 수 없습니다.', 404)
        if request.headers.get('Origin') != web.settings.base or request.headers.get('Transfer-Encoding'):
            raise ConnectError('요청을 확인할 수 없습니다.', 403)
        length = int(request.headers.get('Content-Length', 0))
        if not 0 < length <= 4096 or not request.headers.get('Content-Type', '').startswith('application/x-www-form-urlencoded'):
            raise ConnectError('요청을 확인할 수 없습니다.', 400)
        fields = parse_qs(request.rfile.read(length).decode(), max_num_fields=8, keep_blank_values=True)
        if any(len(v) != 1 for v in fields.values()):
            raise ConnectError('중복 입력은 허용하지 않습니다.', 400)
        fields = {k: v[0] for k, v in fields.items()}
        if not session or not hmac.compare_digest(session.data['csrf'], fields.get('csrf', '')):
            raise ConnectError('관리자 화면을 새로고침 후 다시 시도해 주세요.', 403)
        row = next((r for r in self.rows() if r['id'] == fields.get('binding')), None)
        action = fields.get('action')
        if row is None or action not in ('pause', 'resume', 'disconnect') or not hmac.compare_digest(revision(row), fields.get('revision', '')):
            raise ConnectError('연결 상태가 변경됐습니다. 새로고침해 주세요.', 409)
        if path == '/connect/admin/action':
            if fields.get('confirmation') != row['repo']:
                raise ConnectError('확인 입력이 저장소 이름과 다릅니다.', 400)
            self.mutate(row['id'], action, fields['revision'], claims['email'])
            return web.send(request, 303, location='/connect/admin#connection-' + row['id'])
        labels = {'pause': '일시중지', 'resume': '재개', 'disconnect': '연결 해제'}
        from web_locale import LANGUAGE
        label = {'pause': 'Pause', 'resume': 'Resume', 'disconnect': 'Disconnect'}[action] if LANGUAGE.get() == 'en' else labels[action]
        warning = '알림 연결만 해제합니다. Discord 채널·스레드와 기록은 삭제하지 않습니다. 사용자는 나중에 다시 연결할 수 있습니다.' if action == 'disconnect' else '대기열과 기존 설정을 보존합니다.'
        fields_html = ''.join('<input type="hidden" name="' + k + '" value="' + esc(fields[k]) + '">' for k in ('binding', 'action', 'revision'))
        body = '<div class="card"><h2>' + esc(row['repo']) + ' · ' + label + '</h2><p>' + warning + '</p><p>확인하려면 저장소 이름을 입력해 주세요: <code>' + esc(row['repo']) + '</code></p>'
        body += form('/connect/admin/action', session, fields_html + '<label for="confirmation">저장소 이름</label><input id="confirmation" name="confirmation" required autocomplete="off"><button>' + label + '</button>') + '<a class="button secondary" href="/connect/admin">취소</a></div>'
        return web.send(request, 200, page('관리 작업 확인', body))
