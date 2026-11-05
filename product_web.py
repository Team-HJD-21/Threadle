from web_locale import LANGUAGE

PUBLIC_PATHS = ('/', '/connect', '/connect/', '/connect/features', '/connect/setup', '/connect/pricing', '/connect/faq', '/connect/changelog', '/connect/terms', '/connect/privacy')


def t(ko, en):
    from connect_web import esc
    return esc(en if LANGUAGE.get() == 'en' else ko)


def navigation(active=''):
    links = [('/connect/features', '기능', 'Features'), ('/connect/setup', '연결 방법', 'Setup'), ('/connect/pricing', '요금제', 'Pricing'), ('/connect/faq', 'FAQ', 'FAQ'), ('/connect/changelog', '패치노트', 'Changelog')]
    return '<nav class="product-nav" aria-label="' + t('제품 메뉴', 'Product navigation') + '">' + ''.join('<a href="' + path + '"' + (' aria-current="page"' if active == path else '') + '>' + t(ko, en) + '</a>' for path, ko, en in links) + '</nav>'


def start(session, ko='무료로 시작하기', en='Start free'):
    from connect_web import form
    return form('/connect/discord/start', session, '<button>' + t(ko, en) + '</button>')


def demonstration():
    from demo_web import demonstration as render
    return render(t)


def extract(source, identity):
    pos = source.index(' id="' + identity + '"')
    begin = source.rfind('<section', 0, pos)
    end = source.index('</section>', pos) + len('</section>')
    return source[begin:end]


def pricing(session):
    from demo_web import icon as check_icon
    body = '<section class="pricing-page"><h1>' + t('팀에 맞는 요금제', 'A plan for your team') + '</h1><p class="page-intro">' + t('무료는 저장소 1개와 기본 알림, Pro는 저장소 10개와 모든 팀 기능을 제공합니다.', 'Free covers one repository and core notifications. Pro covers ten repositories and all team features.') + '</p><p class="beta-notice">' + t('유료 요금제 출시 준비 중입니다. 현재 베타에서는 결제와 요금제별 기능 제한을 적용하지 않습니다. 아래는 출시 예정 구성입니다.', 'Paid plans are being prepared. The beta has no payments or plan-based feature restrictions. The comparison below shows the planned launch offering.') + '</p><div class="plan-grid">'
    body += '<article class="plan-card"><h2>' + t('무료', 'Free') + '</h2><div class="plan-price">₩0</div><p>' + t('저장소 한 개의 개발 소식을 정리하세요.', 'Keep one repository’s updates organized.') + '</p><ul><li>' + t('Discord 서버당 저장소 1개', '1 repository per Discord server') + '</li><li>' + t('PR·Issue·Discussion 항목별 스레드', 'Per-item PR, issue, and discussion threads') + '</li><li>' + t('CI/CD 진행·결과와 실패 알림', 'CI/CD progress, results, and failure alerts') + '</li><li>' + t('카테고리·채널 자동 구성', 'Automatic category and channel setup') + '</li></ul>' + '<div class="plan-actions">' + start(session) + '<p class="plan-action-note muted">'+t('베타 기간에는 무료로 이용할 수 있습니다.','Free to use during beta.')+'</p></div></article>'
    body += '<article class="plan-card future-plan"><h2>Pro</h2><div class="plan-price plan-soon">' + t('가격 공개 예정', 'Pricing to be announced') + '</div><p>' + t('여러 프로젝트와 역할로 나뉜 팀을 위한 모든 기능.', 'All features for teams working across projects and roles.') + '</p><ul><li>' + t('Discord 서버당 저장소 10개', '10 repositories per Discord server') + '</li><li>' + t('디렉터리별 작업 영역 분류', 'Directory-based work areas') + '</li><li>' + t('리뷰 대기 알림·예약 및 명령어 요약', 'Review reminders, scheduled and on-demand summaries') + '</li><li>' + t('묶음 알림·방해금지 시간·간편 설정 명령어', 'Batching, Do Not Disturb hours, and settings commands') + '</li></ul><div class="plan-actions"><button disabled aria-disabled="true">' + t('Pro 결제 준비 중', 'Pro checkout coming soon') + '</button><p class="plan-action-note muted">' + t('결제 연결이 준비되면 이 페이지에서 안내합니다.', 'Checkout will appear here once it is ready.') + '</p></div></article></div>'
    body += '<p class="beta-collaboration-note">' + t('현재 무료 베타에서는 개인 계정 연결, 양방향 댓글·멘션, 코드 리뷰 대화 기능도 사용할 수 있습니다.', 'Personal account linking, two-way comments and mentions, and code review conversations are also available during the free beta.') + ' <a href="/connect/features#collaboration">' + t('협업 기능 보기', 'Explore collaboration features') + ' →</a></p>'
    rows = [('저장소 연결', 'Repository connections', '1', '10'), ('이벤트 DB·자동 백업 암호화', 'Event database and managed backup encryption', True, True), ('PR·Issue·Discussion 스레드', 'PR, issue, and discussion threads', True, True), ('CI/CD 진행·결과·실패 알림', 'CI/CD progress, results, and failure alerts', True, True), ('PR 충돌·해결 알림', 'PR conflict and resolution alerts', True, True), ('GitHub 설명·댓글 수정 및 삭제 반영', 'GitHub description and comment edits/deletions', True, True), ('시작 카드의 상태·세부 정보 갱신', 'Live status and details on topic cards', True, True), ('카테고리·채널 자동 생성', 'Automatic channel setup', True, True), ('한국어·영어 안내', 'Korean and English messages', True, True), ('디렉터리별 작업 영역', 'Directory-based work areas', False, True), ('리뷰 대기 알림', 'Review reminders', False, True), ('예약·명령어 요약', 'Scheduled and on-demand summaries', False, True), ('묶음 알림·방해금지 시간', 'Batching and Do Not Disturb hours', False, True), ('간편 설정 명령어', 'Settings commands', False, True)]
    body += '<h2 class="compare-title">' + t('기능 비교', 'Compare features') + '</h2><div class="comparison-scroll"><table class="comparison"><caption class="sr-only">' + t('무료 및 Pro 출시 예정 기능 비교', 'Planned Free and Pro feature comparison') + '</caption><thead><tr><th scope="col">' + t('기능', 'Feature') + '</th><th scope="col">Free</th><th scope="col">Pro</th></tr></thead><tbody>'
    for ko, en, free, pro in rows:
        body += '<tr><th scope="row">' + t(ko, en) + '</th>'
        for value in (free, pro):
            body += '<td>' + (('<span class="plan-included">' + check_icon('check') + '<span class="sr-only">' + t('포함', 'Included') + '</span></span>') if value is True else '—' if value is False else value) + '</td>'
        body += '</tr>'
    return body + '</tbody></table></div></section>'


