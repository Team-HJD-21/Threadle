"""Selectable, illustrative Discord scenes for the public features page."""
from demo_web import icon, compose, starter_post, thread_states, tools


def security(t):
    return '<section class="security-note"><p class="eyebrow">PRIVATE REPOSITORIES</p><h2>'+t('비공개 저장소도, 공개 범위는 분명하게.', 'Private repositories. A clear audience.')+'</h2><p>'+t('저장된 이벤트 DB와 자동 관리 백업을 암호화하고, 암호화 키는 별도로 보호합니다. 연결할 때 Discord에서 내용을 볼 수 있는 범위를 확인합니다.', 'Stored event databases and managed backups are encrypted, with separately protected keys. Setup asks you to confirm who can see forwarded content in Discord.')+'</p><p class="muted">'+t('Discord로 전달된 본문과 이미지는 해당 채널을 볼 수 있는 구성원에게 공개됩니다. Threadle은 전달을 위해 내용을 처리하며, 종단간 암호화 서비스는 아닙니다.', 'Forwarded text and images are visible to members who can view the Discord channel. Threadle processes content to deliver it; this is not end-to-end encryption.')+'</p><a href="/connect/privacy">'+t('데이터 처리와 보호 방법 보기', 'Read about data handling and protection')+' →</a></section>'


