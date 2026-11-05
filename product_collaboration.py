"""Public explanations of opt-in account linking and review conversations."""


def section(t):
    from product_experience import collaboration
    return collaboration(t)


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