def content(path, session):
    from landing_web import home as legacy
    titles = {'/connect/features': t('기능', 'Features'), '/connect/setup': t('연결 방법', 'Setup'), '/connect/pricing': t('요금제', 'Pricing'), '/connect/faq': 'FAQ', '/connect/changelog': t('패치노트', 'Changelog')}
    body = navigation(path)
    if path in ('/', '/connect', '/connect/'):
        body += '<section class="threadle-hero"><p class="eyebrow">GITHUB ↔ DISCORD</p><h1>' + t('하나의 PR, 하나의 스레드.', 'One PR. One thread.') + '</h1><p class="hero-description">' + t('댓글, 리뷰, 새 커밋, CI 결과를 같은 자리에 모으세요. 계정을 연결하면 Discord에서 내 GitHub 이름으로 답하고, 멘션과 코드 리뷰 대화도 이어갈 수 있어요.', 'Keep comments, reviews, commits, and CI results together. Link your account to reply from Discord as yourself, with connected mentions and code review conversations.') + '</p><div class="hero-actions">' + start(session) + '<a class="button secondary" href="/connect/pricing">' + t('Pro 살펴보기', 'Explore Pro') + '</a></div><a class="hero-collaboration-link" href="#collaboration">' + t('Discord에서 답장하는 모습 보기', 'See how replies work') + ' ↓</a></section>'
        from feature_showcase import security
        from product_collaboration import section as collaboration
        return 'TeamHJD - Threadle', body + demonstration() + collaboration(t) + security(t)
    if path in ('/connect/terms','/connect/privacy'):
        from legal_pages import content as legal
        title, legal_body = legal(path.rsplit('/',1)[-1],t)
        return title, body + legal_body
    if path == '/connect/changelog':
        from changelog import content as changelog
        return titles[path], body + changelog(t)
    if path == '/connect/pricing':
        return titles[path], body + pricing(session)
    source = legacy(session)
    if path == '/connect/features':
        from feature_showcase import showcase
        body += showcase(t)
    elif path == '/connect/setup':
        body += extract(source, 'setup')
        pos = source.index('<section class="access-section')
        body += source[pos:source.index('</section>', pos) + len('</section>')]
        body += '<section class="command-reference"><h2>' + t('Discord 명령어', 'Discord commands') + '</h2><p><code>/connect</code> — ' + t('저장소 연결 시작', 'Connect a repository') + '</p><p><code>/summary</code> — ' + t('연결한 모든 저장소의 열린 PR 전체와 가장 오래된 Issue·Discussion 제목 조회', 'See every open PR and the oldest issues and discussions across connected repositories') + '</p><p><code>/settings</code> — ' + t('현재 설정 조회. 옵션을 추가하면 알림 설정을 변경합니다.', 'View current settings, or pass options to change notification preferences.') + '</p><p class="muted">' + t('저장소 연결과 알림 설정 변경에는 Discord 서버 관리 권한이 필요합니다.', 'Connecting repositories and changing notification settings require Manage Server permission.') + '</p></section>'
        from product_collaboration import account_setup
        body += account_setup(t)
    else:
        body += extract(source, 'faq')
        body += '<section class="faq-support"><h2>'+t('더 궁금한 점이 있나요?','Still have questions?')+'</h2><p>'+t('Threadle Support 커뮤니티에서 질문하거나 개선 사항과 오류를 알려 주세요.','Ask questions, request improvements, or report issues in the Threadle Support community.')+'</p><a class="button" href="https://discord.gg/6faZbNBdv" target="_blank" rel="noopener noreferrer">'+t('Threadle Support 참여하기','Join Threadle Support')+'</a></section>'
    if path=='/connect/setup':body+='<p><code>/settings-history</code> — '+t('누가 알림 설정을 변경했는지 최근 기록을 확인합니다.','See who changed notification settings and their recent changes.')+'</p>'
    return titles[path], body