def showcase(t):
    items = [
        ('thread',t('하나의 항목, 하나의 스레드','One item. One thread.'),t('PR·Issue·Discussion의 댓글과 리뷰를 같은 자리에 모으고, 상태와 세부 정보는 기존 메시지에 반영합니다.','Keep comments and reviews with their PR, issue or discussion. Status and details update in the original messages.')),
        ('areas',t('우리 팀에 맞는 작업 영역','Work areas for your team'),t('변경된 디렉터리에 따라 PR을 Frontend·Backend 등 작업 영역 채널로 보냅니다. 여러 영역의 PR은 공통 채널에 모입니다.','Route PRs by changed directories to work-area channels such as Frontend or Backend. PRs spanning areas go to the shared channel.')),
        ('quiet',t('묶음 알림과 방해금지 시간','Batching and Do Not Disturb'),t('새 스레드도 바로 만들고 메시지는 무음으로 전달합니다. 종료 후에는 변경된 항목을 모아 알려드립니다. CI/CD 실패와 충돌 경고는 즉시 전달합니다.','Create threads immediately and deliver messages silently. A roundup follows Do Not Disturb. CI/CD failures and merge conflicts alert immediately.')),
        ('summary',t('리뷰와 열린 PR을 챙기는 요약','Keep reviews and open PRs in view'),t('열린 PR 재알림과 일일·주간 요약을 각각 설정하세요. /summary로 필요할 때 바로 확인할 수도 있습니다.','Configure open-PR reminders and daily and weekly summaries independently. Use /summary whenever you need an overview.')),
    ]

    out='<section class="product-section feature-showcase" data-feature-showcase><div class="section-heading"><h1>'+t('알림 설정','Make it work for your team')+'</h1><p>'+t('네 가지 예시가 차례로 재생됩니다. 궁금한 항목을 직접 선택해도 좋아요.','Four examples play in turn. You can also choose a feature to explore.')+'</p></div><div class="feature-grid" role="tablist" aria-label="'+t('기능 예시','Feature examples')+'">'
    for i,(key,title,description) in enumerate(items):
        out+='<button type="button" class="feature-item" role="tab" id="feature-tab-'+key+'" aria-controls="feature-panel-'+key+'" aria-selected="'+str(i==0).lower()+'" tabindex="'+('0' if i==0 else '-1')+'" data-feature="'+key+'"><span class="feature-title">'+title+'</span><span class="feature-copy">'+description+'</span></button>'
    out+='</div><div class="threadle-preview" data-feature-preview><div class="preview-playback"><button type="button" data-feature-replay>'+t('처음부터 보기','Replay')+'</button><button type="button" data-feature-pause aria-pressed="false" data-play="'+t('예시 재생','Play example')+'" data-pause="'+t('예시 일시정지','Pause example')+'">'+t('예시 일시정지','Pause example')+'</button></div>'
    avatar='<img src="/connect/assets/threadle-avatar.png" alt="" width="32" height="32">'
    def post(content,step,color='',linked=''):
        return '<div class="preview-post" data-feature-step="'+step+'" hidden>'+avatar+'<div><b>Threadle <em>'+icon('check')+' APP</em></b><article class="'+color+'">'+content+'</article>'+linked+'</div></div>'
    for i,(key,title,_) in enumerate(items):
        channel={'thread':'github-pr','areas':'frontend-pr','quiet':'github-issue','summary':'github-alert'}[key]
        kind='issue' if key=='quiet' else 'pr';number='18' if key=='quiet' else '42'
        subject=t('모바일 메뉴 수정','Fix the mobile menu') if key=='quiet' else t('설정 화면 개선','Improve settings')
        out+='<div class="feature-scene" data-feature-kind="'+key+'" role="tabpanel" id="feature-panel-'+key+'" aria-labelledby="feature-tab-'+key+'" tabindex="0"'+(' hidden' if i else '')+'><div class="preview-window"><div class="preview-traffic" aria-hidden="true"><i></i><i></i><i></i></div><div class="preview-case preview-'+('pr' if key in ('thread','areas') else 'issue' if key=='quiet' else 'alert')+'"><aside class="preview-sidebar"><header>Threadle Test Server</header><div class="preview-sidebar-content"><div class="preview-category">🔔 Threadle</div>'
        channels=['github-pr','github-issue','github-discussions','github-ci-cd','github-alert'] if key!='areas' else ['github-pr','frontend-pr','backend-pr','github-alert']
        for name in channels:out+='<div class="preview-channel'+(' is-selected' if name==channel else '')+'">'+icon('hash')+'<span>'+name+'</span></div>'
        out+='</div><div class="preview-user">'+avatar+'<b>Demo team</b></div></aside><div class="preview-main"><header>'+icon('hash')+'<span class="preview-header-name">'+channel+'</span>'+tools('thread','bell','pin','members','search')+'</header><div class="preview-feed preview-summary-feed"><div class="preview-date">'+t('오늘','Today')+'</div>'
        if key!='summary':
            out+=starter_post(t,avatar,kind,number,subject,'main ← feature/settings' if kind=='pr' else '',staged=True)
        else:
            out+=post('<strong>'+t('아직 열린 PR을 모아왔어요.','Here are your open PRs.')+'</strong><p>TeamHJD/threadle · '+t('열린 PR 2개','2 open PRs')+'</p><p>• #42 '+t('설정 화면 개선','Improve settings')+'<br>• #43 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p>','arrival','feature-blue')
            out+=post('<strong>'+t('일일 PR · Issue 현황','Daily PR · Issue overview')+'</strong><p>'+t('열린 PR 2개 · 리뷰 요청 대기 1개','2 open PRs · 1 awaiting review')+'</p>','summary','feature-blue')
            out+='<div class="feature-user-command" data-feature-step="command" hidden><b>Jae</b><code>/summary</code></div>'
            out+=post('<small>'+t('나에게만 보임','Only you can see this')+'</small><strong>TeamHJD/threadle</strong><p><b>PR · 2 Open</b><br>• #42 '+t('설정 화면 개선','Improve settings')+'<br>• #43 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p><p><b>Issue · 1 Open</b><br>• #18 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p>','result','feature-blue')
        out+='</div>'+compose(t,channel)+'</div>'
        if key!='summary':
            out+='<div class="preview-detail" data-feature-detail hidden><header>'+icon('thread')+'<span class="preview-header-name">'+thread_states(kind,number,subject)+'</span>'+tools('muted','more')+'<button type="button" data-feature-back aria-label="'+t('스레드 닫기','Close thread')+'">'+icon('close')+'</button></header><div class="preview-detail-scroll"><h2>'+subject+'</h2><p class="preview-started">'+t('Threadle이 시작함','Started by Threadle')+'</p>'
            description=t('작은 화면에서 메뉴가 잘리는 문제를 확인했습니다.','The menu is clipped on small screens.') if key=='quiet' else t('알림 설정 화면을 개선했습니다.','Improved the notification settings page.')
            if key=='areas':description='<code>frontend/settings.tsx</code><br>'+t('Frontend 영역의 PR입니다. 리뷰와 댓글은 이 스레드에 모입니다.','A PR for the Frontend area. Its reviews and comments stay in this thread.')
            out+=post('<b>Jae</b><strong>'+kind.upper()+' #'+number+' · '+subject+'</strong><p>'+description+'</p>','open','preview-description-card')
            reply=t('작은 화면에서도 확인했어요. 수정 사항을 올릴게요.','Checked on a small screen. I’ll post a fix.') if key=='quiet' else t('모바일에서도 확인했어요. 좋아요!','Checked on mobile too. Looks good!')
            out+=post('<b>Min</b><strong>'+kind.upper()+' #'+number+' · New comment</strong><p>'+reply+'</p>'+('<p>'+icon('muted')+' '+t('무음으로 전달됨','Delivered silently')+'</p>' if key=='quiet' else ''),'reply','feature-gray')
            if key=='thread':out+=post('<b>Min</b><strong>PR #42 · Approved</strong><p>'+t('변경 사항을 확인했습니다.','Changes reviewed.')+'</p>','review','feature-green')
            out+='</div>'+compose(t)+'</div>'
        out+='</div></div>'
        notes={
            'thread':t('시작 알림에서 연결된 스레드를 열고, 댓글·리뷰와 현재 상태를 같은 자리에서 확인합니다.','Open the linked thread from its starter message. Comments, reviews and current status stay together.'),
            'areas':t('frontend/ 변경 → frontend-pr · 여러 작업 영역을 함께 바꾸면 github-pr로 모입니다.','frontend/ changes → frontend-pr. Changes spanning several areas go to github-pr.'),
            'quiet':t('방해금지 22:00–08:00 · 메시지는 무음으로 전달하고 종료 후 변경된 항목을 요약합니다. 새 스레드 생성 알림음은 남을 수 있습니다. CI/CD 실패·충돌 경고는 즉시 전달합니다.','Do Not Disturb 22:00–08:00. Messages are silent; a roundup follows. New-thread sounds may remain. CI/CD failures and conflicts alert immediately.'),
            'summary':t('재알림과 일일·주간 요약은 각각 설정합니다. /summary 결과도 채널에서 확인합니다.','Configure reminders and daily/weekly summaries independently. /summary results appear in the channel too.'),
        }
        out+='<p class="feature-scene-note">'+notes[key]+'</p></div>'
    from product_collaboration import section as collaboration
    return out+'<p class="preview-disclaimer">'+t('작동 예시입니다. 네 가지 예시가 반복 재생되며, 항목 선택·일시정지·다시 보기가 가능합니다.','Illustrative examples cycle automatically. Choose a feature, pause, or replay at any time.')+'</p></div></section>'+collaboration(t)+security(t)
