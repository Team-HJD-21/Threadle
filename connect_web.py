import html
import hashlib
import ipaddress
from http.cookies import SimpleCookie, CookieError
import logging
from runtime_logging import log_exception
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

from connect_auth import ConnectError, Settings, UserApis, channel_permissions, manages_guild
from connect_store import Sessions
from discord_threads import ApiError
from channel_layouts import CHANNELS, ChannelLayouts
from web_locale import LANGUAGE, PAGE_PATH, PUBLIC_ORIGIN, COOKIE as LANGUAGE_COOKIE, preference, switcher, render, text
from team_web import TeamWeb
import legal_consent

COOKIE = '__Host-ghd-session'
LOG = logging.getLogger('connect')
DETAIL = sum(1 << bit for bit in (10, 11, 14, 16, 34, 35, 38))
SUMMARY = sum(1 << bit for bit in (10, 11, 14, 16))


class MissingBot(ConnectError):
    def __init__(self):
        super().__init__('먼저 이 서버에 봇을 초대하고 채널 접근 권한을 허용해 주세요.', 409)


CSS = '''
:root{color-scheme:dark;font-family:Inter,"Segoe UI",sans-serif;background:#10191b;color:#f3f0e8}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(ellipse at top,#10191b,#10191b 60%);min-height:100vh;display:flex;flex-direction:column}
main{width:100%;max-width:760px;margin:0 auto;padding:38px 24px 64px;flex:1;min-height:calc(100vh - 126px);min-height:calc(100svh - 126px)}
.site-header{width:100%;max-width:1180px;margin:0 auto;padding:28px;display:flex;justify-content:space-between;align-items:center;gap:24px}
.site-logo{display:block;position:relative;overflow:hidden;width:min(440px,100%);height:70px;flex-shrink:1}
.site-logo img{position:absolute;width:100%;height:auto;top:50%;left:0;transform:translateY(-50%)}
.header-tools{display:flex;align-items:center;gap:18px;flex-shrink:0}.language-switcher{display:flex;gap:4px;font-size:12px}.language-switcher a{padding:7px 9px;border-radius:7px;color:#a2afab;text-decoration:none;white-space:nowrap}.language-switcher a[aria-current]{background:#2c3d37;color:#f3f0e8}.language-switcher a:hover{background:#24332d;color:white}
.brand{font-size:13px;letter-spacing:.12em;color:#a2afab}.badge{font-size:12px;color:#9be6c5;border:1px solid #365e52;border-radius:20px;padding:6px 12px}
.choice{width:auto;margin-right:9px;accent-color:#d9eea6}.area{padding:12px 0}h2{font-size:20px;letter-spacing:-.02em}h3{font-size:16px}.area p{margin:7px 0}
.area-editor{margin-top:24px;padding-top:12px}.area-editor>label:first-child{margin-top:0}.picker-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;margin:18px 0 8px}.picker-heading h3{margin:0}.picker-heading .muted{margin:0}.directory-browser{border:1px solid #33413e;border-radius:12px;overflow:hidden;background:#131925}.directory-breadcrumbs{display:flex;align-items:center;gap:4px;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid #33413e;background:#1c2332}.directory-breadcrumbs button{background:transparent;color:#b9c6bf;padding:4px 6px;margin:0;border-radius:4px;font-size:12px;min-width:0;overflow-wrap:anywhere}.directory-breadcrumbs button:hover{background:#2c3d37}.directory-breadcrumbs span{color:#727f9c;font-size:12px}.directory-list{max-height:278px;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin}.directory-row{display:grid;grid-template-columns:minmax(0,1fr) 40px;align-items:center;min-height:46px}.directory-row:last-child{border-bottom:0}.directory-row:has(input:checked){background:#292542}.directory-row label{display:flex;align-items:center;gap:10px;padding:11px 12px;margin:0;min-width:0;cursor:pointer;font-size:13px}.directory-row .choice{flex:none;padding:0;margin:0;width:15px;height:15px}.directory-icon{width:17px;height:17px;flex:none;color:#d9eea6}.directory-name{min-width:0;overflow-wrap:anywhere}.directory-open{display:grid;place-items:center;align-self:stretch;margin:0;padding:0;background:transparent;color:#aeb8d1;border-radius:0;font-size:24px}.directory-open:hover{background:#2c3d37;color:white}.directory-empty{padding:14px;margin:0;font-size:12px;color:#90a39b}.picker-note{font-size:12px;color:#90a39b;line-height:1.6;margin:8px 0 12px}.selection-summary{display:flex;align-items:center;gap:8px;margin:14px 0 8px;font-size:12px;color:#aeb8d1}.selection-count{display:inline-flex;align-items:center;justify-content:center;min-width:23px;padding:2px 7px;border-radius:20px;background:#2c3d37;font-size:11px;color:#e4dcff}.selection-chips{display:flex;flex-wrap:wrap;gap:6px}.selection-chip{display:inline-flex;align-items:center;gap:6px;max-width:100%;border:1px solid #475a4f;border-radius:7px;background:#24332d;padding:4px 8px;font-size:12px;color:#d9eea6}.selection-chip span{overflow-wrap:anywhere;min-width:0}.selection-chip button{background:none;color:#b6c7ad;margin:0;padding:0 3px;font-size:16px}.area-actions{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-top:15px}.area-actions button{margin:0}.picker-clear{margin-top:8px}.picker-clear button{background:none;color:#a2afab;padding:4px 0;margin:0;font-size:12px}.sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
.preview-intro{font-size:13px;color:#9ca7c1;margin:0 0 16px}.preview-form{display:flex;flex-wrap:wrap;align-items:flex-end;gap:10px}.preview-form .request-feedback{flex-basis:100%;margin-bottom:0}.preview-input{flex:1;min-width:0}.preview-input label{margin:0 0 7px}.preview-form button{margin:0;white-space:nowrap}.preview-result{margin-top:18px;border:1px solid #475a4f;border-radius:12px;background:#202f28;padding:16px}.preview-kicker{font-size:11px;color:#a99bcf;letter-spacing:.04em;margin:0 0 5px}.preview-destination{font-size:18px;font-weight:650;margin:0 0 8px;overflow-wrap:anywhere}.preview-reason{font-size:13px;color:#b4bfd6;margin:0;line-height:1.6}.preview-stats{display:flex;flex-wrap:wrap;gap:7px;margin-top:12px}.preview-stat{font-size:11px;border:1px solid #3d425a;border-radius:6px;padding:4px 8px;color:#b9c3dc}.preview-files{margin-top:12px;border-radius:7px;background:#171c29;overflow:hidden}.preview-files summary{cursor:pointer;display:flex;justify-content:space-between;align-items:center;gap:8px;padding:11px 13px;font-size:12px;color:#b6c1da;list-style:none}.preview-files summary::after{content:'+';font-size:16px}.preview-files[open] summary::after{content:'−'}.preview-files summary::-webkit-details-marker{display:none}.path-list{list-style:none;margin:0;padding:0 13px;max-height:260px;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin}.path-list li{padding:9px 0;min-width:0}.path-file{display:block;font-size:12px;color:#d4dbee;overflow-wrap:anywhere}.path-parent{display:block;font-size:11px;color:#8795b0;margin-top:3px;overflow-wrap:anywhere;white-space:normal}.preview-limit{padding:0 13px 10px;font-size:11px;color:#90a39b;margin:0}.area .muted{overflow-wrap:anywhere}
#team-settings{overflow-anchor:none}.team-feedback{padding:11px 13px;margin:12px 0;border:1px solid #51506a;background:#202f28;border-radius:8px;font-size:12px;line-height:1.7;color:#d8d1ee}.team-feedback.error{border-color:#95606d;color:#f1c3ce}.team-feedback a{display:inline-block;margin-left:8px}button.is-busy{opacity:.7;cursor:wait}button.is-busy::after{content:'…';margin-left:6px}.directory-row:focus-within{background:#252b40}
h1{font-size:clamp(28px,5vw,40px);letter-spacing:-.04em;margin:12px 0 16px}p{line-height:1.7;color:#b9c6bf}a{color:#d9eea6;text-underline-offset:3px}
.card{background:#192426;border:0;border-radius:10px;padding:26px;margin:24px 0}.steps{font-size:13px;color:#a2afab}
label{display:block;font-size:14px;margin:20px 0 9px}select,input{width:100%;background:#10191b;color:#f3f0e8;border:1px solid #52645e;border-radius:10px;padding:13px;font:inherit}
button,.button{display:inline-block;background:#d9eea6;color:#10191b;border:0;border-radius:10px;padding:13px 20px;font:600 14px "Segoe UI",sans-serif;cursor:pointer;text-decoration:none;margin:14px 8px 4px 0}
.secondary{background:#2c3d37;color:#f3f0e8}.danger{background:#603448;color:#ffdeea}.muted{font-size:13px;color:#a2afab}.row{display:flex;gap:16px}.row>*{flex:1}
small{display:block;color:#a2afab;line-height:1.6}strong{color:#f3f0e8}form{margin:0}.connection-note{margin:30px 0 0;color:#90a39b;font-size:12px;line-height:1.7}
.site-footer{background:transparent;color:#bcc9c1;margin-top:0;padding:28px 28px 20px;font-size:13px;line-height:1.65}
.footer-inner{max-width:1124px;margin:0 auto;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px 36px}
.footer-brand img{display:block;width:140px;height:auto}.footer-tagline{font-size:12px;color:#a2afab;margin:6px 0 0}
.footer-links{display:flex;justify-content:flex-end;align-items:flex-start;flex-wrap:wrap;gap:14px 24px;font-size:13px}
.site-footer a{color:inherit;text-decoration:none}.site-footer a:hover{color:#d9eea6}.site-footer a:focus-visible{outline-color:#d9eea6}
.footer-business{grid-column:1/-1;padding-top:6px;margin:0;display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:7px 36px}
.footer-business>div{display:flex;gap:14px;align-items:baseline;min-width:0}.footer-business dt{color:#a2afab;flex-shrink:0}.footer-business dd{margin:0;overflow-wrap:anywhere}.site-footer .business-info-link{text-decoration:underline;text-underline-offset:4px;color:#a2afab}
.footer-policy{grid-column:1/-1;display:flex;flex-wrap:wrap;gap:6px 24px}.footer-policy p{width:100%;font-size:12px;color:#a2afab;margin:0}
.footer-copyright{align-self:center;color:#a2afab;font-size:12px}.related-sites{position:relative;justify-self:end;width:220px;max-width:100%;color:#a6bbc5}
.related-sites summary{list-style:none;display:flex;justify-content:space-between;align-items:center;gap:20px;padding:8px 13px;border:1px solid #33413e;border-radius:5px;cursor:pointer;min-height:40px}
.related-sites summary::-webkit-details-marker{display:none}.related-sites summary::after{content:none}.footer-chevron{width:18px;height:18px;flex-shrink:0;transition:transform .18s ease}.related-sites[open] .footer-chevron{transform:rotate(180deg)}@media(prefers-reduced-motion:reduce){.footer-chevron{transition:none}}
.related-sites ul{position:absolute;bottom:calc(100% + 8px);left:0;width:100%;margin:0;padding:6px;background:#192426;border:1px solid #33413e;border-radius:5px;list-style:none;box-shadow:0 12px 30px #0005;z-index:2}
.related-sites li a{display:block;padding:10px 12px}.related-sites li a:hover{background:#24332d}
:focus-visible{outline:3px solid #d9eea6;outline-offset:4px}.error{border-color:#95606d}code{font-size:13px;overflow-wrap:anywhere}
@media(max-width:650px){.site-header{padding:18px;gap:12px;flex-wrap:wrap}.site-logo{width:min(380px,100%);height:62px}.site-header .badge{display:none}.header-tools{width:100%;justify-content:flex-end}main{padding:20px 18px 44px;min-height:calc(100vh - 142px);min-height:calc(100svh - 142px)}.row{display:block}.card{padding:20px}.site-footer{padding:24px 20px 20px}.footer-inner{grid-template-columns:minmax(0,1fr);gap:16px}.footer-links{justify-content:flex-start;gap:8px 20px}.footer-business{grid-template-columns:minmax(0,1fr);gap:8px}.footer-policy{gap:6px 20px}.related-sites{justify-self:start;width:100%}}
@media(max-width:650px){html[lang="en"] .footer-business>div{flex-direction:column;gap:2px}}

:root{--bg:#10191b;--surface:#192426;--ink:#f3f0e8;--muted:#a2afab;--line:#33413e;--accent:#d9eea6}body{background:var(--bg)}.threadle-brand{display:inline-flex;align-items:center;gap:12px;text-decoration:none;color:var(--ink);font-size:25px;font-weight:650;letter-spacing:-.8px}.threadle-brand img{width:36px;height:36px;image-rendering:auto}.threadle-brand small{font-size:10px;font-weight:400;letter-spacing:.08em;color:var(--muted);margin-top:-5px}.threadle-brand:hover{color:var(--accent)}button:disabled{opacity:.65;cursor:default}
'''


