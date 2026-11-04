"""Selectable, illustrative Discord scenes for the public features page."""
from demo_web import icon, compose, starter_states, thread_states


def security(t):
    return '<section class="security-note"><p class="eyebrow">PRIVATE REPOSITORIES</p><h2>'+t('비공개 저장소도, 공개 범위는 분명하게.', 'Private repositories. A clear audience.')+'</h2><p>'+t('저장된 이벤트 DB와 자동 관리 백업을 암호화하고, 암호화 키는 별도로 보호합니다. 연결할 때 Discord에서 내용을 볼 수 있는 범위를 확인합니다.', 'Stored event databases and managed backups are encrypted, with separately protected keys. Setup asks you to confirm who can see forwarded content in Discord.')+'</p><p class="muted">'+t('Discord로 전달된 본문과 이미지는 해당 채널을 볼 수 있는 구성원에게 공개됩니다. Threadle은 전달을 위해 내용을 처리하며, 종단간 암호화 서비스는 아닙니다.', 'Forwarded text and images are visible to members who can view the Discord channel. Threadle processes content to deliver it; this is not end-to-end encryption.')+'</p><a href="/connect/privacy">'+t('데이터 처리와 보호 방법 보기', 'Read about data handling and protection')+' →</a></section>'


def showcase(t):
    items = [
        ('thread',t('하나의 항목, 하나의 스레드','One item. One thread.'),t('PR·Issue·Discussion의 댓글과 리뷰를 같은 자리에 모으고, 상태와 세부 정보는 기존 메시지에 반영합니다.','Keep comments and reviews with their PR, issue or discussion. Status and details update in the original messages.')),
        ('areas',t('우리 팀에 맞는 작업 영역','Work areas for your team'),t('변경된 디렉터리에 따라 PR을 Frontend·Backend 등 작업 영역 채널로 보냅니다. 여러 영역의 PR은 공통 채널에 모입니다.','Route PRs by changed directories to work-area channels such as Frontend or Backend. PRs spanning areas go to the shared channel.')),
        ('quiet',t('묶음 알림과 방해금지 시간','Batching and Do Not Disturb'),t('새 스레드도 바로 만들고 메시지는 무음으로 전달합니다. 종료 후에는 변경된 항목을 모아 알려드립니다. CI/CD 실패와 충돌 경고는 즉시 전달합니다.','Create threads immediately and deliver messages silently. A roundup follows Do Not Disturb. CI/CD failures and merge conflicts alert immediately.')),
        ('summary',t('리뷰와 열린 PR을 챙기는 요약','Keep reviews and open PRs in view'),t('열린 PR 재알림과 일일·주간 요약을 각각 설정하세요. /summary로 필요할 때 바로 확인할 수도 있습니다.','Configure open-PR reminders and daily and weekly summaries independently. Use /summary whenever you need an overview.')),
    ]
    out='<section class="product-section feature-showcase" data-feature-showcase><div class="section-heading"><h1>'+t('알림 설정','Make it work for your team')+'</h1><p>'+t('항목을 선택하고 Discord에서 어떻게 보이는지 확인해 보세요.','Choose a feature to see how it works in Discord.')+'</p></div><div class="feature-grid" role="tablist" aria-label="'+t('기능 예시','Feature examples')+'">'
    for i,(key,title,description) in enumerate(items):
        out+='<button type="button" class="feature-item" role="tab" id="feature-tab-'+key+'" aria-controls="feature-panel-'+key+'" aria-selected="'+str(i==0).lower()+'" tabindex="'+('0' if i==0 else '-1')+'" data-feature="'+key+'"><span class="feature-title">'+title+'</span><span class="feature-copy">'+description+'</span></button>'
    out+='</div><div class="threadle-preview" data-feature-preview><div class="preview-playback"><button type="button" data-feature-pause aria-pressed="false" data-play="'+t('예시 재생','Play example')+'" data-pause="'+t('예시 일시정지','Pause example')+'">'+t('예시 일시정지','Pause example')+'</button></div>'
    avatar='<img src="/connect/assets/threadle-avatar.png" alt="" width="32" height="32">'
    def post(content,classes='',color=''):
        return '<div class="preview-post '+classes+'">'+avatar+'<div><b>Threadle <em>'+icon('check')+' APP</em></b><article class="'+color+'">'+content+'</article></div></div>'
    for i,(key,title,_) in enumerate(items):
        channel={'thread':'github-pr','areas':'frontend-pr','quiet':'github-issue','summary':'github-alert'}[key]
        out+='<div class="feature-scene" role="tabpanel" id="feature-panel-'+key+'" aria-labelledby="feature-tab-'+key+'" tabindex="0"'+(' hidden' if i else '')+'><div class="preview-window"><div class="preview-traffic" aria-hidden="true"><i></i><i></i><i></i></div><div class="preview-case preview-'+('pr' if key=='thread' else 'issue' if key=='quiet' else key)+'"><aside class="preview-sidebar"><header>Threadle Test Server</header><div class="preview-sidebar-content"><div class="preview-category">🔔 Threadle</div>'
        channels=['github-pr','github-issue','github-discussions','github-ci-cd','github-alert'] if key!='areas' else ['github-pr','frontend-pr','backend-pr','github-alert']
        for name in channels: out+='<div class="preview-channel'+(' is-selected' if name==channel else '')+'">'+icon('hash')+'<span>'+name+'</span></div>'
        out+='</div><div class="preview-user">'+avatar+'<b>Demo team</b></div></aside><div class="preview-main"><header>'+icon('hash')+'<span class="preview-header-name">'+channel+'</span></header><div class="preview-feed preview-summary-feed"><div class="preview-date">'+t('오늘','Today')+'</div>'
        if key in ('thread','quiet'):
            kind='pr' if key=='thread' else 'issue';number='42' if key=='thread' else '18'
            subject=t('설정 화면 개선','Improve settings') if key=='thread' else t('모바일 메뉴 수정','Fix the mobile menu')
            out+=post(starter_states(kind,number,subject,'feature/settings → main' if key=='thread' else ''),'preview-arrival','preview-state-card')
            out+='<div class="preview-thread-card"><b>'+thread_states(kind,number,subject)+'</b><span>'+t('메시지 3개','3 messages')+' ›</span></div>'
            if key=='quiet':out+='<p class="feature-scene-note">'+icon('muted')+' '+t('방해금지 22:00–08:00 · 메시지는 무음으로','Do Not Disturb 22:00–08:00 · Silent messages')+'</p>'
        elif key=='areas':
            out+=post('<strong>PR #42 · '+t('설정 화면 개선','Improve settings')+'</strong><p><code>frontend/settings.tsx</code></p><p><b>Current Status</b> <code>Open</code></p>','preview-arrival','feature-green')
            out+='<p class="feature-scene-note">'+t('Frontend 영역 → frontend-pr','Frontend area → frontend-pr')+'</p>'
        else:
            out+=post('<strong>'+t('아직 열린 PR을 모아왔어요.','Here are your open PRs.')+'</strong><p>TeamHJD/threadle · '+t('열린 PR 2개','2 open PRs')+'</p><p>• #42 '+t('설정 화면 개선','Improve settings')+'<br>• #43 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p>','preview-arrival','feature-blue')
            out+=post('<strong>'+t('일일 PR · Issue 현황','Daily PR · Issue overview')+'</strong><p>'+t('열린 PR 2개 · 리뷰 요청 대기 1개','2 open PRs · 1 awaiting review')+'</p>','preview-message message-1','feature-blue')
        out+='</div>'+compose(t,channel)+'</div><div class="preview-detail"><header>'+icon('thread')+'<span class="preview-header-name">'+title+'</span></header><div class="preview-detail-scroll"><h2>'+title+'</h2>'
        if key=='thread':
            out+=post('<b>Jae</b><strong>PR #42 · '+t('설정 화면 개선','Improve settings')+'</strong><p>'+t('알림 설정 화면을 개선했습니다.','Improved the notification settings page.')+'</p>','preview-message message-0','preview-description-card')
            out+=post('<b>Min</b><strong>PR #42 · New comment</strong><p>'+t('모바일에서도 확인했어요. 좋아요!','Checked on mobile too. Looks good!')+'</p>','preview-message message-1','feature-gray')
            out+=post('<strong>PR #42 · Approved</strong><p>'+t('변경 사항을 확인했습니다.','Changes reviewed.')+'</p>','preview-message message-2','feature-green')
        elif key=='areas':
            out+=post('<strong>PR #42 · '+t('설정 화면 개선','Improve settings')+'</strong><p><code>feature/settings</code> → <code>main</code></p><p>'+t('Frontend의 리뷰와 댓글이 이 스레드로 이어집니다.','Frontend reviews and comments stay in this thread.')+'</p>','preview-message message-0','feature-green')
            out+='<div class="feature-scene-note"><b>'+t('여러 영역을 바꾸면?','Changes across areas?')+'</b><p><code>frontend/</code> + <code>backend/</code> → <b>github-pr</b></p></div>'
        elif key=='quiet':
            out+=post('<b>Min</b><strong>ISSUE #18 · New comment</strong><p>'+t('작은 화면에서 재현했습니다. 수정 사항을 올릴게요.','Reproduced on a small screen. I’ll post a fix.')+'</p><p>'+icon('muted')+' '+t('무음으로 전달됨','Delivered silently')+'</p>','preview-message message-1','feature-gray')
            out+='<div class="feature-scene-note"><b>08:00 · github-alert</b><p>'+t('방해금지 동안 변경된 Issue #18을 요약합니다.','A roundup links to Issue #18 after Do Not Disturb.')+'</p><p>'+t('Discord의 새 스레드 생성 알림음은 발생할 수 있습니다. CI/CD 실패·충돌 경고는 즉시 전달합니다.','Discord may still play a new-thread sound. CI/CD failures and merge-conflict warnings alert immediately.')+'</p></div>'
        else:
            out+=post('<b>Jae</b><p><code>/summary</code></p>','preview-message message-0','feature-gray')
            out+=post('<strong>TeamHJD/threadle</strong><p><b>PR · 2 Open</b><br>• #42 '+t('설정 화면 개선','Improve settings')+'<br>• #43 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p><p><b>Issue · 1 Open</b><br>• #18 '+t('모바일 메뉴 수정','Fix the mobile menu')+'</p>','preview-message message-1','feature-blue')
        out+='</div>'+compose(t)+'</div></div></div></div>'
    return out+'<p class="preview-disclaimer">'+t('작동 예시입니다. 항목을 선택하면 예시가 바뀝니다.','Illustrative examples. Select a feature to switch scenes.')+'</p></div></section>'+security(t)
