from html import escape
from discord_threads import Topic
from root_status import decorate


def starter_states(kind,number,title,detail=''):
    topic=Topic('TeamHJD/threadle',kind,int(number),title,'',{})
    final={'pr':{'merged':True},'issue':{'state':'closed'},'discussion':{'isAnswered':True}}[kind]
    initial=decorate(topic,{}, {'title':title})
    changed=decorate(topic,{}, {'title':title,**final})
    middle=''
    def markup(embed):return '<strong>'+escape(embed['title'])+'</strong>'+('<p>'+escape(detail.replace('`',''))+'</p>' if detail else '')+'<p><b>Current Status</b> <code>'+escape(embed['fields'][-1]['value'].strip('`'))+'</code></p>'
    return '<div class="preview-state-before">'+markup(initial)+'</div><div class="preview-state-after">'+markup(changed)+'</div>'


ICONS = {
    'hash': '<path d="m9 3-2 18M17 3l-2 18M4 9h17M3 15h17" stroke-width="2.6"/>',
    'chevron': '<path d="m6 9 6 6 6-6"/>',
    'right': '<path d="m9 5 7 7-7 7"/>',
    'plus': '<path d="M12 4v16M4 12h16"/>',
    'close': '<path d="m5 5 14 14M19 5 5 19"/>',
    'more': '<g fill="currentColor" stroke="none"><circle cx="5" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="19" cy="12" r="2"/></g>',
    'thread': '<g transform="rotate(-45 12 12)"><path d="M7 3h10M7 21h10M8 3v18M16 3v18M8 7h8M8 12h8M8 17h8" stroke-width="2.6"/></g>',
    'calendar': '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M3 10h18M7 3v4M17 3v4"/><path d="M7 14h3v3H7z" fill="currentColor" stroke="none"/>',
    'boost': '<path d="m12 2 9 6v9l-9 5-9-5V8Z"/><path d="m12 6 5 3v6l-5 3-5-3V9Z"/>',
    'members': '<g fill="currentColor" stroke="none"><circle cx="10" cy="7" r="4"/><path d="M2 21v-3a8 8 0 0 1 16 0v3Z"/><path d="M17 3a4 4 0 0 1 0 8 7 7 0 0 0-1-8ZM19 13a7 7 0 0 1 4 6v2h-3v-3a10 10 0 0 0-1-5Z"/></g>',
    'invite': '<circle cx="9" cy="6" r="4" fill="currentColor" stroke="none"/><path d="M1 21v-3a8 8 0 0 1 13-6M19 12v9M14.5 16.5h9"/>',
    'bell': '<path d="M20 17H4l2-3V9a6 6 0 0 1 12 0v5Z" fill="currentColor" stroke="none"/><path d="M10 20a2 2 0 0 0 4 0M12 2v2"/>',
    'muted': '<path d="M18 12V9a6 6 0 0 0-8-6M6 8v6l-2 3h13M10 20a2 2 0 0 0 4 0M3 3l18 18"/>',
    'pin': '<path d="m15 2 7 7-3 1-3 5-1 4-4-4-7 7 5-9-4-4 4-1 5-3Z" fill="currentColor" stroke="none"/>',
    'search': '<circle cx="10.5" cy="10.5" r="7.5"/><path d="m16 16 6 6"/>',
    'mic': '<rect x="8" y="2" width="8" height="13" rx="4" fill="currentColor" stroke="none"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8"/>',
    'headphones': '<path d="M3 14v-3a9 9 0 0 1 18 0v3"/><rect x="3" y="12" width="4" height="9" rx="2" fill="currentColor" stroke="none"/><rect x="17" y="12" width="4" height="9" rx="2" fill="currentColor" stroke="none"/>',
    'settings': '<path d="m10 2-1 3-3 1-3-1-2 4 2 2v3l-2 2 2 4 3-1 3 1 1 3h4l1-3 3-1 3 1 2-4-2-2v-3l2-2-2-4-3 1-3-1-1-3Z" fill="currentColor" stroke="none"/><circle cx="12" cy="12" r="4" fill="#232428" stroke="none"/>',
    'gift': '<rect x="3" y="9" width="18" height="4" rx="1"/><path d="M5 13v8h14v-8M12 9v12M12 9H8a3 3 0 1 1 3-3Zm0 0h4a3 3 0 1 0-3-3Z"/>',
    'gif': '<rect x="2" y="3" width="20" height="18" rx="3" fill="currentColor" stroke="none"/><path d="M8 9H5v6h3v-3H7M11 9v6M14 15V9h5M14 12h4" stroke="#383a40" stroke-width="1.4"/>',
    'sticker': '<path d="M20 13V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h8Zm0 0h-5a2 2 0 0 0-2 2v5"/><path d="M7 8h.1M14 8h.1M7 12q3 3 6 0"/>',
    'smile': '<circle cx="12" cy="12" r="10"/><path d="M7 14q5 6 10 0M8 8h.1M16 8h.1"/>',
    'apps': '<g fill="currentColor" stroke="none"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1" transform="rotate(12 17.5 6.5)"/><rect x="3" y="14" width="7" height="7" rx="1"/><path d="m17 13 5 8H12Z"/></g>',
    'send': '<path d="m2 3 21 9-21 9 4-8 10-1-10-1Z" fill="currentColor" stroke="none"/>',
    'check': '<path d="m4 12 5 5L20 6" stroke-width="3"/>',
    'status': '<circle cx="12" cy="12" r="8" fill="currentColor" stroke="none"/>',
}