def esc(value):
    return html.escape(str(value), quote=True)


CSS += '\n.admin-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.admin-stats .card{padding:18px;margin:14px 0 22px}.admin-stats strong{display:block;font-size:28px;color:#d9eea6}.admin-stats span{font-size:12px;color:#a2afab}.admin-heading{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap}.admin-search button{margin-right:10px}.admin-binding-title{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.admin-binding-title h2{margin:0;overflow-wrap:anywhere}.admin-status{font-size:12px;padding:5px 10px;border:1px solid #33413e;border-radius:20px;white-space:nowrap}.admin-actions{display:flex;gap:10px;flex-wrap:wrap}.admin-actions form{margin:0}.admin-actions button{margin:0}.admin-audit{padding-left:18px;font-size:12px;overflow-wrap:anywhere}.admin-audit li{margin:12px 0}.admin-binding code{overflow-wrap:anywhere;font-size:11px}@media(max-width:500px){.admin-stats{gap:7px}.admin-stats .card{padding:12px}.admin-stats span{font-size:11px}}\n'


def footer():
    return '''<footer class="site-footer"><div class="footer-inner">
<div class="footer-brand"><a href="https://teamhjd.com/" target="_blank" rel="noopener noreferrer"><img src="/connect/assets/teamhjd-wordmark.png?v=1" width="486" height="98" alt="Team HJD"></a><p class="footer-tagline">Happy Journey of Developers.</p></div>
<nav class="footer-links" aria-label="하단 메뉴"><a href="https://github.com/Team-HJD-21" target="_blank" rel="noopener noreferrer">GitHub</a><a href="https://store.steampowered.com/app/4336820/The_Developer/" target="_blank" rel="noopener noreferrer">Steam</a><a href="https://teamhjd.com/privacy/index.html#email" target="_blank" rel="noopener noreferrer">support@teamhjd.com</a><a href="/connect/terms">이용약관</a><a href="/connect/privacy">개인정보처리방침</a><a href="/connect/privacy#policy-12">개인정보 요청</a></nav>
<dl class="footer-business">
<div><dt>상호</dt><dd translate="no" lang="ko">팀 에이치제이디(TeamHJD)</dd></div><div><dt>대표자</dt><dd>황재동</dd></div>
<div><dt>사업자등록번호</dt><dd>622-10-17519 <a class="business-info-link" href="https://www.ftc.go.kr/bizCommPop.do?wrkr_no=6221017519" target="_blank" rel="noopener noreferrer">(사업자정보확인)</a></dd></div>
<div><dt>통신판매업 신고번호</dt><dd translate="no" lang="ko">2026-수원영통-1221호</dd></div><div><dt>사업장 주소</dt><dd translate="no" lang="ko">경기도 수원시 영통구 태장로 71번길 19</dd></div>
</dl><div class="footer-policy"><a href="https://teamhjd.com/press/index.html" target="_blank" rel="noopener noreferrer">프레스킷</a><a href="https://teamhjd.com/content-policy/index.html" target="_blank" rel="noopener noreferrer">콘텐츠 이용 · AI 학습 정책</a><p>AI 검색·정보 조회는 허용하며, 콘텐츠의 AI 학습 이용은 금지합니다.</p></div>
<small class="footer-copyright">© 2026 TeamHJD. All rights reserved. · v1.0.0-beta.17</small>
<details class="related-sites"><summary>관련 사이트<svg class="footer-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="m6 15 6-6 6 6"></path></svg></summary><ul><li><a href="https://teamhjd.com/" target="_blank" rel="noopener noreferrer">TeamHJD 메인</a></li><li><a href="https://docs.teamhjd.com/" target="_blank" rel="noopener noreferrer">TeamHJD Docs</a></li></ul></details>
</div></footer>'''


