from web_locale import LANGUAGE


def home(session, signed_in=False):
    from connect_web import esc, form

    def t(ko, en):
        return esc(en if LANGUAGE.get() == 'en' else ko).replace('&lt;br&gt;', '<br>')

    def start():
        return form('/connect/discord/start', session, '<button>' + t('Discord로 시작하기', 'Continue with Discord') + '</button>')

    navigation = ('<nav class="product-nav" aria-label="' + t('제품 메뉴', 'Product navigation') + '">'
                  '<a href="#features">' + t('기능', 'Features') + '</a><a href="#setup">' + t('연결 방법', 'Setup')
                  + '</a><a href="#plans">' + t('이용 안내', 'Plans') + '</a><a href="#faq">FAQ</a></nav>')
    body = navigation + '<section class="product-hero" aria-labelledby="hero-title"><div class="hero-copy">'
    body += '<p class="eyebrow">GITHUB → DISCORD</p>'
    body += '<h1 id="hero-title">' + t('GitHub 알림을', 'GitHub notifications.') + '<br><em>' + t('Discord에 정리하세요.', 'Organized in Discord.') + '</em></h1>'
    body += '<p class="hero-description">' + t('PR, 이슈, Discussion마다 스레드를 만들고 관련 댓글과 리뷰를 모아줍니다. 새 활동은 알림 채널에서 확인하세요.', 'Each PR, issue, and discussion gets a thread for its comments and reviews. New activity appears in your notification channel.') + '</p>'
    body += '<div class="hero-actions">' + start() + '<a class="text-link" href="#preview">' + t('작동 예시 보기', 'Explore the preview') + ' ↓</a></div>'
    body += '<p class="hero-note">' + t('베타 무료 이용 · 개인 GitHub 토큰 입력 없음', 'Free during beta · No personal GitHub token to paste') + '</p></div>'
    body += '<div class="demo-frame" id="preview"><input class="sr-only" type="radio" name="preview-kind" id="preview-pr" checked><input class="sr-only" type="radio" name="preview-kind" id="preview-issue"><input class="sr-only" type="radio" name="preview-kind" id="preview-ci">'
    body += '<div class="demo-top"><span><span class="demo-mark" aria-hidden="true">↳</span> launchpad</span><span class="demo-example">' + t('작동 예시', 'Illustrative preview') + '</span></div>'
    body += '<div class="demo-tabs" role="group" aria-label="' + t('알림 예시 선택', 'Choose a notification preview') + '"><label for="preview-pr">PR</label><label for="preview-issue">ISSUE</label><label for="preview-ci">CI</label></div><div class="demo-body">'
    demos = [
        ('pr', 'PR #42', 'feature/workspace-settings', t('작업 영역 설정 추가', 'Add workspace settings'),
         [('J', 'Jae', '09:41', t('PR을 열었습니다.', 'opened this pull request.')),
          ('M', 'Min', '09:48', t('리뷰 승인 · 설정 흐름 확인했어요.', 'Approved · The setup flow looks good.')),
          ('N', 'GitHub Notify', '09:48', t('새 리뷰도 같은 PR 스레드에 기록됩니다.', 'The new review stays in this PR’s thread.'))],
         t('PR #42에 새 리뷰가 도착했어요.', 'New review on PR #42.')),
        ('issue', 'ISSUE #18', 'bug / needs-triage', t('모바일 설정 화면 개선', 'Improve the mobile setup screen'),
         [('M', 'Min', '10:02', t('이슈를 등록했습니다.', 'opened this issue.')),
          ('J', 'Jae', '10:09', t('재현했어요. 수정 사항도 여기에 남길게요.', 'Reproduced it. I’ll post the fix here too.')),
          ('N', 'GitHub Notify', '10:09', t('등록부터 댓글까지, 하나의 이슈 스레드에.', 'The issue and its comments, together in one thread.'))],
         t('ISSUE #18에 새 댓글이 도착했어요.', 'New comment on ISSUE #18.')),
        ('ci', 'PR #42 · CI', 'checks / build', t('실패한 검사도 PR 맥락 안에서', 'Failed checks, with the PR context'),
         [('N', 'GitHub Notify', '10:14', t('CI 실패 · integration-tests', 'CI failed · integration-tests')),
          ('J', 'Jae', '10:18', t('실행 결과를 확인하고 수정할게요.', 'I’ll check the run and fix it.')),
          ('N', 'GitHub Notify', '10:18', t('관련 PR의 스레드에서 함께 확인하세요.', 'Follow up in the thread for the related PR.'))],
         t('PR #42의 CI 실패를 확인해 주세요.', 'A check failed on PR #42.')),
    ]
    for kind, number, branch, title, events, alert in demos:
        body += '<div class="demo-panel panel-' + kind + '"><div class="demo-topic"><span class="topic-tag">' + number + '</span><code>' + branch + '</code><h2>' + title + '</h2></div><div class="demo-events">'
        for avatar, name, when, message in events:
            body += '<div class="demo-event"><span class="demo-avatar avatar-' + avatar.lower() + '" aria-hidden="true">' + avatar + '</span><div><div class="demo-byline"><b>' + name + '</b><time>' + when + '</time></div><p>' + message + '</p></div></div>'
        body += '</div><div class="demo-alert"><div><span class="demo-channel"># github-alert</span><p>' + alert + '</p></div></div></div>'
    body += '</div><p class="demo-caption">' + t('예시 데이터입니다. 실제 알림은 연결한 저장소의 새 활동부터 전달됩니다.', 'Example data. Your connection delivers new activity from the repository you choose.') + '</p></div></section>'
    body += '<section class="product-section" id="features"><div class="section-heading"><h2>' + t('알림 설정', 'Make it work for your team') + '</h2><p>' + t('기본 알림에 필요한 옵션만 더하세요.', 'Start with the basics and add the options you need.') + '</p></div>'
    features = [
        ('01', t('하나의 항목, 하나의 스레드', 'One topic, one thread'), t('PR·이슈·Discussion의 변경, 댓글, 리뷰가 같은 스레드로 이어집니다. 알림 채널에서는 해당 스레드로 바로 이동할 수 있습니다.', 'Changes, comments, and reviews stay with the PR, issue, or discussion they belong to. Notification links take you straight to that thread.')),
        ('02', t('우리 팀에 맞는 작업 영역', 'Work areas that fit your team'), t('Frontend, Backend, 게임 플레이처럼 이름을 정하고 디렉터리를 연결하세요. 단일 영역 PR은 해당 채널로, 여러 영역의 PR은 공통 채널로 모입니다.', 'Name your areas and map them to directories. A PR within one area goes to its channel; cross-area PRs stay in the shared channel.')),
        ('03', t('묶음 알림과 방해금지 시간', 'Batching and Do Not Disturb hours'), t('일반 알림 묶기와 방해금지 시간을 설정하세요. 기존 스레드는 무음으로 기록하고, 새 스레드는 방해금지 종료 후 생성합니다. CI/CD 실패는 즉시 알려드립니다.', 'Batch notifications or set Do Not Disturb hours. Existing threads update silently; new threads are created after Do Not Disturb. CI/CD failures alert you immediately.')),
        ('04', t('리뷰와 열린 PR을 챙기는 요약', 'Keep an eye on reviews and open PRs'), t('설정 이후 도착한 리뷰 요청을 추적하고, 열린 PR 현황을 일일·주간 요약으로 받으세요. 필요한 기능만 선택할 수 있습니다.', 'Track new review requests after enabling reminders, and get a daily or weekly snapshot of open PRs. Turn on only what your team needs.')),
    ]
    body += '<div class="feature-grid">'
    for number, title, description in features:
        body += '<article class="feature-item"><h3>' + title + '</h3><p>' + description + '</p></article>'
    body += '</div></section>'
    body += '<section class="product-section setup-section" id="setup"><div class="section-heading"><h2>' + t('연결 방법', 'How to connect') + '</h2></div><ol class="setup-steps">'
    for title, description in [
        (t('Discord 서버 선택', 'Choose a Discord server'), t('Discord 계정을 확인하고 관리 권한이 있는 서버를 선택합니다. 봇이 없다면 초대 승인으로 이어집니다.', 'Sign in with Discord and select a server you manage. If the bot isn’t there yet, you’ll be asked to add it.')),
        (t('GitHub 저장소 승인', 'Grant repository access'), t('GitHub App의 접근을 승인하고 연결할 저장소를 선택합니다. 개인 토큰을 복사할 필요가 없습니다.', 'Grant the GitHub App access and pick a repository. There’s no personal access token to copy.')),
        (t('알림 구성 확인', 'Review your notification setup'), t('저장소 이름의 카테고리와 ISSUE·PR·Discussion·CI·알림 채널을 구성합니다. 작업 영역과 알림 옵션도 선택할 수 있습니다.', 'Review the repository category and ISSUE, PR, Discussion, CI, and alert channels. Add work areas and notification preferences if you need them.')),
    ]:
        body += '<li><h3>' + title + '</h3><p>' + description + '</p></li>'
    body += '</ol><div class="setup-action">' + start() + '<span>' + t('봇에서는 /connect로 시작할 수도 있어요.', 'You can also start with /connect in Discord.') + '</span></div></section>'
    body += '<section class="access-section product-section" aria-labelledby="access-title"><div><h2 id="access-title">' + t('권한과 연결 관리', 'Access and permissions') + '</h2></div><ul>'
    for ko, en in [
        ('GitHub App으로 저장소 접근을 승인합니다. 개인 GitHub 비밀번호나 토큰을 입력하지 않습니다.', 'Authorize repository access through the GitHub App. You never paste your GitHub password or a personal token.'),
        ('설정 과정에서 Discord 서버 관리 권한과 GitHub 저장소 관리 권한을 확인합니다.', 'Setup checks that you can manage the Discord server and administer the GitHub repository.'),
        ('디렉터리 탐색에는 Contents 읽기 권한이 필요합니다. 알림 내용은 연결한 채널을 볼 수 있는 구성원에게 공개됩니다.', 'Directory browsing requires Contents read permission. Notifications are visible to members who can access the connected channels.'),
        ('웹의 연결 관리에서 저장소 연결을 해제하거나 GitHub에서 앱 접근을 철회할 수 있습니다.', 'Disconnect a repository in connection settings, or revoke the app’s access on GitHub.'),
    ]:
        body += '<li>' + t(ko, en) + '</li>'
    body += '</ul></section>'
    body += '<section class="product-section" id="plans"><div class="section-heading"><h2>' + t('현재는 무료 베타로 운영합니다.', 'Beta access is free.') + '</h2><p>' + t('현재는 요금제별 제한과 결제를 적용하지 않습니다. Pro의 가격과 제공 범위는 출시 전에 안내할 예정입니다.', 'There are no paid tiers or plan limits during beta. Pro pricing and limits will be announced before paid plans launch.') + '</p></div><div class="plan-grid">'
    body += '<article class="plan-card"><span class="plan-label">' + t('현재 이용 가능', 'AVAILABLE NOW') + '</span><h3>' + t('무료 베타', 'Free beta') + '</h3><div class="plan-price">₩0</div><p>' + t('기본 알림부터 팀 알림 설정까지, 현재 제공하는 기능을 사용해 보세요.', 'Try the current notification and team settings features with your own repository.') + '</p><ul><li>' + t('PR·ISSUE·Discussion 스레드', 'PR, issue, and discussion threads') + '</li><li>' + t('작업 영역 분류·리뷰 대기 알림', 'Work areas and review reminders') + '</li><li>' + t('묶음·방해금지 시간·PR 현황 요약', 'Batching, Do Not Disturb hours, and PR digests') + '</li></ul>' + start() + '</article>'
    body += '<article class="plan-card future-plan"><span class="plan-label">' + t('준비 중', 'IN PLANNING') + '</span><h3>Pro</h3><div class="plan-price plan-soon">' + t('출시 전 안내', 'Details to come') + '</div><p>' + t('여러 프로젝트를 운영하는 팀을 위한 구독을 준비하고 있습니다.', 'We’re planning a subscription for teams managing several projects.') + '</p><ul><li>' + t('저장소 연결 한도와 제공 범위 안내 예정', 'Repository limits will be published before launch') + '</li><li>' + t('월간·연간 구독 검토 중', 'Monthly and annual options under consideration') + '</li><li>' + t('현재는 결제되지 않습니다.', 'No payments are being collected yet.') + '</li></ul><p class="plan-footnote">' + t('확정된 요금제는 이 페이지에서 안내합니다.', 'Confirmed plans will be announced on this page.') + '</p></article></div></section>'
    body += '<section class="product-section faq-section" id="faq"><div class="section-heading"><h2>' + t('자주 묻는 질문', 'Frequently asked questions') + '</h2></div><div class="faq-list">'
    questions = [
        ('비공개 저장소도 연결할 수 있나요?', 'Can I connect a private repository?', '앱에 접근을 허용한 저장소라면 연결할 수 있습니다. 조직 정책에 따라 관리자의 설치 승인이 필요할 수 있습니다.', 'Yes, if you grant the app access. An organization owner may need to approve the installation under your organization’s policies.'),
        ('기존 채널을 직접 지정해야 하나요?', 'Do I need to set up channels myself?', '저장소별 카테고리와 알림 채널을 자동으로 구성합니다. 같은 연결에서 봇이 이미 만든 채널은 재사용합니다.', 'We create a repository category and its notification channels. Channels already created by the bot for that connection are reused.'),
        ('방해금지 시간 동안 알림이 사라지나요?', 'What happens to updates during Do Not Disturb hours?', '새 항목도 바로 스레드를 만들고 메시지는 무음으로 전달합니다. Discord의 스레드 생성 알림음은 발생할 수 있습니다. 종료 후 변경된 항목을 요약하며 CI/CD 실패는 즉시 알려드립니다.', 'New topics create threads immediately and messages arrive silently. Discord may still play a thread creation sound. A catch-up roundup follows Do Not Disturb; CI/CD failures are alerted immediately.'),
        ('알림도 영어로 받을 수 있나요?', 'Can I receive notifications in English?', '한국어 또는 영어를 선택해 연결 설정을 저장하면 이후 봇이 만드는 안내에 적용됩니다. 사용자가 작성한 제목과 댓글은 번역하지 않습니다.', 'Save your connection settings in Korean or English to use that language for future bot-generated messages. Repository titles and comments are kept as written.'),
        ('과거 활동을 모두 가져오나요?', 'Will it import past activity?', '설치 이전의 활동을 전체 가져오지는 않습니다. 연결한 뒤 수신한 새 활동부터 알림을 전달합니다.', 'It doesn’t import the full history from before installation. Notifications start with new activity received after you connect.'),
        ('연결을 끝내려면 어떻게 하나요?', 'How do I disconnect?', '연결 관리에서 저장소 연결을 해제할 수 있습니다. GitHub에서 앱 접근을 철회할 수도 있으며 기존 Discord 채널과 스레드를 자동 삭제하지 않습니다.', 'Disconnect the repository in connection settings, or revoke access on GitHub. Existing Discord channels and threads are not automatically deleted.'),
    ]
    for ko, en, answer_ko, answer_en in questions:
        body += '<details><summary>' + t(ko, en) + '</summary><p>' + t(answer_ko, answer_en) + '</p></details>'
    body += '</div></section><section class="product-cta"><h2>' + t('저장소를 연결해 보세요.', 'Connect your repository') + '</h2><div class="hero-actions">' + start() + '<a class="text-link" href="https://teamhjd.com/privacy/index.html#email" target="_blank" rel="noopener noreferrer">' + t('문의하기', 'Get in touch') + '</a></div></section>'
    return body