def icon(name):
    return '<svg class="preview-icon icon-' + name + '" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">' + ICONS[name] + '</svg>'


def tools(*names):
    return '<span class="preview-tools" aria-hidden="true">' + ''.join(icon(name) for name in names) + '</span>'


def compose(t, channel=None):
    hint = (t('메시지 보내기', 'Message') + ' #' + channel) if channel else t('스레드에 답장', 'Reply in thread')
    return '<div class="preview-compose">' + icon('plus') + '<span class="preview-compose-hint">' + hint + '</span>' + tools('gift','gif','sticker','smile','apps','send') + '</div>'


def ci_panes(t, avatar):
    title = 'Threadle CI and deployment'
    badge = '<b>Threadle <em>' + icon('check') + ' APP</em></b>'
    out = '<div class="preview-main"><header>' + icon('hash') + '<span class="preview-header-name">github-ci-cd</span>' + tools('bell','pin','members','search') + '</header><div class="preview-feed"><div class="preview-welcome"><div class="preview-hash">' + icon('hash') + '</div><h2>' + t('실행부터 완료까지.', 'From run to result.') + '</h2><p>' + t('워크플로의 진행과 검사 결과를 한눈에.', 'Follow the workflow and its checks at a glance.') + '</p></div><div class="preview-date">' + t('오늘', 'Today') + '</div>'
    out += '<div class="preview-post preview-arrival">' + avatar + '<div>' + badge + '<article><b>Run #128</b><strong>' + title + '</strong><span class="preview-ci-running">' + '🔄 Running' + '</span><span class="preview-ci-success">' + '✅ Succeeded' + '</span><dl class="preview-ci-metadata">'
    for ko,en,value in [('Repository','Repository','TeamHJD/threadle'),('Branch','Branch','main'),('Trigger','Trigger','push'),('Attempt','Attempt','1')]:
        out += '<div><dt>' + t(ko,en) + '</dt><dd>' + value + '</dd></div>'
    out += '</dl><p class="preview-link">' + t('GitHub 실행 결과', 'View run on GitHub') + '</p></article></div></div></div>' + '</div>'
    out += '<div class="preview-detail"><header>' + icon('check') + '<span class="preview-header-name">Run #128</span>' + tools('more','close') + '</header><div class="preview-detail-scroll"><p class="preview-started">GITHUB ACTIONS</p><h2>' + title + '</h2><p><code>main</code> · <code>a1b2c3d</code></p><div class="preview-checks">'
    for index, (name, duration) in enumerate([('Set up job','2s'),('Checkout','4s'),('Install dependencies','9s'),('Run tests','32s'),('Build','12s')]):
        out += '<div class="preview-check-step step-' + str(index) + '">' + icon('check') + '<span>' + name + '</span><time>' + duration + '</time></div>'
    out += '</div><p class="preview-ci-success preview-ci-complete">' + icon('check') + ' ' + t('모든 검사가 통과했어요.', 'All checks passed.') + '</p><p class="preview-ci-note">' + t('실행 상태는 같은 메시지에서 갱신되고, 실패는 alert 채널에서도 알려드려요.', 'Run status updates in one message. Failures also appear in the alert channel.') + '</p></div></div></div>'
    return out