def page(title, body, landing=False):
    import json
    from product_web import PUBLIC_PATHS
    base = PUBLIC_ORIGIN.get()
    path = PAGE_PATH.get()
    public = landing and path in PUBLIC_PATHS
    title_text = 'TeamHJD - Threadle' + ((' | ' + text(title)) if title != 'TeamHJD - Threadle' else '')
    description = ('GitHub updates, organized in Discord. Keep PRs, issues, discussions, reviews, and CI updates together in per-item threads.' if LANGUAGE.get() == 'en' else 'GitHub 소식을 Discord 스레드로 정리하세요. PR, 이슈, Discussion의 댓글·리뷰·새 커밋·CI 결과를 항목별로 모아줍니다.')
    extra = '<link rel="icon" type="image/png" href="/connect/assets/threadle-avatar.png"><meta name="description" content="' + esc(description) + '">'
    extra += '<meta name="robots" content="' + ('index,follow' if public else 'noindex,nofollow') + '">'
    if landing:
        extra += '<link rel="stylesheet" href="/connect/assets/landing.css?v=20261102-2"><link rel="stylesheet" href="/connect/assets/product.css?v=20261103-17"><script src="/connect/assets/preview.js?v=20261103-17" defer></script>'
    extra += '<link rel="stylesheet" href="/connect/assets/controls.css?v=20261102-6"><script src="/connect/assets/controls.js?v=1" defer></script>'
    if public:
        preview_image = esc(base + '/connect/assets/threadle-banner.png?v=20261102-9')
        extra += '<meta property="og:image" content="' + preview_image + '"><meta property="og:image:alt" content="Threadle — GitHub updates delivered to Discord"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="' + preview_image + '">'
        canonical_path = '/' if path in ('/', '/connect', '/connect/') else path
        canonical = base + canonical_path + ('?lang=en' if LANGUAGE.get() == 'en' else '')
        extra += '<link rel="canonical" href="' + esc(canonical) + '">'
        for language in ('ko', 'en'):
            extra += '<link rel="alternate" hreflang="' + language + '" href="' + base + canonical_path + ('?lang=en' if language == 'en' else '') + '">'
        extra += '<meta property="og:type" content="website"><meta property="og:site_name" content="TeamHJD - Threadle"><meta property="og:title" content="' + esc(title_text) + '"><meta property="og:description" content="' + esc(description) + '"><meta property="og:url" content="' + esc(canonical) + '">'
        data = {'@context':'https://schema.org','@type':'WebSite','name':'TeamHJD - Threadle','alternateName':'Threadle','url':base+'/', 'publisher':{'@type':'Organization','name':'TeamHJD','url':'https://teamhjd.com/'}}
        extra += '<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace('<', '\u003c') + '</script>'
    note = '' if landing else '<p class="connection-note">저장소의 알림 내용은 연결한 Discord 알림 채널을 볼 수 있는 구성원에게 공개됩니다. 개인 GitHub 토큰을 입력할 필요가 없습니다. 로그인 세션은 30분 후 만료되며, 완료한 알림 연결은 유지됩니다.</p>'
    return render('<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
                  + '<title>' + esc(title_text) + '</title><style>' + CSS + '</style>' + extra + '</head><body' + (' class="product-page"' if landing else '') + '>'
                  + '<header class="site-header"><a class="threadle-brand" href="/connect" aria-label="Threadle Home"><img src="/connect/assets/threadle-avatar.png" width="32" height="32" alt="Threadle"><span>Threadle<small>by TeamHJD</small></span></a><div class="header-tools">' + switcher() + '</div></header><main>'
                  + ('' if landing else '<h1>' + esc(title) + '</h1>') + body + note + '</main>' + footer() + '</body></html>')



def form(action, session, body):
    return '<form method="post" action="' + action + '"><input type="hidden" name="csrf" value="' + esc(session.data['csrf']) + '">' + body + '</form>'


def select(name, label, choices, selected=None):
    options = ''.join('<option value="' + esc(key) + '"' + (' selected' if str(key) == str(selected) else '') + '>'
                      + esc(text) + '</option>' for key, text in choices)
    return '<label for="' + name + '">' + esc(label) + '</label><select required id="' + name + '" name="' + name + '">' + options + '</select>'


