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
        ('ci', 'CI', 'github-ci', '42', t('설정 화면 개선', 'Improve settings'), '`feature/settings` → `main`',
         t('integration-tests 검사에 실패했습니다.', 'The integration-tests check failed.'),
         t('결과를 확인했어요. 이 PR에서 수정할게요.', 'I checked the run. I’ll fix it in this PR.')),
    ]
    avatar = '<img src="/connect/assets/threadle-avatar.png" alt="" width="32" height="32">'
    out = '<section class="threadle-preview" aria-label="' + t('Discord 스레드 작동 예시', 'Discord thread preview') + '">'
    for i, (key, *_) in enumerate(cases):
        out += '<input class="sr-only" type="radio" name="threadle-preview" id="preview-' + key + '"' + (' checked' if i == 0 else '') + '>'
    out += '<div class="preview-tabs">' + ''.join('<label for="preview-' + k + '">' + label + '</label>' for k, label, *_ in cases) + '</div>'
    out += '<div class="preview-window"><div class="preview-traffic" aria-hidden="true"><i></i><i></i><i></i></div>'
    for key, label, channel, number, title, detail, description, reply in cases:
        topic = label + ' #' + number
        out += '<div class="preview-case preview-' + key + '"><aside class="preview-sidebar"><header>Threadle Test Server <span>⌄</span></header><div class="preview-sidebar-content"><p>Events</p><p>Server Boosts</p><small>' + t('텍스트 채널', 'TEXT CHANNELS') + '</small><p>#　' + t('일반', 'general') + '</p><small>Threadle</small>'
        for name, icon in [('github-issue','‼️'),('github-pr','🙏'),('github-discussions','🙋'),('github-ci','🔄'),('github-alert','🚨')]:
            out += '<div class="preview-channel' + (' is-selected' if name == channel else '') + '">#　' + icon + ' ' + name + '</div>'
        out += '</div><div class="preview-user">' + avatar + '<b>Team HJD</b><span>●</span></div></aside>'
        out += '<div class="preview-main"><header>#　' + channel + '</header><div class="preview-feed"><div class="preview-welcome"><div class="preview-hash">#</div><h2>' + t('환영합니다!', 'Welcome to #') + ' ' + channel + '!</h2><p>' + t('이 채널의 시작이에요.', 'This is the start of this channel.') + '</p></div><div class="preview-date">' + t('오늘', 'Today') + '</div><div class="preview-post preview-arrival">' + avatar + '<div><b>Threadle <em>✓ APP</em></b><article><strong>' + topic + ' · ' + title + '</strong><p>' + detail.replace('`', '') + '</p></article><div class="preview-thread-card"><b>[' + topic + '] ' + title + '</b><span>' + t('메시지 2개', '2 messages') + ' ›</span></div></div></div></div><div class="preview-compose">＋　' + t('메시지 보내기', 'Message') + ' #' + channel + '</div></div>'
        out += '<div class="preview-detail"><header>⑂　[' + topic + '] ' + title + '<span>⋯　×</span></header><div class="preview-detail-scroll"><h2>' + title + '</h2><p class="preview-started">' + t('Threadle이 시작함', 'Started by Threadle') + '</p><div class="preview-date">' + t('오늘', 'Today') + '</div>'
        for i, (author, heading, text) in enumerate([
            ('Threadle', topic + ' · ' + title, detail.replace('`', '')),
            ('Jae', topic + ' · ' + t('등록', 'Opened'), description),
            ('Min', topic + ' · ' + t('새 댓글', 'New comment'), reply),
        ]):
            out += '<div class="preview-post preview-message message-' + str(i) + '">' + avatar + '<div><b>Threadle <em>✓ APP</em></b><article><b>' + author + '</b><strong>' + heading + '</strong><p>' + text + '</p></article></div></div>'
        out += '</div><div class="preview-compose">＋　' + t('스레드에 답장', 'Reply in thread') + '</div></div></div>'
    out += '</div><p class="preview-disclaimer">' + t('작동 예시입니다. 실제 GitHub 소식은 연결한 저장소에서 전달됩니다.', 'Illustrative preview. Live updates come from your connected repositories.') + '</p></section>'
    return out