def alert_panes(t, avatar):
    badge = '<b>Threadle <em>' + icon('check') + ' APP</em></b>'
    prs = [t('설정 화면 개선', 'Improve settings'), t('모바일 메뉴 수정', 'Fix mobile navigation')]
    issues = [t('연결 상태 표시 개선', 'Improve connection status'), t('알림 언어 설정 추가', 'Add notification language settings')]
    issues += [t('설정 저장 오류 확인', 'Check settings save errors'),t('모바일 목록 정렬', 'Sort lists on mobile'),t('요약 제목 다듬기', 'Polish digest headings'),t('도움말 링크 확인', 'Check help links'),t('시간대 설명 보완', 'Clarify time zone settings')]
    discussion = t('다음 업데이트 아이디어', 'Ideas for the next update')
    def titles(items):
        return '<ul class="preview-summary-titles">' + ''.join('<li>' + text + '</li>' for text in items) + '</ul>'
    out = '<div class="preview-main"><header>' + icon('hash') + '<span class="preview-header-name">github-alert</span>' + tools('bell','pin','members','search') + '</header><div class="preview-feed preview-summary-feed"><div class="preview-date">09:00</div><div class="preview-post preview-arrival">' + avatar + '<div>' + badge + '<p>' + t('🧵 오늘 확인할 개발 소식을 모아왔어요.', '🧵 Here’s your team roundup.') + '</p><article><strong>' + t('일일 PR · Issue 현황', 'Daily PR & issue digest') + '</strong><p>TeamHJD/threadle</p><p>' + t('현재 열린 PR 2개 · 리뷰 요청 대기 1개 · 초안 0개', '2 open PRs · 1 awaiting review · 0 drafts') + '</p>' + titles(prs) + '</article><article><strong>' + t('가장 오래된 미해결 Issue', 'Oldest open issues') + '</strong>' + titles(issues[:5]) + '<small>7 remaining issues…</small></article></div></div></div></div>'
    out += '<div class="preview-detail"><header>' + icon('hash') + '<span class="preview-header-name">github-alert</span>' + tools('more') + '</header><div class="preview-detail-scroll"><h2>' + t('필요할 때 바로 요약.', 'A roundup on demand.') + '</h2><p class="preview-started">' + t('명령어를 사용한 채널에서 저장소별로 확인해요.', 'Get a roundup per repository, right in this channel.') + '</p><div class="preview-post preview-message message-1">' + avatar + '<div><b>Jae</b><p><code>/summary</code></p></div></div><div class="preview-post preview-message message-2">' + avatar + '<div>' + badge + '<article class="preview-command-summary"><strong>TeamHJD/threadle</strong><b>' + t('PR · 2개 열림', 'PR · 2 open') + '</b>' + titles(prs) + '<b>' + t('Issue · 7개 열림', 'Issue · 7 open') + '</b>' + titles(issues) + '<small>' + t('미해결 Issue는 가장 오래된 순서로 최대 10개 표시', 'Up to 10 open issues, oldest first') + '</small><b>' + t('Discussion · 1개 열림', 'Discussion · 1 open') + '</b>' + titles([discussion]) + '</article></div></div></div>' + compose(t,'github-alert') + '</div></div>'
    return out


