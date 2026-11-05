"""Read-only, administrator-only views of server-scoped account consent."""
import datetime as dt
import re

from connect_auth import ConnectError
from web_locale import LANGUAGE

PAGE_SIZE=50


def say(ko,en):return en if LANGUAGE.get()=='en' else ko


def available(db):
    return db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('personal_accounts','personal_guilds')").fetchone()[0]==2


def panel(registry,selected):
    from connect_web import esc
    guilds={str(row['guild']):row.get('guild_name') or say('이름 확인 대기','Name unavailable') for row in selected}
    shown=sorted(guilds.items(),key=lambda value:(value[1].casefold(),value[0]))[:50]
    counts={}
    if shown:
        with registry.db() as db:
            if available(db):
                marks=','.join('?' for _ in shown)
                counts=dict(db.execute('SELECT g.guild,count(*) FROM personal_guilds g JOIN personal_accounts a ON a.discord=g.discord '
                                       'WHERE g.guild IN ('+marks+') GROUP BY g.guild',[guild for guild,_ in shown]))
    body='<section id="server-accounts" class="card admin-account-panel"><h2>'+say('서버별 계정 연결','Linked accounts by server')+'</h2>'
    body+='<p class="muted">'+say('이 서버에서 /account connect를 완료한 사용자를 확인합니다. 저장소가 여러 개여도 서버당 한 번만 표시합니다.',
                                  'View people who completed /account connect for each server. Multiple repositories share one server list.')+'</p>'
    if len(guilds)>50:body+='<p class="muted">'+say('처음 50개 서버를 표시합니다. 위 검색으로 범위를 좁혀 주세요.','Showing the first 50 servers. Use search above to narrow the list.')+'</p>'
    body+='<div class="admin-account-servers">'
    for guild,name in shown:
        body+='<a class="admin-account-server" href="/connect/admin/accounts/'+esc(guild)+'"><span><strong translate="no">'+esc(name)+'</strong><small>Discord '+esc(guild)+'</small></span><span class="admin-account-count">'+str(counts.get(guild,0))+say('명',' linked')+'<small>'+say('사용자 보기 →','View people →')+'</small></span></a>'
    if not shown:body+='<p class="muted">'+say('표시할 서버가 없습니다.','No servers match this view.')+'</p>'
    return body+'</div></section>'


def members(registry,guild,page):
    from connect_web import esc
    if not re.fullmatch(r'[1-9][0-9]{0,24}',guild) or not re.fullmatch(r'[1-9][0-9]{0,5}',page):
        raise ConnectError(say('올바른 계정 목록 요청이 아닙니다.','Invalid account list request.'),400)
    page=int(page)
    with registry.db() as db:
        server=db.execute('SELECT (SELECT name FROM guild_profiles WHERE guild=?) WHERE EXISTS(SELECT 1 FROM bindings WHERE guild=?)',(guild,guild)).fetchone()
        if server is None:raise ConnectError(say('서버를 찾을 수 없습니다.','Server not found.'),404)
        count=0;rows=[]
        if available(db):
            count=db.execute('SELECT count(*) FROM personal_guilds g JOIN personal_accounts a ON a.discord=g.discord WHERE g.guild=?',(guild,)).fetchone()[0]
            if page>max(1,(count+PAGE_SIZE-1)//PAGE_SIZE):raise ConnectError(say('페이지를 찾을 수 없습니다.','Page not found.'),404)
            # Never select credentials, generations, queued bodies, or other servers' preferences.
            rows=db.execute('SELECT a.discord,a.github,a.login,g.mentions,g.comments,g.consent,a.credential IS NOT NULL '
                            'FROM personal_guilds g JOIN personal_accounts a ON a.discord=g.discord '
                            'WHERE g.guild=? ORDER BY g.consent DESC,g.discord LIMIT ? OFFSET ?',
                            (guild,PAGE_SIZE,(page-1)*PAGE_SIZE)).fetchall()
        elif page!=1:raise ConnectError(say('페이지를 찾을 수 없습니다.','Page not found.'),404)
    name=server[0] or say('이름 확인 대기','Name unavailable')
    body='<p><a href="/connect/admin#server-accounts">← '+say('관리자로 돌아가기','Back to administration')+'</a></p>'
    body+='<section class="card admin-account-panel"><p><strong translate="no">'+esc(name)+'</strong> · Discord <code>'+esc(guild)+'</code></p>'
    body+='<p class="muted">'+say('이 서버에서 연결한 계정 '+str(count)+'명. 현재 서버 멤버 전체 목록은 아닙니다.',
                                  str(count)+' linked accounts for this server. This is not the full current server membership list.')+'</p>'
    body+='<div class="admin-account-members">'
    for discord,github,login,mentions,comments,consent,can_comment in rows:
        identity=esc(login)
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}',login):identity='<a href="https://github.com/'+esc(login)+'" target="_blank" rel="noopener noreferrer">@'+identity+'</a>'
        discord_link=esc(discord)
        if re.fullmatch(r'[1-9][0-9]{0,24}',discord):discord_link='<a href="https://discord.com/users/'+esc(discord)+'" target="_blank" rel="noopener noreferrer">'+esc(discord)+'</a>'
        status=say('사용','On') if comments and can_comment else say('재연결 필요','Reconnect required') if comments else say('끔','Off')
        try:stamp=dt.datetime.fromtimestamp(consent,dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')
        except (ValueError,TypeError,OverflowError,OSError):stamp='—'
        body+='<article class="admin-account-member"><div class="admin-account-person"><strong translate="no">'+identity+'</strong><span>'+say('연결됨','Linked')+'</span><small>Discord '+discord_link+'</small></div><dl>'
        body+='<div><dt>'+say('자동 멘션','Automatic mentions')+'</dt><dd>'+say('사용','On')+'</dd></div>' if mentions else '<div><dt>'+say('자동 멘션','Automatic mentions')+'</dt><dd>'+say('끔','Off')+'</dd></div>'
        body+='<div><dt>'+say('댓글 전송','Comment forwarding')+'</dt><dd>'+status+'</dd></div><div><dt>'+say('이 서버 연결 시각','Linked for this server')+'</dt><dd>'+stamp+'</dd></div></dl></article>'
    if not rows:body+='<p class="muted">'+say('아직 계정을 연결한 사용자가 없습니다.','No accounts have been linked yet.')+'</p>'
    body+='</div>'
    if count>PAGE_SIZE:
        body+='<nav class="admin-account-pagination" aria-label="'+say('계정 목록 페이지','Account list pages')+'">'
        last=max(1,(count+PAGE_SIZE-1)//PAGE_SIZE)
        for target,label in ((page-1,say('이전','Previous')),(page+1,say('다음','Next'))):
            if 1<=target<=last:body+='<a class="button secondary" href="/connect/admin/accounts/'+guild+'?page='+str(target)+'">'+label+'</a>'
        body+='<span>'+str(page)+' / '+str(last)+'</span></nav>'
    return body+'</section>'