class Onboarding:
    def __init__(self, service, settings, users=None, sessions=None):
        self.service, self.settings = service, settings
        self.users = users or UserApis(settings)
        self.sessions = sessions or Sessions(service.root / 'connect.sqlite3', settings.session_key)
        self.layouts = ChannelLayouts(service.root / 'channel-layouts.sqlite3', settings.discord_id)
        self.team = TeamWeb(self)
        self.rate_lock = threading.Lock()
        self.rates = {}

    @classmethod
    def create(cls, service):
        settings = Settings.load(service.env)
        return cls(service, settings) if settings else None

    def check(self, require_events=True):
        if not self.service.env.get('GITHUB_APP_WEBHOOK_SECRET') or self.service.tokens is None:
            raise ValueError('GitHub App webhook authentication is not configured')
        application = self.service.tokens.app_api().call('GET', '/app')
        if (int(application['id']) != self.settings.github_id
                or application.get('client_id') != self.settings.github_client
                or application.get('slug') != self.settings.github_slug):
            raise ValueError('GitHub App identity does not match public connection settings')
        needed = {'issues', 'issue_comment', 'pull_request', 'pull_request_review', 'pull_request_review_comment',
                  'discussion', 'discussion_comment', 'workflow_run'}
        if require_events and not needed <= set(application.get('events', [])):
            raise ValueError('GitHub App webhook subscriptions are incomplete')
        for permission in ('issues', 'pull_requests', 'discussions', 'actions'):
            if application.get('permissions', {}).get(permission) not in ('read', 'write'):
                raise ValueError('GitHub App repository permissions are incomplete')
        discord = self.service.discord()
        bot = discord.call('GET', '/users/@me')
        if not bot.get('bot') or str(bot['id']) != self.settings.discord_id:
            raise ValueError('Discord Application ID does not match the bot token')
        discord_app = discord.call('GET', '/oauth2/applications/@me')
        if str(discord_app.get('id')) != self.settings.discord_id or discord_app.get('bot_public') is not True:
            raise ValueError('Enable Public Bot so other server administrators can invite it')
        if discord_app.get('bot_require_code_grant'):
            raise ValueError('Disable Require OAuth2 Code Grant for the separate bot invite step')
        if 'integration_types_config' in discord_app and '0' not in discord_app['integration_types_config']:
            raise ValueError('Enable Guild Install for the Discord application')
        if 'redirect_uris' in discord_app and self.settings.callback('discord') not in discord_app['redirect_uris']:
            raise ValueError('Register the Discord OAuth2 callback URL before activation')
        if require_events and discord_app.get('interactions_endpoint_url') != self.settings.base + '/discord/interactions':
            raise ValueError('Set the Discord Interactions Endpoint URL before activation')
        if require_events and not self.service.env.get('DISCORD_PUBLIC_KEY'):
            raise ValueError('Discord interaction signature verification is not configured')

    def send(self, request, status, body='', location=None, session=None, clear=False, content_type='text/html; charset=utf-8'):
        content = body.encode('utf-8')
        scripts = "script-src 'self'; " + ("connect-src 'self'; " if urlsplit(request.path).path.startswith('/connect/team') else '')
        request.send_response(status)
        for key, value in {
            'Content-Type': content_type, 'Content-Length': str(len(content)),
            'Cache-Control': 'no-store', 'Referrer-Policy': 'same-origin', 'X-Content-Type-Options': 'nosniff',
            'Vary': 'Accept-Language, Cookie', 'Content-Language': LANGUAGE.get(),
            'X-Robots-Tag': 'noindex' if status >= 400 or urlsplit(request.path).path not in ('/', '/connect', '/connect/', '/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq', '/connect/changelog', '/connect/terms', '/connect/privacy') else 'index, follow',
            'X-Frame-Options': 'DENY', 'Content-Security-Policy': "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self'; " + scripts +
            "form-action 'self' https://discord.com https://github.com; base-uri 'none'; frame-ancestors 'none'",
        }.items():
            request.send_header(key, value)
        if location:
            request.send_header('Location', location)
        if getattr(request, 'ui_language_choice', None):
            request.send_header('Set-Cookie', LANGUAGE_COOKIE + '=' + request.ui_language_choice
                                + '; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=31536000')
        if clear or session:
            cookie = COOKIE + '=' + ('' if clear else session.key)
            request.send_header('Set-Cookie', cookie + '; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=' + ('0' if clear else '1800'))
        request.end_headers()
        request.wfile.write(content)

    def redirect(self, request, path, session=None):
        self.send(request, 303, location=path, session=session)

    def public_document(self, request, path):
        base = self.settings.base
        if path in ('/robots.txt', '/connect/robots.txt'):
            body = 'User-agent: *\nAllow: /\n' + ''.join('Disallow: ' + p + '\n' for p in ('/connect/discord/', '/connect/github/', '/connect/team', '/connect/server', '/connect/repositories', '/connect/guilds', '/connect/channels', '/connect/done', '/discord/', '/github/')) + 'Sitemap: ' + base + '/connect/sitemap.xml\n'
            mime = 'text/plain; charset=utf-8'
        else:
            from xml.sax.saxutils import escape
            paths = ('/', '/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq', '/connect/changelog', '/connect/terms', '/connect/privacy')
            body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join('<url><loc>' + escape(base + p + ('?lang=en' if language == 'en' else '')) + '</loc></url>' for p in paths for language in ('ko', 'en')) + '</urlset>'
            mime = 'application/xml; charset=utf-8'
        data = body.encode()
        request.send_response(200)
        for key, value in {'Content-Type': mime, 'Content-Length': str(len(data)), 'Cache-Control': 'public, max-age=3600', 'X-Content-Type-Options': 'nosniff'}.items():
            request.send_header(key, value)
        request.end_headers()
        request.wfile.write(data)

    def asset(self, request, path):
        name = path.removeprefix('/connect/assets/')
        if name not in ('notify-logo.png', 'teamhjd-wordmark.png', 'threadle-icon.png', 'team-settings.js', 'landing.css', 'product.css', 'threadle-avatar.png', 'admin-update.js', 'controls.js', 'controls.css', 'preview.js', 'threadle-banner.png'):
            raise ConnectError('파일을 찾을 수 없습니다.', 404)
        asset = Path(__file__).parent / 'web_assets' / name
        if not asset.is_file():
            raise ConnectError('파일을 찾을 수 없습니다.', 404)
        data = asset.read_bytes()
        request.send_response(200)
        content_type = 'text/javascript; charset=utf-8' if name.endswith('.js') else 'text/css; charset=utf-8' if name.endswith('.css') else 'image/png'
        for key, value in {'Content-Type': content_type, 'Content-Length': str(len(data)),
                           'Cache-Control': 'public, max-age=86400', 'X-Content-Type-Options': 'nosniff'}.items():
            request.send_header(key, value)
        request.end_headers()
        request.wfile.write(data)

    def throttle(self, key):
        minute = int(time.monotonic() // 60)
        with self.rate_lock:
            self.rates = {k: value for k, value in self.rates.items() if value[0] == minute}
            count = self.rates.get(key, (minute, 0))[1] + 1
            if len(self.rates) > 10000 or count > 90:
                raise ConnectError('요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.', 429)
            self.rates[key] = minute, count

    def client_key(self, request):
        peer = request.client_address[0]
        if ipaddress.ip_address(peer).is_loopback:
            forwarded = request.headers.get('X-Forwarded-For', '').split(',')[-1].strip()
            try:
                peer = str(ipaddress.ip_address(forwarded))
            except ValueError:
                pass
        return hashlib.sha256((self.settings.session_key + peer).encode()).hexdigest()

    def session(self, request):
        try:
            cookies = SimpleCookie(request.headers.get('Cookie', ''))
            return self.sessions.get(cookies[COOKIE].value) if COOKIE in cookies else None
        except (CookieError, ValueError):
            return None

    def require(self, session, provider='discord'):
        if session is None or not session.data.get(provider + '_token'):
            raise ConnectError('로그인이 필요하거나 세션이 만료됐습니다. 다시 연결해 주세요.', 401)
        if session.data.get(provider + '_expires', 0) <= time.time():
            raise ConnectError('로그인이 만료됐습니다. 다시 연결해 주세요.', 401)

    def guild(self, session, guild_id):
        self.require(session)
        guild = next((g for g in self.users.guilds(session.data['discord_token']) if str(g['id']) == str(guild_id)), None)
        if guild is None or not manages_guild(guild):
            raise ConnectError('이 Discord 서버를 관리할 권한이 없습니다.', 403)
        return guild

    def channels(self, guild_id):
        api = self.service.discord()
        bot = api.call('GET', '/users/@me')
        if not bot.get('bot') or str(bot['id']) != self.settings.discord_id:
            raise ConnectError('봇 설정이 일치하지 않습니다. 운영자에게 문의해 주세요.', 503)
        try:
            member = api.call('GET', f'/guilds/{guild_id}/members/{bot["id"]}')
            roles = api.call('GET', f'/guilds/{guild_id}/roles')
            channels = api.call('GET', f'/guilds/{guild_id}/channels')
        except ApiError as error:
            if error.status in (403, 404):
                raise MissingBot() from None
            raise
        return [(channel, channel_permissions(guild_id, member, roles, channel))
                for channel in channels if channel.get('type') == 0 and str(channel.get('guild_id')) == str(guild_id)]

    def repositories(self, session, selected=None):
        self.require(session, 'github')
        token = session.data['github_token']
        installations = self.users.installations(token)
        choices = []
        for installation in installations:
            identity = int(installation['id'])
            if selected is not None and selected != identity:
                continue
            for repo in self.users.repositories(token, identity):
                choices.append((identity, repo))
        return choices

    def callback(self, request, provider, session, query):
        if session is None:
            raise ConnectError('연결 세션이 만료됐습니다. 처음부터 다시 진행해 주세요.', 401)
        try:
            verifier = self.sessions.consume(session, provider, query.get('state', ''))
        except ValueError:
            raise ConnectError('로그인 확인 정보가 일치하지 않거나 만료됐습니다. 다시 시작해 주세요.', 403) from None
        if query.get('error') or not query.get('code'):
            raise ConnectError('로그인이 취소됐습니다. 원할 때 다시 연결할 수 있습니다.', 400)
        if provider == 'github':
            self.require(session)
        if provider == 'discord' and query.get('iss', 'https://discord.com') != 'https://discord.com':
            raise ConnectError('인증 서버 정보가 일치하지 않습니다.', 403)
        result = self.users.exchange(provider, query['code'], verifier)
        token = result.get('access_token')
        if not isinstance(token, str) or not token or result.get('token_type', '').lower() != 'bearer':
            raise ConnectError('로그인 정보를 확인하지 못했습니다.', 502)
        if provider == 'discord':
            scopes = set(result.get('scope', '').split())
            if not {'identify', 'guilds'} <= scopes:
                raise ConnectError('계정 확인과 서버 목록 접근에 동의해야 연결할 수 있습니다.', 403)
            profile = self.users.discord(token, '/users/@me')
        else:
            profile = self.users.github(token, '/user')
        if not str(profile.get('id', '')).isdigit():
            raise ConnectError('사용자 정보를 확인하지 못했습니다.', 502)
        installing = session.data.get('pending_install') if provider == 'discord' else None
        pending = (session.data.get('pending_discord') or installing) if provider == 'discord' else None
        if pending and (pending['user'] != str(profile['id']) or pending['expires'] <= time.time()):
            raise ConnectError('Discord에서 /connect 명령을 실행한 계정으로 진행해 주세요. 링크가 만료됐다면 명령을 다시 실행해 주세요.', 403)
        if provider == 'discord':
            previous = {key: session.data[key] for key in ('github_token', 'github_user', 'github_expires', 'repository')
                        if installing and key in session.data}
            session.data = {'csrf': session.data['csrf']}
            session.data.update(previous)
        if provider == 'github':
            self.service.registry.save_github_login(str(profile['id']), profile.get('login'), time.time())
        session.data.update({provider + '_token': token, provider + '_user': str(profile['id']),
                             provider + '_expires': time.time() + min(1800, int(result.get('expires_in', 1800)))})
        if pending:
            self.guild(session, pending['guild'])
            session.data.update({'guild': pending['guild'], 'from_discord': not bool(installing)})
            if pending.get('channel'):
                session.data['preferred_channel'] = pending['channel']
            if installing:
                self.channels(pending['guild'])
        session = self.sessions.rotate(session)
        if installing:
            if session.data.get('repository') and session.data.get('github_expires', 0) > time.time():
                return self.redirect(request, '/connect/channels', session)
            return self.start_github(request, session, checked=True)
        target = '/connect/server' if pending else '/connect/guilds'
        self.redirect(request, target if provider == 'discord' else '/connect/team' if session.data.pop('return_to_team', False) else '/connect/repositories', session)

    def get(self, request, path, session, query):
        if path in ('/', '/connect', '/connect/'):
            if query.get('ticket'):
                try:
                    pending = self.sessions.consume_ticket(query['ticket'])
                except ValueError:
                    raise ConnectError('이 연결 버튼은 이미 사용했거나 만료됐습니다. Discord에서 /connect 명령을 다시 실행해 주세요.', 401) from None
                if (session and session.data.get('discord_user') == pending['user']
                        and session.data.get('discord_expires', 0) > time.time()):
                    self.guild(session, pending['guild'])
                    session.data.update({'guild': pending['guild'], 'preferred_channel': pending['channel'], 'from_discord': True})
                    self.sessions.save(session)
                    return self.redirect(request, '/connect/server')
                fresh = self.sessions.create({'pending_discord': pending})
                return self.redirect(request, '/connect', fresh)
            fresh = session or self.sessions.create()
            from product_web import content
            title, body = content(path, fresh)
            if fresh.data.get('pending_discord'):
                body = ('<p>Discord에서 선택한 서버에 GitHub 저장소를 연결합니다. 명령을 실행한 계정인지 확인한 뒤 계속합니다.</p>'
                        '<div class="card">' + form('/connect/discord/start', fresh, '<button>Discord 계정 확인하고 계속하기</button>') + '</div>')
            return self.send(request, 200, page(title, body, landing=not bool(fresh.data.get('pending_discord'))), session=fresh)
        if path in ('/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq', '/connect/changelog', '/connect/terms', '/connect/privacy'):
            from product_web import content
            fresh = session or self.sessions.create()
            title, body = content(path, fresh)
            return self.send(request, 200, page(title, body, landing=True), session=fresh)
        if path in ('/connect/discord/callback', '/connect/github/callback'):
            return self.callback(request, path.split('/')[2], session, query)
        self.require(session)
        if path == '/connect/guilds':
            guilds = [g for g in self.users.guilds(session.data['discord_token']) if manages_guild(g)]
            choices = [(g['id'], g['name']) for g in guilds]
            body = '<p>관리 권한이 있는 서버만 표시합니다.</p><div class="card">'
            if choices:
                body += form('/connect/guild', session, select('guild', '알림을 받을 서버', choices)
                             + '<button>이 서버로 계속하기</button>'
                             '<button class="secondary" name="mode" value="manage">기존 연결 관리</button>')
            else:
                body += '<p>관리할 수 있는 서버가 없습니다. 서버 소유자 또는 서버 관리 권한이 필요합니다.</p>'
            return self.send(request, 200, page('Discord 서버 선택', body + '</div>'))
        guild = self.guild(session, session.data.get('guild', ''))
        if path == '/connect/team':
            return self.team.get(request, session, guild, query)
        if path == '/connect/server':
            invite = ('<a class="button secondary" target="_blank" rel="noopener noreferrer" href="'
                      + esc(self.settings.invite(guild['id'])) + '">서버에 봇 초대하기</a>')
            if session.data.get('from_discord'):
                invite = '<details><summary>봇 초대 또는 권한 다시 설정</summary>' + invite + '</details>'
            body = ('<p><strong>' + esc(guild['name']) + '</strong>에 알림을 연결합니다.</p><div class="card">'
                    '<p>GitHub 계정을 연결하고 알림을 받을 저장소를 선택해 주세요.</p>' + invite
                    + form('/connect/github/start', session, '<button>GitHub 계정 연결하기</button>') + '</div>')
            for binding in self.service.registry.active():
                if str(binding.config['guild_id']) == str(guild['id']):
                    body += '<div class="card"><strong>' + esc(binding.repo) + '</strong>'
                    if binding.auth == 'app':
                        body += '<p><a class="button secondary" href="/connect/team?binding=' + esc(binding.id) + '">팀 알림 설정</a></p>'
                        body += form('/connect/disconnect', session, '<input type="hidden" name="binding" value="'
                                     + esc(binding.id) + '"><button class="danger">이 저장소 연결 해제</button>')
                    else:
                        body += '<p class="muted">기존 운영 연결 · 이전은 운영자에게 문의해 주세요.</p>'
                    body += '</div>'
            body += '<a href="/connect/guilds">다른 서버 선택</a>'
            body += form('/connect/logout', session, '<button class="secondary">로그아웃</button>')
            return self.send(request, 200, page('서버 연결 관리', body))
        if path == '/connect/github/installed':
            return self.redirect(request, '/connect/repositories' if session.data.get('github_token') else '/connect/server')
        if path == '/connect/repositories':
            choices = self.repositories(session)
            options = [(str(i) + ':' + str(r['id']), r['full_name']) for i, r in choices]
            body = ('<p>앱 접근이 승인됐고, 내 계정에 관리 권한이 있는 저장소만 표시합니다.</p>'
                    '<div class="card"><a class="button secondary" href="https://github.com/apps/'
                    + esc(self.settings.github_slug) + '/installations/new" target="_blank" rel="noopener noreferrer" aria-label="앱 설치 / 저장소 접근 설정 (새 탭)">앱 설치 / 저장소 접근 설정</a>'
                    '<a class="button secondary" href="/connect/repositories">목록 새로고침</a>'
                    '<p class="muted">GitHub 설정은 새 탭에서 열립니다. 설정을 마친 뒤 이 탭에서 목록을 새로고침해 주세요.</p>')
            if options:
                body += form('/connect/repository', session, select('repository', '연결할 저장소', options)
                             + '<button>자동 연결 설정 확인하기</button>')
            else:
                body += '<p>연결할 저장소가 없습니다. 앱을 설치하고 접근할 저장소를 선택해 주세요. 조직 승인 대기 중이면 관리자의 승인이 필요합니다.</p>'
            return self.send(request, 200, page('GitHub 저장소 선택', body + '</div>'))
        if path == '/connect/channels':
            installation, repo_id = self.selection(session)
            repo = next((r for i, r in self.repositories(session, installation) if int(r['id']) == repo_id), None)
            if repo is None:
                raise ConnectError('저장소 접근이 변경됐습니다. 저장소를 다시 선택해 주세요.', 403)
            body = '<p><strong>' + esc(repo['full_name']) + '</strong> → <strong>' + esc(guild['name']) + '</strong></p><div class="card">'
            existing = next((b for b in self.service.registry.active()
                             if b.repo_id == repo_id and str(b.config['guild_id']) == str(guild['id'])), None)
            from notification_language import selected_language
            language = selected_language(session,str(guild['id'])+'|'+session.data['repository'],existing,LANGUAGE.get())
            language_field = select('notification_language','알림 언어',[('ko','한국어 (KR)'),('en','English (EN)')],language)
            language_field += '<p class="muted">이 저장소의 봇 알림에 사용할 언어입니다. 사이트 언어와 별개로 저장되며 제목과 댓글 원문은 번역하지 않습니다.</p>'
            connection_form = False
            if existing:
                body += '<p>이미 연결된 저장소입니다. 기존 알림 채널을 그대로 사용합니다.</p>'
                body += form('/connect/save', session, language_field + legal_consent.fields(session) + '<button>기존 연결 확인하기</button>').replace('<form ','<form id="connection-form" ',1)
                connection_form = True
            elif not self.layouts.can_manage(self.service.discord(), str(guild['id'])):
                body += '<p>카테고리와 알림 채널을 자동 생성하려면 봇의 채널 관리 권한이 필요합니다.</p>'
                body += form('/connect/bot/permissions', session, '<button>채널 생성 권한 승인하기</button>')
            else:
                body += '<p>아래 카테고리와 채널을 자동으로 생성하고 연결합니다. 이 봇이 이미 만든 채널이 있으면 재사용합니다.</p>'
                body += '<strong>' + esc(self.layouts.name(guild['id'], repo_id, repo['full_name'])) + '</strong><ul>' + ''.join('<li>' + esc(name) + '</li>' for _, name in CHANNELS) + '</ul>'
                body += '<p class="muted">연결 후 채널 이름은 원하는 대로 바꿔도 알림이 유지됩니다.</p>'
                fields = language_field + legal_consent.fields(session)
                fields += '<label for="workflow">CI 워크플로 이름 필터 (선택)</label><input id="workflow" name="workflow" maxlength="1000" placeholder="비워두면 모든 워크플로, 여러 개는 쉼표로 구분">'
                fields += '<p class="muted">새 카테고리는 서버의 기본 역할 권한을 따릅니다. 비공개 저장소의 알림도 이 채널을 볼 수 있는 서버 구성원에게 공개됩니다.</p><button>카테고리·채널 자동 생성하고 연결하기</button>'
                body += form('/connect/save', session, fields).replace('<form ','<form id="connection-form" ',1)
                connection_form = True
            body += '</div>'
            body += '<div class="card"><h2>알림을 더 세밀하게 설정할까요?</h2><p>기본 연결을 그대로 사용하거나, 디렉터리별 PR 채널과 리뷰 대기·묶음·요약 알림을 선택할 수 있습니다.</p>' + ('<button class="secondary" type="submit" form="connection-form" formaction="/connect/team/start" formnovalidate>팀 알림 설정</button>' if connection_form else '<a class="button secondary" href="/connect/team">팀 알림 설정</a>') + '</div><a href="/connect/repositories">저장소 다시 선택</a>'
            return self.send(request, 200, page('알림 채널 자동 설정', body))
        if path == '/connect/done':
            identity = session.data.get('completed')
            binding = self.service.registry.get(identity) if identity else None
            if binding is None or str(binding.config['guild_id']) != str(guild['id']):
                return self.redirect(request, '/connect/server')
            body = ('<div class="card"><p><strong>' + esc(binding.repo) + '</strong>의 새 활동을 <strong>'
                    + esc(guild['name']) + '</strong>으로 전달할 준비가 됐습니다.</p>'
                    '<p>이제 GitHub에서 이슈를 만들거나 댓글을 남겨 스레드와 안내 채널의 알림을 확인해 주세요.</p>'
                    '<small>연결 이전 활동은 개별 알림으로 다시 보내지 않습니다. 봇과 앱의 접근 권한을 유지해 주세요.</small>'
                    '<a class="button" href="https://discord.com/channels/' + esc(guild['id']) + '/'
                    + esc(binding.config.get('alert_channel') or binding.config['channels']['issue']) + '">Discord 알림 채널 열기</a>'
                    '<a class="button" href="/connect/server">연결 관리</a></div>')
            return self.send(request, 200, page('연결이 완료됐어요.', body))
        raise ConnectError('페이지를 찾을 수 없습니다.', 404)

    @staticmethod
    def selection(session):
        value = session.data.get('repository', '')
        if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', value):
            raise ConnectError('저장소를 먼저 선택해 주세요.', 409)
        return tuple(map(int, value.split(':')))

    def start_github(self, request, session, checked=False):
        self.require(session)
        if not checked:
            self.guild(session, session.data.get('guild', ''))
            try:
                self.channels(session.data['guild'])
            except MissingBot:
                return self.start_install(request, session)
        if session.data.get('github_token') and session.data.get('github_expires', 0) > time.time():
            return self.redirect(request, '/connect/team' if session.data.pop('return_to_team', False) else '/connect/repositories', session)
        state, verifier = self.sessions.state(session, 'github')
        return self.redirect(request, self.settings.authorize('github', state, verifier), session)

    def start_install(self, request, session):
        session.data['pending_install'] = {'user': session.data['discord_user'],
                                           'guild': session.data['guild'], 'expires': time.time() + 600}
        if session.data.get('preferred_channel'):
            session.data['pending_install']['channel'] = session.data['preferred_channel']
        self.sessions.save(session)
        state, _ = self.sessions.state(session, 'discord')
        return self.redirect(request, self.settings.install(session.data['guild'], state))

    def post(self, request, path, session, fields):
        if session is None or not self.sessions.valid_csrf(session, fields.get('csrf')):
            raise ConnectError('연결 확인 정보가 만료됐습니다. 페이지를 다시 열어 주세요.', 403)
        if path == '/connect/logout':
            self.sessions.remove(session)
            return self.send(request, 303, location='/connect', clear=True)
        if path in ('/connect/discord/start', '/connect/github/start'):
            provider = path.split('/')[2]
            if provider == 'github':
                return self.start_github(request, session)
            session.data.pop('pending_install', None)
            self.sessions.save(session)
            state, verifier = self.sessions.state(session, provider)
            return self.redirect(request, self.settings.authorize(provider, state, verifier))
        self.require(session)
        if path == '/connect/guild':
            guild = self.guild(session, fields.get('guild', ''))
            session.data['guild'] = str(guild['id'])
            for key in ('repository', 'completed', 'team_draft', 'team_binding', 'return_to_team'):
                session.data.pop(key, None)
            self.sessions.save(session)
            if fields.get('mode') == 'manage':
                return self.redirect(request, '/connect/server')
            try:
                self.channels(session.data['guild'])
            except MissingBot:
                return self.start_install(request, session)
            return self.start_github(request, session, checked=True)
        guild = self.guild(session, session.data.get('guild', ''))
        if path.startswith('/connect/team/'):
            return self.team.post(request, path, session, guild, fields)
        if path == '/connect/bot/permissions':
            return self.start_install(request, session)
        if path == '/connect/disconnect':
            try:
                self.service.registry.disconnect(fields.get('binding', ''), guild['id'], session.data['discord_user'])
            except ValueError as error:
                raise ConnectError(str(error), 403) from None
            return self.redirect(request, '/connect/server')
        if path == '/connect/repository':
            value = fields.get('repository', '')
            if not re.fullmatch(r'[1-9][0-9]*:[1-9][0-9]*', value):
                raise ConnectError('저장소를 선택해 주세요.')
            installation, repo_id = map(int, value.split(':'))
            if not any(int(r['id']) == repo_id for i, r in self.repositories(session, installation)):
                raise ConnectError('이 저장소를 연결할 권한이 없습니다.', 403)
            session.data['repository'] = value
            session.data.pop('team_draft', None)
            session.data.pop('team_binding', None)
            self.sessions.save(session)
            return self.redirect(request, '/connect/channels')
        if path == '/connect/save':
            if any(key in fields for key in ('issue', 'pr', 'discussion', 'alert', 'ci')):
                raise ConnectError('알림 채널은 서버에서 자동으로 설정합니다. 페이지를 다시 열어 주세요.', 403)
            installation, repo_id = self.selection(session)
            repo = next((r for i, r in self.repositories(session, installation) if int(r['id']) == repo_id), None)
            if repo is None:
                raise ConnectError('저장소 접근 권한이 변경됐습니다. 다시 확인해 주세요.', 403)
            names = [name.strip() for name in fields.get('workflow', '').split(',') if name.strip()]
            if len(names) > 20 or any(len(name) > 100 for name in names):
                raise ConnectError('CI 필터는 이름당 100자, 최대 20개까지 입력할 수 있습니다.')
            config = {'guild_id': str(guild['id']), 'channels': {kind: '1' for kind in ('issue', 'pr', 'discussion')}}
            from registry import Binding
            binding = Binding('pending', 'pending', repo_id, repo['full_name'], installation, 'app', config, False)
            actual = self.service.github(binding).call('GET', f'/repositories/{repo_id}')
            if int(actual['id']) != repo_id:
                raise ConnectError('앱이 이 저장소에 접근할 수 없습니다.', 403)
            consent = legal_consent.receipt(session, fields, str(guild['id']) + '|' + session.data['repository'])
            existing = next((b for b in self.service.registry.active()
                             if b.repo_id == repo_id and str(b.config['guild_id']) == str(guild['id'])), None)
            from notification_language import normalize_language, selected_language
            choice = (session.data.get('team_draft') or {}).get('language') if fields.get('team_apply') == '1' else fields.get('notification_language')
            notification_language = normalize_language(choice) if choice is not None else selected_language(session,str(guild['id'])+'|'+session.data['repository'],existing,LANGUAGE.get())
            if existing:
                if existing.auth != 'app' or existing.installation != installation:
                    raise ConnectError('기존 연결이 있습니다. 연결 관리에서 확인해 주세요.', 409)
                config = dict(existing.config)
            else:
                config = self.layouts.ensure(self.service.discord(), str(guild['id']), repo_id, actual['full_name'])
                config['workflow_names'] = list(dict.fromkeys(names))
                config['team_notifications'] = {'digest_weekday':4,'weekly_digest_hour':9}
                config['language'] = notification_language
            if fields.get('team_apply') == '1':
                draft = session.data.get('team_draft')
                if not draft or draft['key'] != str(guild['id']) + '|' + session.data['repository']:
                    raise ConnectError('팀 알림 설정을 다시 확인해 주세요.', 409)
                if existing and (draft.get('binding') != existing.id or draft.get('original') != existing.config):
                    raise ConnectError('연결 정보가 변경됐습니다. 다시 확인해 주세요.', 409)
                groups = draft['groups'] if draft['split'] else []
                if groups:
                    layout = self.layouts.ensure(self.service.discord(), str(guild['id']), repo_id, actual['full_name'], groups)
                    groups = layout['routing_groups']
                config.update({'routing_groups': groups, 'team_notifications': draft['options'], 'language': notification_language})
            if not existing or choice is not None or 'language' in config:
                config['language'] = notification_language
            available = {str(c['id']): p for c, p in self.channels(guild['id'])}
            destinations = {**config['channels'], 'alert': config.get('alert_channel'), 'ci': config.get('ci_channel'),
                            **{'role:' + g['id']: g['channel'] for g in config.get('routing_groups', [])}}
            for name, channel in destinations.items():
                needed = DETAIL if name in ('issue', 'pr', 'discussion') or name.startswith('role:') else SUMMARY
                if channel and available.get(str(channel), 0) & needed != needed:
                    raise ConnectError('알림 채널의 접근·전송 권한이 부족합니다. 서버의 채널 권한을 확인한 뒤 다시 시도해 주세요.', 403)
            try:
                if existing and (fields.get('team_apply') == '1' or config != existing.config):
                    self.service.registry.configure(existing.id, config, session.data['discord_user'], session.data['github_user'], consent=consent)
                    identity = existing.id
                else:
                    identity = self.service.registry.connect(repo_id, actual['full_name'], installation, config,
                                                             session.data['discord_user'], session.data['github_user'], consent=consent)
            except ValueError as error:
                raise ConnectError(str(error), 409) from None
            self.service.registry.save_guild_name(str(guild['id']), guild.get('name'))
            session.data['legal_acceptance'] = consent
            session.data['completed'] = identity
            if fields.get('team_apply') == '1':
                session.data.pop('team_draft', None)
            self.sessions.save(session)
            LOG.info('Connected binding=%s', identity)
            return self.redirect(request, '/connect/done')
        raise ConnectError('요청을 찾을 수 없습니다.', 404)

    def handle(self, request):
        language_token = LANGUAGE.set('ko')
        path_token = PAGE_PATH.set('/connect')
        origin_token = PUBLIC_ORIGIN.set(self.settings.base)
        try:
            parsed = urlsplit(request.path)
            LANGUAGE.set(preference(request))
            safe_pages = ('/', '/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq', '/connect/changelog', '/connect/terms', '/connect/privacy', '/connect', '/connect/guilds', '/connect/server', '/connect/repositories', '/connect/channels', '/connect/done', '/connect/team', '/connect/admin', '/connect/admin/changelog')
            PAGE_PATH.set(parsed.path if parsed.path in safe_pages else {
                '/connect/guild': '/connect/guilds', '/connect/repository': '/connect/repositories',
                '/connect/save': '/connect/channels', '/connect/github/start': '/connect/server',
                '/connect/bot/permissions': '/connect/channels',
            }.get(parsed.path, '/connect/team' if parsed.path.startswith('/connect/team/') else '/connect'))
            if request.headers.get('Host', '').lower() != urlsplit(self.settings.base).netloc.lower():
                raise ConnectError('올바른 서비스 주소로 접속해 주세요.', 400)
            if parsed.path == '/connect/admin' or parsed.path.startswith('/connect/admin/'):
                return self.service.operator.handle(request)
            if request.command == 'GET' and parsed.path in ('/robots.txt', '/sitemap.xml', '/connect/robots.txt', '/connect/sitemap.xml'):
                return self.public_document(request, parsed.path)
            if request.command == 'GET' and parsed.path == '/favicon.ico':
                return self.asset(request, '/connect/assets/threadle-avatar.png')
            if request.command == 'GET' and parsed.path.startswith('/connect/assets/'):
                return self.asset(request, parsed.path)
            session = self.session(request)
            self.throttle(self.sessions.digest(session.key) if session else 'anonymous:' + self.client_key(request))
            if request.command == 'GET':
                values = parse_qs(parsed.query, max_num_fields=10, keep_blank_values=True)
                if any(len(value) != 1 for value in values.values()):
                    raise ConnectError('요청이 올바르지 않습니다.')
                return self.get(request, parsed.path, session, {k: v[0] for k, v in values.items()})
            if request.command != 'POST':
                raise ConnectError('지원하지 않는 요청입니다.', 405)
            if request.headers.get('Origin') != self.settings.base:
                raise ConnectError('다른 사이트에서 보낸 요청은 처리할 수 없습니다.', 403)
            if request.headers.get('Content-Type', '').split(';')[0].strip() != 'application/x-www-form-urlencoded':
                raise ConnectError('입력 형식을 확인해 주세요.', 415)
            length = int(request.headers.get('Content-Length', 0))
            if request.headers.get('Transfer-Encoding') or not 0 < length <= 16384:
                raise ConnectError('입력 크기를 확인해 주세요.', 413)
            raw = request.rfile.read(length)
            if len(raw) != length:
                raise ConnectError('요청을 끝까지 받지 못했습니다.')
            values = parse_qs(raw.decode('utf-8'), max_num_fields=80 if parsed.path in ('/connect/team/select', '/connect/team/group', '/connect/team/browse', '/connect/team/unselect') else 16, keep_blank_values=True)
            if any(len(value) != 1 for value in values.values()):
                raise ConnectError('중복된 입력 항목이 있습니다.')
            return self.post(request, parsed.path, session, {k: v[0] for k, v in values.items()})
        except ConnectError as error:
            recovery = {
                '/connect/guild': '/connect/guilds', '/connect/repository': '/connect/repositories',
                '/connect/save': '/connect/channels', '/connect/channels': '/connect/channels',
                '/connect/repositories': '/connect/repositories', '/connect/guilds': '/connect/guilds',
            }.get(urlsplit(request.path).path, '/connect/team' if urlsplit(request.path).path.startswith('/connect/team/') else '/connect/server')
            return self.send(request, error.status, page('연결을 확인해 주세요.', '<div class="card error"><p>'
                             + esc(error) + '</p><a class="button" href="' + recovery + '">현재 단계에서 다시 시도</a>'
                             '<a class="button secondary" href="/connect/server">연결 관리</a>'
                             '<a class="button secondary" href="/connect">처음부터 다시 연결</a></div>'))
        except ApiError as error:
            LOG.warning('Provider unavailable status=%s', error.status)
            return self.send(request, 502, page('외부 연결을 확인해 주세요.', '<div class="card error"><p>Discord 또는 GitHub 권한을 확인해 주세요. '
                             '일시적인 장애라면 잠시 후 다시 시도할 수 있습니다.</p><a href="/connect/server">연결 관리로 돌아가기</a></div>'))
        except (ValueError, TypeError, KeyError, UnicodeError):
            return self.send(request, 400, page('요청을 확인해 주세요.', '<p>입력이 올바르지 않거나 연결 정보가 변경됐습니다.</p><a href="/connect">다시 연결하기</a>'))
        except Exception as error:
            log_exception(LOG,'Connection flow unavailable error=%s', type(error).__name__)
            return self.send(request, 503, page('잠시 후 다시 시도해 주세요.', '<p>지금은 연결을 완료하지 못했습니다. 기존 알림 연결은 유지됩니다.</p><a href="/connect">연결 페이지</a>'))
        finally:
            LANGUAGE.reset(language_token)
            PAGE_PATH.reset(path_token)
            PUBLIC_ORIGIN.reset(origin_token)