def demonstration(t):
    cases = [
        ('pr', 'PR', 'github-pr', '42', t('설정 화면 개선', 'Improve settings'), '`feature/settings` → `main`',
         t('설정 화면을 작은 화면에서도 편하게 사용할 수 있도록 수정했습니다.', 'Make the settings page easier to use on smaller screens.'),
         t('변경 사항 확인했어요. LGTM!', 'Looks good to me. LGTM!')),
        ('issue', 'ISSUE', 'github-issue', '18', t('Threadle의 첫 번째 배달', 'Threadle’s first little delivery'), 'Bug · Priority: High',
         t('GitHub의 새 소식을 하나의 스레드로 배달해 주세요.', 'Bring this GitHub issue and its updates into one cozy thread.'),
         t('배달 도착! 댓글도 같은 스레드에 모였어요.', 'Delivery received! This comment found the same thread.')),
        ('discussion', 'DISCUSSION', 'github-discussions', '9', t('다음 업데이트 아이디어', 'Ideas for the next update'), 'Ideas · enhancement',
         t('다음 업데이트에서 어떤 기능을 보고 싶으신가요?', 'What would you like to see in the next update?'),
         t('설정을 내보낼 수 있으면 좋겠어요.', 'Exporting settings would be useful.')),
        ('ci', 'CI', 'github-ci-cd', '128', 'Threadle CI and deployment', '`main` · `push`',
         t('integration-tests 검사에 실패했습니다.', 'The integration-tests check failed.'),
         t('CI 채널에서 실행 결과를 확인해 주세요.', 'Check the run details in the CI channel.')),
        ('alert', 'ALERT', 'github-alert', '', '', '', '', ''),
    ]
    avatar = '<img src="/connect/assets/threadle-avatar.png" alt="" width="32" height="32">'
    out = '<section class="threadle-preview" aria-label="' + t('Discord 스레드 작동 예시', 'Discord thread preview') + '">'
    for i, (key, *_) in enumerate(cases):
        out += '<input class="sr-only" type="radio" name="threadle-preview" id="preview-' + key + '"' + (' checked' if i == 0 else '') + '>'
    out += '<div class="preview-playback"><button type="button" data-preview-pause aria-pressed="false" data-pause="' + t('자동 재생 일시정지', 'Pause autoplay') + '" data-play="' + t('자동 재생', 'Play previews') + '">' + t('자동 재생 일시정지', 'Pause autoplay') + '</button></div>'
    out += '<div class="preview-window"><div class="preview-traffic" aria-hidden="true"><i></i><i></i><i></i></div>'
    for key, label, channel, number, title, detail, description, reply in cases:
        topic = label + ' #' + number
        out += '<div class="preview-case preview-' + key + '"><aside class="preview-sidebar"><header><span class="preview-server-name">Threadle Test Server</span>' + icon('chevron') + icon('invite') + '</header><div class="preview-sidebar-content"><div class="preview-category"><span>🔔 Threadle</span>' + icon('chevron') + icon('plus') + '</div>'
        for target, name, emoji in [('pr','github-pr','🙏'),('issue','github-issue','‼️'),('discussion','github-discussions','🙋'),('ci','github-ci-cd','🔄'),('alert','github-alert','🚨')]:
            out += '<button type="button" data-preview-key="' + target + '" aria-pressed="' + ('true' if target == key else 'false') + '" class="preview-channel' + (' is-selected' if name == channel else '') + '">' + icon('hash') + '<span>' + emoji + ' ' + name + '</span></button>'
        out += '</div><div class="preview-user">' + avatar + '<b>Team HJD</b><span class="preview-status">' + icon('status') + '</span>' + tools('mic','headphones','settings') + '</div></aside>'
        if key == 'ci':
            out += ci_panes(t, avatar)
            continue
        if key == 'alert':
            out += alert_panes(t, avatar)
            continue
        out += '<div class="preview-main"><header>' + icon('hash') + '<span class="preview-header-name">' + channel + '</span>' + tools('thread','bell','pin','members','search') + '</header><div class="preview-feed"><div class="preview-welcome"><div class="preview-hash">' + icon('hash') + '</div><h2>' + t('환영합니다!', 'Welcome to #') + ' ' + channel + '!</h2><p>' + t('이 채널의 시작이에요.', 'This is the start of this channel.') + '</p></div><div class="preview-date">' + t('오늘', 'Today') + '</div><div class="preview-post preview-arrival">' + avatar + '<div><b>Threadle <em>' + icon('check') + ' APP</em></b><article class="preview-state-card">' + starter_states(key,number,title,detail) + '</article><div class="preview-thread-card"><b>[' + topic + '] ' + title + '</b><span>' + t('메시지 2개', '2 messages') + ' ' + icon('right') + '</span></div></div></div></div>' + compose(t, channel) + '</div>'
        out += '<div class="preview-detail"><header>' + icon('thread') + '<span class="preview-header-name">[' + topic + '] ' + title + '</span>' + tools('muted','more','close') + '</header><div class="preview-detail-scroll"><h2>' + title + '</h2><p class="preview-started">' + t('Threadle이 시작함', 'Started by Threadle') + '</p><div class="preview-date">' + t('오늘', 'Today') + '</div>'
        for i, (author, heading, text) in enumerate([
            ('Threadle', topic + ' · ' + title, detail.replace('`', '')),
            ('Jae', topic + ' · ' + title, description),
            ('Min', topic + ' · ' + 'New comment', reply),
        ]):
            out += '<div class="preview-post preview-message message-' + str(i) + '">' + avatar + '<div><b>Threadle <em>' + icon('check') + ' APP</em></b><article' + (' class="preview-state-card"' if i==0 else ' class="preview-description-card"' if i==1 else '') + '><b>' + author + '</b>' + (starter_states(key,number,title,text) if i==0 else '<strong>'+heading+'</strong><p>'+text+'</p>') + '</article></div></div>'
        out += '</div>' + compose(t) + '</div></div>'
    out += '</div><p class="preview-disclaimer">' + t('작동 예시입니다. 실제 GitHub 소식은 연결한 저장소에서 전달됩니다.', 'Illustrative preview. Live updates come from your connected repositories.') + '</p></section>'
    return out
