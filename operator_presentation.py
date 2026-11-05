"""Read-only presentation helpers for the authenticated operator workspace."""
import datetime as dt
from connect_web import esc
from web_locale import LANGUAGE


def t(ko,en):
    return esc(en if LANGUAGE.get()=='en' else ko)


def heading(email):
    return ('<header class="operator-heading"><div><p class="operator-eyebrow">THREADLE / OPERATIONS</p><h1>'+t('운영 현황','Operations overview')+
            '</h1><p>'+t('서버별 연결과 설정, 처리 상태를 한곳에서 확인하세요.','Connections, preferences and delivery status, in one place.')+
            '</p></div><div class="operator-identity"><span>'+t('관리자 전용','Administrator only')+'</span><small>'+esc(email)+
            '</small></div></header><nav class="operator-nav" aria-label="'+t('관리 화면 메뉴','Administration navigation')+'">'+
            ''.join('<a href="'+href+'">'+t(ko,en)+'</a>' for href,ko,en in [
                ('#operator-connections','연결','Connections'),('#server-accounts','계정 연결','Linked accounts'),
                ('#operator-tools','API·이메일','API & email'),('#operator-events','이벤트 기록','Event history'),
                ('/connect/admin/changelog','사이트 패치노트','Site changelog')])+'</nav>')


def stats(rows):
    active=[r for r in rows if r['enabled'] and r['installation_enabled']]
    items=[(len({r['guild'] for r in active}),'연결된 서버','Connected servers','Discord','서버 기준','By server'),
           (len({r['repo_id'] for r in active}),'연결된 저장소','Repositories','GitHub','중복 제외','Unique repositories'),
           (len(active),'서버별 연결','Connections','Routing','저장소 × 서버','Repository × server'),
           (sum(r['paused'] for r in active),'일시중지','Paused','Delivery','대기열 유지','Queue retained')]
    return '<div class="admin-stats">'+''.join('<article class="operator-stat"><span>'+t(ko,en)+'</span><strong>'+str(value)+'</strong><small>'+tag+' <i>·</i> '+t(note_ko,note_en)+'</small></article>' for value,ko,en,tag,note_ko,note_en in items)+'</div>'


def queue_counts(pending,retries,notices,active):
    return '<dl class="operator-queue">'+''.join('<div><dt>'+t(ko,en)+'</dt><dd'+(' class="needs-attention"' if value and index==1 and active else '')+'>'+str(value)+'</dd></div>' for index,(value,ko,en) in enumerate([(pending,'대기 이벤트','Queued'),(retries,'재시도 중','Retrying'),(notices,'예약·묶음 알림','Scheduled / batched')]))+'</dl>'


def event_history(service):
    with service.registry.db() as db:
        rows=db.execute('SELECT kind,guild,channel,message,author,actor,source,outcome,created FROM chat_events ORDER BY id DESC LIMIT 30').fetchall()
        audits=db.execute('SELECT guild,channel,actor,target,action,count,created FROM chat_audit_events ORDER BY created DESC LIMIT 10').fetchall()
    kinds={'message-create':('메시지 생성','Message created'),'message-edit':('메시지 수정','Message edited'),
           'message-delete':('메시지 삭제','Message deleted'),'github-comment-delete':('GitHub 댓글 삭제','GitHub comment deletion'),
           'thread-create':('스레드 생성','Thread created'),'thread-update':('스레드 갱신','Thread updated'),
           'thread-delete':('스레드 삭제','Thread deleted'),'reaction-add':('반응 추가','Reaction added'),
           'reaction-remove':('반응 제거','Reaction removed')}
    out='<section class="operator-section" id="operator-events"><div class="operator-section-heading"><div><p class="operator-eyebrow">EVENTS</p><h2>'+t('대화 이벤트 기록','Conversation events')+'</h2></div><span>'+t('최근 30건','Latest 30')+'</span></div><p class="muted">'+t('관리 채널·스레드의 이벤트만 기록합니다. 본문과 인증정보는 보관하지 않으며, 확인되지 않은 삭제자는 미상으로 표시합니다.','Only events from managed conversations are recorded. Bodies and credentials are excluded; unknown deletion actors stay unknown.')+'</p>'
    if rows:
        out+='<div class="operator-table-scroll"><table class="operator-table"><caption class="sr-only">'+t('최근 대화 이벤트','Recent conversation events')+'</caption><thead><tr>'+''.join('<th scope="col">'+t(ko,en)+'</th>' for ko,en in [('시각 (UTC)','Time (UTC)'),('이벤트','Event'),('서버·채널','Server / channel'),('주체','Actor'),('결과','Outcome')])+'</tr></thead><tbody>'
        for kind,guild,channel,message,author,actor,source,outcome,created in rows:
            stamp=dt.datetime.fromtimestamp(created,dt.timezone.utc).strftime('%m-%d %H:%M:%S')
            label=kinds.get(kind,(kind,kind))
            outcome_label={'done':('완료','Done'),'queued':('대기','Queued'),'failed':('실패','Failed'),'observed':('기록','Recorded'),'succeeded':('성공','Succeeded')}.get(outcome,(outcome,outcome))
            outcome_class=' outcome-warning' if outcome in ('queued','failed') else ''
            out+='<tr><td><time>'+stamp+'</time></td><td><b>'+t(*label)+'</b><small>'+esc(source)+' · '+esc(message or '—')+'</small></td><td><code>'+esc(guild)+'</code><small>'+esc(channel)+'</small></td><td>'+('<code>'+esc(actor)+'</code>' if actor else t('미상','Unknown'))+('<small>'+t('작성자','Author')+' '+esc(author)+'</small>' if author else '')+'</td><td><span class="operator-outcome'+outcome_class+'">'+t(*outcome_label)+'</span></td></tr>'
        out+='</tbody></table></div>'
    else:
        out+='<div class="operator-empty"><strong>'+t('아직 기록된 이벤트가 없어요.','No conversation events yet.')+'</strong><p>'+t('관리 채널에서 활동이 발생하면 이곳에 기록됩니다.','Activity in managed conversations will appear here.')+'</p></div>'
    if audits:
        out+='<details class="operator-audit-evidence"><summary>'+t('Discord 감사 로그 증거','Discord audit-log evidence')+' ('+str(len(audits))+')</summary><p class="muted">'+t('감사 로그는 여러 삭제를 묶을 수 있어 특정 메시지의 삭제자로 단정하지 않습니다.','Audit entries can combine deletions and are not attributed to one specific message.')+'</p><ul>'
        for guild,channel,actor,target,action,count,created in audits:
            out+='<li><code>'+esc(actor or 'unknown')+'</code> · '+t('대상','Target')+' <code>'+esc(target or '—')+'</code> · '+esc(count or 0)+' '+t('건','events')+' · '+t('채널','Channel')+' <code>'+esc(channel)+'</code></li>'
        out+='</ul></details>'
    return out+'</section>'
