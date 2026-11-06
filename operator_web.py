import hashlib
import hmac
import json
import re
import shutil
import state_db as sqlite3
import time
import threading
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
        from operator_presentation import heading,stats,queue_counts,event_history,t
        from operator_accounts import panel as accounts_panel
        from operator_settings import details
        from guild_plans import subscription
        from delivery_metrics import summary
        from state_backups import latest
        rows = self.rows()
        plans = {}
        selected = [r for r in rows if query.casefold() in (r['repo'] + ' ' + r['guild'] + ' ' + (r.get('guild_name') or '') + ' ' + (r.get('github_login') or '')).casefold()]
        body='<div class="operator-dashboard" data-operator-dashboard>'+heading(claims['email'])+stats(rows)
        body+='<section class="operator-section" id="operator-connections"><div class="operator-section-heading"><div><p class="operator-eyebrow">CONNECTIONS</p><h2>'+t('서버와 저장소 연결','Servers & repositories')+'</h2></div><span>'+t('검색 결과','Matching connections')+' '+str(len(selected))+'</span></div>'
        body+='<form method="get" action="/connect/admin" class="admin-search"><label for="admin-query">'+t('서버 이름·저장소·서버 ID','Server name, repository or server ID')+'</label><div><input id="admin-query" name="q" maxlength="100" placeholder="'+t('이름이나 ID로 찾기','Search by name or ID')+'" value="'+esc(query)+'"><button>'+t('검색','Search')+'</button><a class="button secondary" href="/connect/admin">'+t('전체 보기','Show all')+'</a></div></form>'
        body+='<p class="operator-context-note">'+t('같은 저장소도 연결된 서버마다 설정과 처리 상태가 다릅니다. 일시중지 중에는 이벤트를 보관하고, 재개하면 순서대로 처리합니다.','Each server connection has its own preferences and delivery state, even for the same repository. Paused connections retain events and resume in order.')+'</p>'
        for heading_text,group in [(t('현재 연결','Current connections'),[r for r in selected if r['enabled']]),(t('연결 해제 기록','Disconnected history'),[r for r in selected if not r['enabled']])]:
            history=bool(group and not group[0]['enabled'])
            body+=('<details class="operator-history"><summary>'+heading_text+' <span>'+str(len(group))+'</span></summary>' if history else '<section class="admin-connection-group"><h3>'+heading_text+' <span>'+str(len(group))+'</span></h3>')
            if len(group)>50:body+='<p class="muted">'+t('처음 50건입니다. 검색으로 범위를 좁혀 주세요.','Showing the first 50. Use search to narrow the results.')+'</p>'
            if not group:body+='<div class="operator-empty">'+t('표시할 연결이 없습니다.','No connections match this view.')+'</div>'
            body+='<div class="admin-binding-grid">'
            for row in group[:50]:
                active=bool(row['enabled'] and row['installation_enabled'] and not row['paused'])
                status=t('연결 해제','Disconnected') if not row['enabled'] else t('GitHub 설치 중지','Installation suspended') if not row['installation_enabled'] else t('일시중지','Paused') if row['paused'] else t('동작 중','Active')
                status_class='active' if active else 'paused' if row['enabled'] else 'disconnected'
                body+='<article id="connection-'+esc(row['id'])+'" class="card admin-binding"><div class="operator-binding-server"><span>'+t('DISCORD 서버','DISCORD SERVER')+'</span><strong>'+esc(row.get('guild_name') or t('이름 확인 대기','Name unavailable'))+'</strong></div><div class="admin-binding-title"><h2>'+esc(row['repo'])+'</h2><span class="admin-status status-'+status_class+'">'+status+'</span></div>'
                if row['guild'] not in plans:
                    plans[row['guild']]=subscription(self.service.registry,row['guild'])
                plan=plans[row['guild']]
                subscription_text={'none':t('구독 없음 · 베타 무료 이용','No subscription · free beta access'),'active':t('구독 중','Active subscription'),'trialing':t('체험 중','Trial'),'past_due':t('결제 확인 필요','Payment attention'),'canceled':t('구독 종료','Canceled'),'expired':t('기간 만료','Expired')}.get(plan['status'],esc(plan['status']))
                body+='<div class="operator-binding-plan"><span>'+esc(plan['plan'].title())+'</span><small>'+subscription_text+'</small></div>'
                try:
                    pending,retries,notices=self.counts(row)
                    body+=queue_counts(pending,retries,notices,active)
                except sqlite3.Error:body+='<p class="error">'+t('대기열 상태를 읽지 못했습니다.','Queue status could not be read.')+'</p>'
                if not row['enabled']:body+='<p class="operator-context-note">'+t('연결 해제 당시의 기록입니다. 이 연결의 이벤트는 처리하지 않습니다.','Historical counts from this disconnected connection. Its events are not processed.')+'</p>'
                body+=details(json.loads(row['config']))
                state=self.service.root if row['legacy_state'] else self.service.root/'connections'/row['id']
                metrics=summary((state/'queue.sqlite3').resolve())
                body+='<details class="operator-diagnostics"><summary>'+t('연결 정보·전송 측정','Connection details & timing')+'</summary><dl><div><dt>Discord ID</dt><dd><code>'+esc(row['guild'])+'</code></dd></div><div><dt>GitHub</dt><dd>'+esc(('@'+row['github_login']) if row.get('github_login') else ('ID '+row['github_user']) if row.get('github_user') else '—')+'</dd></div><div><dt>'+t('연결 ID','Connection ID')+'</dt><dd><code>'+esc(row['id'])+'</code></dd></div><div><dt>'+t('인증 방식','Authentication')+'</dt><dd>'+esc(row['auth'])+'</dd></div></dl>'
                if metrics:
                    from web_locale import LANGUAGE
                    text=(f"First Discord write: median {metrics['p50']:.2f}s · P95 {metrics['p95']:.2f}s · {metrics['count']} events; queue {metrics['queue_p50']:.2f}s / processing {metrics['processing_p50']:.2f}s" if LANGUAGE.get()=='en' else f"첫 전송·갱신: 중앙값 {metrics['p50']:.2f}초 · P95 {metrics['p95']:.2f}초 · {metrics['count']}건; 대기 {metrics['queue_p50']:.2f}초 / 처리 {metrics['processing_p50']:.2f}초")
                    body+='<p class="muted">'+esc(text)+'</p>'
                else:body+='<p class="muted">'+t('전송 지연 측정 대기','Waiting for delivery timing samples')+'</p>'
                body+='</details>'
                if row['enabled']:
                    body+='<div class="admin-actions">'
                    if row['installation_enabled']:body+=self.form(session,row,'resume' if row['paused'] else 'pause','재개' if row['paused'] else '일시중지')
                    body+=self.form(session,row,'disconnect','연결 해제')+'</div>'
                body+='</article>'
            body+='</div>'+('</details>' if history else '</section>')
        body+='</section>'+accounts_panel(self.service.registry,selected)
        from api_usage import panel
        body+='<section class="operator-section" id="operator-tools"><div class="operator-section-heading"><div><p class="operator-eyebrow">SERVICE TOOLS</p><h2>'+t('API와 운영 알림','API & operator notifications')+'</h2></div></div><p class="muted">'+t('API 사용량은 조회 버튼을 눌렀을 때만 외부 서비스에서 갱신합니다.','External API usage is refreshed only when you press its refresh button.')+'</p><div class="operator-tool-grid">'+panel(self.service.api_usage,session)+self.service.operator_mail.panel(session,claims)+'</div></section>'
        body+=event_history(self.service)
        with self.service.registry.db() as db:inbox=db.execute('SELECT count(*) FROM inbox WHERE done=0').fetchone()[0]
        snapshot=latest(self.service.root)
        body+='<section class="operator-section operator-system"><div class="operator-section-heading"><h2>'+t('시스템 정보','System information')+'</h2></div><div class="operator-system-grid"><article><span>'+t('분배 대기 웹후크','Undistributed webhooks')+'</span><b>'+str(inbox)+'</b></article><article><span>'+t('디스크 여유','Free disk space')+'</span><b>'+str(round(shutil.disk_usage(self.service.root).free/1024**3,1))+' GB</b></article><article><span>'+t('명령어 등록','Command registration')+'</span>'+self.command_status()+'</article></div>'
        if snapshot:
            stamp=time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(snapshot['created']))
            body+='<p class="muted">'+t('마지막 DB 백업·복원 검증','Last DB backup / restore check')+': '+stamp+' · DB '+str(snapshot['files'])+'</p>'
        body+='<p class="muted">'+t('화면 조회 시각','Page loaded')+' · '+time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime())+'</p></section>'
        body+='<details class="operator-history" id="admin-audit"><summary>'+t('최근 관리자 작업','Recent administrator actions')+'</summary><ul class="admin-audit">'
        audit=self.audit()
        for happened,actor,action,binding in audit:body+='<li><time>'+esc(happened)+' UTC</time><b>'+esc(actor)+'</b><span>'+esc(action)+'</span><code>'+esc(binding)+'</code></li>'
        if not audit:body+='<li>'+t('아직 기록된 관리자 작업이 없습니다.','No administrator actions recorded yet.')+'</li>'
        return body+'</ul></details></div>'

    def command_status(self):
        from connect_web import esc
        from web_locale import LANGUAGE
        en=LANGUAGE.get()=='en'
        try:
            sync=json.loads((self.service.root/'command-sync.json').read_text('utf-8'))
            text=('Discord registration verified' if en else 'Discord 등록 확인됨') if sync.get('state')=='registered' else ('Retrying Discord command registration' if en else 'Discord 명령어 등록 재시도 중')
        except (OSError,ValueError):text='Waiting for command sync' if en else '명령어 동기화 확인 대기'
        return '<p>'+esc(text)+'</p>'

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
        from web_locale import PAGE_PATH
        PAGE_PATH.set(path)
        if path.startswith('/connect/admin/update/'):
            raise ConnectError('요청을 찾을 수 없습니다.',404)
        accounts_path=re.fullmatch(r'/connect/admin/accounts/([1-9][0-9]{0,24})',path)
        if request.command == 'GET' and (path in ('/connect/admin','/connect/admin/changelog') or accounts_path):
            session = session or web.sessions.create({'admin_subject': claims['sub']})
            query = parse_qs(urlsplit(request.path).query, max_num_fields=2).get('q', [''])[0][:100]
            if accounts_path:
                values=parse_qs(urlsplit(request.path).query,max_num_fields=2,keep_blank_values=True)
                if any(len(v)!=1 for v in values.values()):raise ConnectError('중복 입력은 허용하지 않습니다.',400)
                from operator_accounts import members,say
                from web_locale import PAGE_PATH
                PAGE_PATH.set(path)
                body=page(say('계정 연결 사용자','Linked accounts'),members(self.service.registry,accounts_path[1],values.get('page',['1'])[0]))
            elif path.endswith('/changelog'):
                from changelog import content
                from product_web import t
                body=page('사이트·관리자 패치노트',content(t,admin=True),landing=True)
            else:
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
        if request.command != 'POST' or path not in ('/connect/admin/confirm', '/connect/admin/action', '/connect/admin/api-usage/refresh', '/connect/admin/email-subscription'):
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
        if path == '/connect/admin/email-subscription':
            if fields.get('enabled') not in ('yes','no'):
                raise ConnectError('메일 구독 설정을 확인해 주세요.',400)
            enabled = fields['enabled'] == 'yes'
            self.service.operator_mail.subscribe(claims['email'],claims['sub'],enabled)
            with self.service.registry.db() as db:
                db.execute('INSERT INTO admin_audit(actor,action,binding) VALUES(?,?,?)',
                           (claims['email'],'connection-email-enabled' if enabled else 'connection-email-disabled',''))
            return web.send(request,303,location='/connect/admin#connection-email')
        if path == '/connect/admin/api-usage/refresh':
            if not self.service.api_usage.refresh(self.service):
                raise ConnectError('이미 조회 중입니다. 잠시 후 다시 시도해 주세요.', 409)
            return web.send(request, 303, location='/connect/admin#api-usage')
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
