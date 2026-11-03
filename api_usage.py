"""Manual admin snapshots; Discord observation never performs network requests."""
import json
import math
import threading
import time
from urllib.request import Request, urlopen


def number(value):
    try:
        result=float(value)
        return result if math.isfinite(result) and result>=0 else None
    except (ValueError,TypeError):
        return None


class ApiUsage:
    def __init__(self, root):
        self.path=root/'api-usage.json'
        self.lock=threading.Lock()
        self.refresh_lock=threading.Lock()
        self.buckets={}
        self.global_hit=None

    def observe(self, method, path, headers, status=200, detail=None):
        # Never retain paths (webhook tokens), message content or authorization.
        h={k.lower():v for k,v in headers.items()}
        bucket=h.get('x-ratelimit-bucket')
        if not bucket and status!=429:return
        import hashlib
        key=hashlib.sha256(str(bucket or 'unknown').encode()).hexdigest()[:12]
        # Major parameters have independent quotas even for identical bucket hashes.
        import re
        major=re.match(r'/((?:channels|guilds)/[0-9]+)(?:/|$)',path)
        scope=major[1] if major else 'other'
        entry={'bucket':key,'scope':scope,'method':method,'observed':time.time(),
               'limit':number(h.get('x-ratelimit-limit')),
               'remaining':number(h.get('x-ratelimit-remaining')),
               'reset':number(h.get('x-ratelimit-reset')),
               'retry_after':number((detail or {}).get('retry_after',h.get('retry-after'))),
               'limited':status==429}
        with self.lock:
            self.buckets[key+scope]=entry
            if status==429 and ((detail or {}).get('global') or h.get('x-ratelimit-global')=='true'):
                self.global_hit=entry['observed']
            while len(self.buckets)>64:
                del self.buckets[min(self.buckets,key=lambda k:self.buckets[k]['observed'])]

    def read(self):
        try:return json.loads(self.path.read_text('utf-8'))
        except (OSError,ValueError):return None

    def refresh(self, service):
        if not self.refresh_lock.acquire(False):return False
        try:
            groups={}
            for b in service.registry.active(include_paused=True):
                groups.setdefault(('app',b.installation) if b.auth=='app' else ('legacy',0),b)
            github=[]
            for key,b in groups.items():
                row={'scope':f'Installation {key[1]}' if key[0]=='app' else 'Legacy token'}
                try:
                    token=(service.tokens.token(b.installation,b.repo_id) if b.auth=='app' else service.env.get('GITHUB_TOKEN'))
                    if not token:raise ValueError('Credentials unavailable')
                    request=Request('https://api.github.com/rate_limit',headers={
                        'Authorization':'Bearer '+token,'User-Agent':'Threadle-admin',
                        'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
                    with urlopen(request,timeout=5) as response:
                        data=json.loads(response.read(65536))
                    row['resources']={name:{k:number(values.get(k)) for k in ('limit','remaining','used','reset')}
                                      for name,values in data['resources'].items() if name in ('core','graphql','search')}
                except Exception:
                    # Do not expose exception text, which can contain credentials or URLs.
                    row['error']=True
                row['observed']=time.time()
                github.append(row)
            with self.lock:
                discord=list(self.buckets.values())
                global_hit=self.global_hit
            snapshot={'created':time.time(),'github':github,'discord':discord,'global_hit':global_hit}
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(snapshot),encoding='utf-8')
            temporary.replace(self.path)
            return True
        finally:self.refresh_lock.release()


def panel(usage, session):
    from connect_web import esc,form
    from web_locale import LANGUAGE
    en=LANGUAGE.get()=='en'
    def t(ko,english):return english if en else ko
    def stamp(value):return time.strftime('%Y-%m-%d %H:%M:%S UTC',time.gmtime(value)) if value else '—'
    def n(value):return str(int(value)) if value is not None else '—'
    body='<section class="card" id="api-usage"><h2>API '+t('사용량','usage')+'</h2>'
    body+='<p class="muted">'+t('조회 버튼을 누를 때만 갱신합니다. 자동 조회는 하지 않습니다.','Updates only when you click Refresh. No automatic queries.')+'</p>'
    body+=form('/connect/admin/api-usage/refresh',session,'<button>'+t('API 사용량 조회','Refresh API usage')+'</button>')
    data=usage.read()
    if not data:return body+'<p>'+t('아직 조회하지 않았습니다.','Not queried yet.')+'</p></section>'
    body+='<p>'+t('마지막 조회: ','Last refresh: ')+stamp(data['created'])+'</p><h3>GitHub</h3>'
    body+='<p class="muted">'+t('설치 단위 한도입니다. 연결된 서버·저장소별로 합산하지 않습니다. 2차 제한은 별도입니다.','Quotas are shared per installation, not added per server or repository. Secondary limits are separate.')+'</p>'
    for row in data['github']:
        body+='<h4>'+esc(row['scope'])+'</h4>'
        if row.get('error'):
            body+='<p>'+t('조회 실패 · 자격 증명 또는 연결 상태를 확인해 주세요.','Query failed. Check credentials or connectivity.')+'</p>'
            continue
        body+='<ul>'
        for name,values in row['resources'].items():
            body+='<li>'+esc({'core':'REST','graphql':'GraphQL','search':'Search'}[name])+': '+t('남음 ','remaining ')+n(values['remaining'])+' / '+n(values['limit'])+' · '+t('사용 ','used ')+n(values['used'])+' · Reset '+stamp(values['reset'])+'</li>'
        body+='</ul><p class="muted">'+stamp(row['observed'])+'</p>'
    if not data['github']:body+='<p>'+t('활성 연결이 없습니다.','No active connections.')+'</p>'
    body+='<h3>Discord</h3><p class="muted">'+t('최근 실제 응답에서 관측한 요청 묶음별 잔량입니다. 전체 잔량이 아니며, 초기화 시각이 지나도 실제 응답 전까지 새 값으로 추정하지 않습니다. 재시작 전 관측은 포함하지 않습니다.','Remaining requests per bucket from recent real responses, not a global quota. Values are not extrapolated after reset. Observations before restart are excluded.')+'</p>'
    body+='<p>'+t('마지막 전역 제한 발생: ','Last observed global limit: ')+stamp(data.get('global_hit'))+'</p>'
    if data['discord']:body+='<details><summary>'+t('요청별 제한 상세 보기','View bucket details')+' ('+str(len(data['discord']))+')</summary>'
    for row in sorted(data['discord'],key=lambda r:r['observed'],reverse=True):
        body+='<p><code>'+esc(row['method']+' '+row['scope']+' · '+row['bucket'])+'</code><br>'+t('남음 ','Remaining ')+n(row['remaining'])+' / '+n(row['limit'])+' · Reset '+stamp(row['reset'])+'<br><small>'+t('응답 확인: ','Observed: ')+stamp(row['observed'])+(' · HTTP 429' if row['limited'] else '')+'</small></p>'
    if data['discord']:body+='</details>'
    if not data['discord']:body+='<p>'+t('아직 제한 헤더가 포함된 응답이 없습니다.','No responses with rate-limit headers observed yet.')+'</p>'
    return body+'</section>'
