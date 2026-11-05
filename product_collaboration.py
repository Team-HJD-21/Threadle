"""Public explanations of opt-in account linking and review conversations."""


def section(t):
    out = '<section class="collaboration-section" id="collaboration" aria-labelledby="collaboration-title">'
    out += '<div class="section-heading"><p class="eyebrow">GITHUB ↔ DISCORD</p><h2 id="collaboration-title">' + t(
        '알림을 보고, 그 자리에서 답하세요.', 'See the update. Keep the conversation going.') + '</h2><p>' + t(
        'GitHub에서 시작한 대화를 Discord에서 이어가세요. 내 계정으로 답하고, 팀원을 멘션하고, 코드 리뷰의 해결 여부까지 확인합니다.',
        'Continue a GitHub conversation in Discord. Reply as yourself, mention your teammates, and follow code reviews through to resolution.') + '</p></div>'
    out += '<div class="collaboration-example" aria-label="' + t('Discord 댓글과 GitHub 등록 예시', 'Example of a Discord reply posted to GitHub') + '">'
    for platform, author, mention, label in (
        ('Discord', 'Min', '@Jae', t('PR #42 스레드에서 답장', 'Reply in the PR #42 thread')),
        ('GitHub', 'min-dev', '@jae-dev', t('본인 계정의 댓글로 등록', 'Posted from your own account')),
    ):
        out += '<article class="collaboration-sample"><div class="collaboration-sample-header"><span class="collaboration-platform">' + platform + '</span><span>' + label + '</span></div>'
        out += '<div class="collaboration-message"><span class="collaboration-avatar" aria-hidden="true">M</span><div><b>' + author + '</b><p><span class="collaboration-mention">' + mention + '</span> ' + t('오류 처리도 확인해 주세요.', 'Could you check the error handling too?') + '</p></div></div></article>'
    out += '</div><p class="collaboration-caption">' + t(
        '설명을 위한 예시입니다. 각자 계정을 연결하고 댓글 전송을 선택하면, 연결된 팀원의 실제 Discord 멘션도 GitHub 아이디로 바뀝니다.',
        'Illustrative example. Link your own account and opt into forwarding. Native Discord mentions of linked teammates become their GitHub usernames.') + '</p>'
    cards = [
        ('01', '내 GitHub 계정으로 남기는 댓글', 'Your comments. Your GitHub identity.',
         'PR·Issue·Discussion 스레드에 쓴 새 메시지를 내 GitHub 계정의 댓글로 전달합니다. 일반 알림에 답장하면 GitHub 원문을 인용합니다.',
         'New messages in PR, issue, and discussion threads become comments from your GitHub account. Reply to a notification to quote its GitHub original.'),
        ('02', '서로 다른 닉네임도, 같은 팀원', 'Different usernames. The same teammate.',
         '연결된 사람의 GitHub 멘션은 Discord로, 실제 Discord 멘션은 GitHub 아이디로 이어집니다. 서버에서 해당 채널을 볼 수 있는 사람에게만 멘션합니다.',
         'Linked mentions work both ways: GitHub to Discord, and native Discord mentions to GitHub usernames. Discord pings only reach members who can view that channel.'),
        ('03', '코드 리뷰 대화를 한 카드에서', 'One card for each code review conversation.',
         '파일·코드 리뷰 카드에 답장하면 해당 GitHub 리뷰 대화에 등록합니다. 답글은 같은 카드에 모이고, Resolve되면 빨강에서 초록으로 바뀝니다.',
         'Reply to a code or file review card to answer in that GitHub review conversation. Replies stay in the same card, changing from red to green when resolved.'),
    ]
    out += '<div class="collaboration-grid">'
    for number, ko, en, description_ko, description_en in cards:
        out += '<article class="collaboration-card"><span class="collaboration-number" aria-hidden="true">' + number + '</span><h3>' + t(ko, en) + '</h3><p>' + t(description_ko, description_en) + '</p></article>'
    out += '</div><div class="collaboration-actions"><a class="button" href="/connect/setup#account-setup">' + t('내 계정 연결 방법 보기', 'See how to link your account') + '</a><p>' + t(
        '서버 연결은 관리자가, 개인 계정 연결은 각 팀원이 직접 진행합니다. 댓글 전송은 선택 사항입니다.',
        'An admin connects the repository; each teammate links their own account. Comment forwarding is optional.') + '</p></div></section>'
    return out


def account_setup(t):
    out = '<section class="account-setup" id="account-setup" aria-labelledby="account-setup-title"><p class="eyebrow">/ACCOUNT CONNECT</p><h2 id="account-setup-title">' + t(
        '팀원은 각자 계정만 연결하세요.', 'Each teammate links their own account.') + '</h2><p>' + t(
        '저장소가 연결된 Discord 서버에서 진행하세요. 개인 계정 연결에는 서버 관리 권한이나 봇 재초대가 필요하지 않습니다.',
        'Start in a Discord server with a connected repository. Personal account linking needs neither Manage Server permission nor another bot invitation.') + '</p><ol>'
    for ko, en in [
        ('/account connect를 실행하고 나에게만 보이는 연결 링크를 엽니다.', 'Run /account connect and open your private connection link.'),
        ('명령어를 실행한 Discord 계정을 확인한 뒤, 본인 GitHub 계정으로 로그인합니다.', 'Verify the Discord account that ran the command, then sign in with your own GitHub account.'),
        ('이 서버에서 받을 멘션과 댓글 전송을 직접 선택합니다. 댓글 전송은 기본으로 꺼져 있습니다.', 'Choose mentions and comment forwarding for this server. Forwarding is off by default.'),
    ]:
        out += '<li>' + t(ko, en) + '</li>'
    out += '</ol><div class="account-command-list"><p><code>/account status</code> — ' + t('내 연결 상태와 선택한 기능 확인', 'View your connection and selected features') + '</p><p><code>/account disconnect</code> — ' + t(
        '모든 서버에서 내 계정 연결 해제', 'Disconnect your account across all servers') + '</p></div><p class="muted">' + t(
        'GitHub 댓글은 본인과 Threadle 앱 모두 접근할 수 있는 저장소에만 작성합니다. 댓글 전송을 사용하려면 서버 관리자의 앱 권한 설정도 완료되어 있어야 합니다.',
        'GitHub comments require repository access for both you and the Threadle App. The server administrator must also complete the App permission setup for forwarding.') + '</p></section>'
    return out
