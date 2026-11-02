from contextvars import ContextVar
import html
from html.parser import HTMLParser
from http.cookies import SimpleCookie, CookieError
import re
from urllib.parse import parse_qs, urlsplit

LANGUAGE = ContextVar('web_language', default='ko')
PAGE_PATH = ContextVar('web_page_path', default='/connect')
PUBLIC_ORIGIN = ContextVar('public_origin', default='https://threadle.teamhjd.com')
COOKIE = '__Host-ghd-language'

EN = {
    '이용약관': 'Terms of Service',
    '명령어 동기화:': 'Command sync:',
    'Discord 등록 확인됨': 'Discord registration verified',
    'Discord 명령어 등록 재시도 중': 'Retrying Discord command registration',
    '명령어 동기화 확인 대기': 'Command sync pending',

    'Threadle 업데이트': 'Threadle updates',
    '서명된 업데이트 ZIP을 올려 주세요. 인증 정보·연결·대기열을 보존하고, 적용 실패 시 이전 파일을 복구합니다. 적용 중 서비스가 잠깐 재시작됩니다.': 'Upload a signed update ZIP. Credentials, connections, and queues are preserved. If the update fails, previous files are restored. The service restarts briefly during the update.',
    '적용하려면 UPDATE 입력': 'Type UPDATE to apply',
    '이 버전 적용': 'Apply this version', '이 업데이트 취소': 'Cancel this update',
    '업데이트 ZIP': 'Update ZIP', '업로드 및 검사': 'Upload and verify',
    '업로드 후 버전을 확인하고 적용합니다. 재시작 중에는 이 화면에서 상태를 기다려 주세요.': 'Review the uploaded version before applying it. Keep this page open while the service restarts.',
    '서버:': 'Server:', '이름 확인 대기': 'Name pending', '확인할 수 없음': 'Unavailable',

    '현재 연결': 'Current connections',
    '연결 해제 기록': 'Disconnected connections',
    '처음 50건을 표시합니다. 검색으로 범위를 좁혀 주세요.': 'Showing the first 50. Search to narrow the results.',
    '열린 PR 반복 알림': 'Recurring open PR reminders',
    '4시간마다': 'Every 4 hours', '12시간마다': 'Every 12 hours', '24시간마다': 'Every 24 hours', '48시간마다': 'Every 48 hours',
    '담당자나 리뷰 요청이 없어도 초안을 포함한 열린 PR을 설정한 간격으로 다시 알립니다. 종료되면 중단하며, 조용한 시간에는 보내지 않습니다.': 'Get recurring reminders for all open PRs, including drafts, even without an assignee or requested reviewer. Reminders stop when the PR closes and wait until quiet hours end.',
    'PR · Issue 현황 요약': 'PR and issue roundup',

    'GitHub 활동을 Discord 스레드로 정리하고 팀에 맞게 알림을 받아보세요.': 'Keep GitHub updates organized in Discord threads, with notifications that fit your team.',
    '알림 언어': 'Notification language',
    '설정을 적용하면 현재 화면 언어로 이후 봇 안내를 보냅니다. 제목과 댓글 원문은 번역하지 않습니다.': 'Apply these settings to use this language for future bot messages. Titles and comments stay in their original language.',
    '개발 소식을 한곳에.': 'Keep your team in the loop.',
    '저장소 활동을 항목별 스레드로 모으고, 새 소식은 알림 채널로 받아보세요.': 'Keep GitHub activity organized in threads and get new updates in your Discord notification channel.',
    '1 · Discord 서버 선택　2 · 저장소 승인　3 · 알림 채널 자동 설정': '1 · Choose a server　2 · Connect GitHub　3 · Set up notifications',
    '관리 권한이 있는 Discord 서버와 GitHub 저장소를 연결할 수 있습니다.': 'Connect a Discord server you manage to a GitHub repository where you have admin access.',
    'Discord로 시작하기': 'Continue with Discord',
    'Discord 계정 확인하고 계속하기': 'Verify your Discord account',
    'Discord에서 선택한 서버에 GitHub 저장소를 연결합니다. 명령을 실행한 계정인지 확인한 뒤 계속합니다.': 'Connect GitHub to the server you selected in Discord. First, verify that you are signed in with the account that ran the command.',
    'Discord 서버 선택': 'Choose a Discord server',
    '관리 권한이 있는 서버만 표시합니다.': 'Only servers you own or have permission to manage are shown.',
    '알림을 받을 서버': 'Discord server',
    '이 서버로 계속하기': 'Continue with this server',
    '기존 연결 관리': 'Manage existing connections',
    '관리할 수 있는 서버가 없습니다. 서버 소유자 또는 서버 관리 권한이 필요합니다.': 'No eligible servers found. You need to own the server or have Manage Server permission.',
    '서버 연결 관리': 'Manage connections',
    '에 알림을 연결합니다.': ' will receive your GitHub updates.',
    'GitHub 계정을 연결하고 알림을 받을 저장소를 선택해 주세요.': 'Connect your GitHub account, then choose a repository.',
    '서버에 봇 초대하기': 'Add the bot to this server',
    '봇 초대 또는 권한 다시 설정': 'Add the bot or update its permissions',
    'GitHub 계정 연결하기': 'Connect GitHub',
    '이 저장소 연결 해제': 'Disconnect this repository',
    '기존 운영 연결 · 이전은 운영자에게 문의해 주세요.': 'This connection is managed by the service operator. Contact support if you need to change it.',
    '다른 서버 선택': 'Choose another server',
    '로그아웃': 'Sign out',
    'GitHub 저장소 선택': 'Choose a GitHub repository',
    '앱 접근이 승인됐고, 내 계정에 관리 권한이 있는 저장소만 표시합니다.': 'Only repositories available to this app and where you have admin access are shown.',
    '앱 설치 / 저장소 접근 설정': 'Install the app or manage access',
    '앱 설치 / 저장소 접근 설정 (새 탭)': 'Install the GitHub App or manage repository access (opens in a new tab)',
    '목록 새로고침': 'Refresh repositories',
    'GitHub 설정은 새 탭에서 열립니다. 설정을 마친 뒤 이 탭에서 목록을 새로고침해 주세요.': 'GitHub settings open in a new tab. When you are done, return here and refresh the repository list.',
    '연결할 저장소': 'GitHub repository',
    '자동 연결 설정 확인하기': 'Review notification setup',
    '연결할 저장소가 없습니다. 앱을 설치하고 접근할 저장소를 선택해 주세요. 조직 승인 대기 중이면 관리자의 승인이 필요합니다.': 'No repositories available yet. Install the app and grant access to the repositories you want to connect. An organization owner may need to approve the installation.',
    '알림 채널 자동 설정': 'Set up notifications',
    '이미 연결된 저장소입니다. 기존 알림 채널을 그대로 사용합니다.': 'This repository is already connected. Its current notification channels will stay in use.',
    '기존 연결 확인하기': 'Continue with this connection',
    '카테고리와 알림 채널을 자동 생성하려면 봇의 채널 관리 권한이 필요합니다.': 'The bot needs Manage Channels permission to create your notification category and channels.',
    '채널 생성 권한 승인하기': 'Grant channel permissions',
    '아래 카테고리와 채널을 자동으로 생성하고 연결합니다. 이 봇이 이미 만든 채널이 있으면 재사용합니다.': 'We will create the category and channels below and connect your repository. Channels already created for this connection will be reused.',
    'CI 워크플로 이름 필터 (선택)': 'CI workflow names (optional)',
    '비워두면 모든 워크플로, 여러 개는 쉼표로 구분': 'Leave blank for all workflows, or separate names with commas',
    '새 카테고리는 서버의 기본 역할 권한을 따릅니다. 비공개 저장소의 알림도 이 채널을 볼 수 있는 서버 구성원에게 공개됩니다.': 'The new category uses your server\'s default role permissions. Anyone who can view these channels will be able to read the notifications, including updates from private repositories.',
    '카테고리·채널 자동 생성하고 연결하기': 'Create channels and connect',
    '저장소 다시 선택': 'Choose another repository',
    '연결이 완료됐어요.': 'You are connected!',
    '의 새 활동을': ' will send new activity to ',
    '으로 전달할 준비가 됐습니다.': '.',
    '이제 GitHub에서 이슈를 만들거나 댓글을 남겨 스레드와 안내 채널의 알림을 확인해 주세요.': 'Create an issue or leave a comment on GitHub to check that updates arrive in the right thread and notification channel.',
    '설치 이전 활동은 자동으로 가져오지 않습니다. 봇과 앱의 접근 권한을 유지해 주세요.': 'Only new activity will be delivered. Keep the Discord bot and GitHub App installed with the required permissions.',
    'Discord 알림 채널 열기': 'Open notifications in Discord',
    '연결 관리': 'Manage connections',
    '연결 관리로 돌아가기': 'Back to connections',
    '현재 단계에서 다시 시도': 'Try this step again',
    '처음부터 다시 연결': 'Start over',
    '다시 연결하기': 'Start again',
    '연결 페이지': 'Connection setup',
    '연결을 확인해 주세요.': 'We could not complete this step.',
    '외부 연결을 확인해 주세요.': 'Check your connection permissions',
    '요청을 확인해 주세요.': 'Something needs your attention.',
    '잠시 후 다시 시도해 주세요.': 'Please try again shortly.',
    'Discord 또는 GitHub 권한을 확인해 주세요. 일시적인 장애라면 잠시 후 다시 시도할 수 있습니다.': 'Check the permissions granted to Discord and GitHub. If either service is temporarily unavailable, try again shortly.',
    '입력이 올바르지 않거나 연결 정보가 변경됐습니다.': 'Some details are invalid or have changed. Please review your connection setup.',
    '지금은 연결을 완료하지 못했습니다. 기존 알림 연결은 유지됩니다.': 'We cannot finish setting up this connection right now. Your existing notification connections are still in place.',
    '저장소의 알림 내용은 연결한 Discord 알림 채널을 볼 수 있는 구성원에게 공개됩니다. 개인 GitHub 토큰을 입력할 필요가 없습니다. 로그인 세션은 30분 후 만료되며, 완료한 알림 연결은 유지됩니다.': 'Notifications are visible to anyone who can access the connected Discord channels. No personal GitHub token is needed. Your sign-in session lasts 30 minutes; completed connections stay active.',
    'TeamHJD GitHub Notify 홈': 'TeamHJD GitHub Notify home',
    '하단 메뉴': 'Footer navigation',
    '관련 사이트': 'Related sites',
    'TeamHJD 메인': 'TeamHJD',
    '상호': 'Business name',
    '대표자': 'Representative',
    '황재동': 'Jaedong Hwang',
    '사업자등록번호': 'Business registration number',
    '(사업자정보확인)': '(Business information)',
    '통신판매업 신고번호': 'Mail-order business registration',
    '사업장 주소': 'Business address',
    '프레스킷': 'Press kit',
    '개인정보처리방침': 'Privacy policy',
    '콘텐츠 이용 · AI 학습 정책': 'Content use & AI training policy',
    'AI 검색·정보 조회는 허용하며, 콘텐츠의 AI 학습 이용은 금지합니다.': 'AI search and information retrieval are permitted. Use of our content for AI training is prohibited.',
    '먼저 이 서버에 봇을 초대하고 채널 접근 권한을 허용해 주세요.': 'Add the bot to this server and grant it access to the notification channels to continue.',
    '페이지를 찾을 수 없습니다.': 'This page could not be found.',
    '파일을 찾을 수 없습니다.': 'This file could not be found.',
    '요청을 찾을 수 없습니다.': 'This action is not available.',
    '로그인이 필요하거나 세션이 만료됐습니다. 다시 연결해 주세요.': 'Please sign in to continue. Your previous session may have expired.',
    '로그인이 만료됐습니다. 다시 연결해 주세요.': 'Your sign-in session has expired. Please sign in again.',
    '이 Discord 서버를 관리할 권한이 없습니다.': 'You do not have permission to manage this Discord server.',
    '봇 설정이 일치하지 않습니다. 운영자에게 문의해 주세요.': 'The bot configuration could not be verified. Please contact support.',
    '봇 설정을 확인할 수 없습니다. 운영자에게 문의해 주세요.': 'The bot configuration could not be verified. Please contact support.',
    '연결 세션이 만료됐습니다. 처음부터 다시 진행해 주세요.': 'Your setup session has expired. Please start again.',
    '로그인이 취소됐습니다. 원할 때 다시 연결할 수 있습니다.': 'Sign-in was cancelled. You can start again whenever you are ready.',
    '인증 서버 정보가 일치하지 않습니다.': 'The sign-in provider could not be verified. Please start again.',
    '로그인 정보를 확인하지 못했습니다.': 'We could not verify your sign-in. Please try again.',
    '사용자 정보를 확인하지 못했습니다.': 'We could not verify your account. Please try again.',
    'Discord에서 /connect 명령을 실행한 계정으로 진행해 주세요. 링크가 만료됐다면 명령을 다시 실행해 주세요.': 'Use the Discord account that started this connection. If the link has expired, run /connect again in Discord.',
    '이 연결 버튼은 이미 사용했거나 만료됐습니다. Discord에서 /connect 명령을 다시 실행해 주세요.': 'This setup link has expired or has already been used. Run /connect again in Discord for a new link.',
    '저장소를 먼저 선택해 주세요.': 'Choose a repository before continuing.',
    '저장소를 선택해 주세요.': 'Choose a repository to continue.',
    '연결 확인 정보가 만료됐습니다. 페이지를 다시 열어 주세요.': 'This page has expired. Open the setup page again to continue.',
    '요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.': 'Too many requests. Please wait a moment before trying again.',
    '로그인 확인 정보가 일치하지 않거나 만료됐습니다. 다시 시작해 주세요.': 'This sign-in attempt has expired or could not be verified. Please start again.',
    '계정 확인과 서버 목록 접근에 동의해야 연결할 수 있습니다.': 'Allow access to your account identity and server list to continue.',
    '저장소 접근이 변경됐습니다. 저장소를 다시 선택해 주세요.': 'Repository access has changed. Please choose the repository again.',
    '이 저장소를 연결할 권한이 없습니다.': 'You do not have permission to connect this repository.',
    '알림 채널은 서버에서 자동으로 설정합니다. 페이지를 다시 열어 주세요.': 'Notification channels are configured automatically. Reopen the setup page to continue.',
    '저장소 접근 권한이 변경됐습니다. 다시 확인해 주세요.': 'Your repository permissions have changed. Please review access and try again.',
    'CI 필터는 이름당 100자, 최대 20개까지 입력할 수 있습니다.': 'Enter up to 20 workflow names, with no more than 100 characters per name.',
    '앱이 이 저장소에 접근할 수 없습니다.': 'The GitHub App does not have access to this repository.',
    '기존 연결이 있습니다. 연결 관리에서 확인해 주세요.': 'This repository already has a connection. Review it under Manage connections.',
    '이미 연결된 저장소입니다. 기존 연결을 해제한 뒤 다시 연결해 주세요.': 'This repository is already connected with different settings. Disconnect it before setting it up again.',
    '기존 운영 연결이 있습니다. 운영자에게 이전을 요청해 주세요.': 'This repository has an operator-managed connection. Contact support to migrate it.',
    '관리할 수 있는 연결이 아닙니다.': 'You do not have permission to manage this connection.',
    '알림 채널의 접근·전송 권한이 부족합니다. 서버의 채널 권한을 확인한 뒤 다시 시도해 주세요.': 'The bot cannot access or post to one of the notification channels. Check its channel permissions and try again.',
    '올바른 서비스 주소로 접속해 주세요.': 'Please open this page using the official site address.',
    '지원하지 않는 요청입니다.': 'This request method is not supported.',
    '다른 사이트에서 보낸 요청은 처리할 수 없습니다.': 'We could not verify where this request came from. Reopen the setup page on this site.',
    '입력 형식을 확인해 주세요.': 'This form could not be read. Please reopen the page and try again.',
    '입력 크기를 확인해 주세요.': 'The submitted form is too large or incomplete.',
    '요청을 끝까지 받지 못했습니다.': 'The request was interrupted. Please try again.',
    '중복된 입력 항목이 있습니다.': 'This form contains duplicate fields. Reopen the page and try again.',
    '요청이 올바르지 않습니다.': 'This request is not valid. Please try again from the setup page.',
    '서버 목록을 끝까지 확인하지 못했습니다. 다시 시도해 주세요.': 'We could not load your complete server list. Please try again.',
    '저장소 목록을 끝까지 확인하지 못했습니다. 앱의 저장소 접근 범위를 줄여 주세요.': 'We could not load the complete repository list. Try giving the app access to fewer repositories.',
    '응답이 너무 큽니다. 저장소 범위를 줄여 다시 시도해 주세요.': 'The provider returned too much data. Try giving the app access to fewer repositories.',
    '다른 연결 요청이 진행 중입니다. 잠시 후 다시 확인해 주세요.': 'Another setup request is in progress. Please check again shortly.',
    'Discord 채널 생성 결과를 아직 확인할 수 없습니다. 중복 생성을 막기 위해 다시 만들지 않고 있습니다. 잠시 후 다시 확인해 주세요.': 'We are still checking whether Discord created the channels. To avoid duplicates, we will not create them again. Please check back shortly.',
    '알림 채널 자동 생성을 위해 봇의 채널 관리 권한을 승인해 주세요.': 'Grant the bot Manage Channels permission so it can create your notification channels.',
    '이 서버의 알림 채널을 생성하고 있습니다. 잠시 후 현재 단계에서 다시 확인해 주세요.': 'Notification channels are being created for this connection. Please check this step again shortly.',
    'Discord의 채널 생성 대기 시간이 남아 있습니다. 잠시 후 다시 시도해 주세요.': 'Discord has temporarily limited channel creation. Please wait before trying again.',
    '기존 GitHub 카테고리의 서버나 종류가 변경됐습니다. 채널 설정을 확인해 주세요.': 'The saved GitHub category no longer matches this server. Please review your channel settings.',
    'Discord에서 채널을 만들지 못했습니다. 봇의 채널 관리 권한과 서버 채널 수를 확인한 뒤 다시 시도해 주세요.': 'Discord could not create the channels. Check the bot\'s Manage Channels permission and your server\'s channel limit, then try again.',
    'Discord에 채널 생성 요청을 보내지 못했습니다. 현재 단계에서 다시 시도해 주세요.': 'We could not send the channel creation request to Discord. Please try this step again.',
    '기존 알림 채널이 다른 카테고리로 이동됐습니다. 채널 설정을 확인해 주세요.': 'An existing notification channel has been moved to a different category. Please review your channel settings.',
}


EN.update({
    '팀 알림 설정': 'Team notifications',
    '저장소 관리 권한을 확인하려면 GitHub 계정을 연결해 주세요.': 'Connect GitHub to verify that you can manage this repository.',
    'PR 스레드 분류': 'Organize PR threads',
    '나누지 않기': 'Keep one PR channel',
    '디렉터리별로 나누기': 'Create channels by directory',
    '분류 방식 선택': 'Choose how to organize PRs',
    '작업 영역': 'Work areas',
    '단일 영역 PR은 해당 채널에, 여러 영역 또는 미분류 파일이 포함된 PR은 공통 채널에 스레드를 만듭니다. 생성된 스레드는 이동하지 않습니다.': 'PRs that belong to one work area get a thread in that area’s channel. PRs spanning multiple areas or unassigned files use the shared PR channel. Existing threads stay where they were created.',
    '영역 제거': 'Remove area',
    '디렉터리 선택': 'Choose directories',
    '상위 디렉터리': 'Parent directory',
    '루트의 파일만': 'Files at the repository root only',
    '하위 디렉터리 보기': 'Browse subdirectories',
    '선택에 추가': 'Add to selection',
    '항목이 많아 앞의 60개를 표시합니다. 아래 경로 입력으로 목록에 있는 디렉터리를 선택할 수 있습니다.': 'Showing the first 60 entries. You can also enter the path of a directory in this list below.',
    '디렉터리 경로': 'Directory path',
    '디렉터리를 읽을 수 없습니다. GitHub App의 Contents 읽기 권한과 저장소 접근 승인을 확인해 주세요.': 'We could not load these directories. Check that the GitHub App has Contents read permission and access to this repository.',
    '이미 설치한 계정이나 조직은 추가 권한 업데이트를 승인해야 합니다. 승인 후 GitHub 로그인을 새로 해 주세요.': 'Existing installations need approval for the additional permission. After approving it for your account or organization, sign in with GitHub again.',
    'GitHub 앱 권한 확인': 'Review GitHub App access',
    '선택한 디렉터리': 'Selected directories',
    '이 영역에 넣을 폴더를 체크하고 영역 추가를 누르세요. 선택한 폴더의 하위 디렉터리도 포함됩니다.': 'Select the folders for this area, then choose Add area. Subdirectories of each selected folder are included.',
    '선택 저장 후 계속 탐색': 'Save selection and keep browsing',
    '현재 디렉터리': 'Current directory',
    '현재 폴더 전체': 'This folder and its subdirectories',
    '폴더를 체크하면 하위 폴더도 포함됩니다. 화살표를 눌러 더 안쪽으로 이동할 수 있습니다.': 'Selecting a folder includes its subdirectories. Use the arrows to browse inside.',
    '선택 해제': 'Remove selection',
    '선택 저장': 'Save selection',
    'PR 번호로 보낼 채널과 분류 이유를 확인하세요.': 'Enter a PR number to see where its thread will go and why.',
    '예: 123': 'e.g. 123',
    '예상 스레드 위치': 'Thread destination',
    '검사한 경로': 'Paths checked',
    '미분류': 'Unassigned',
    '분류에 사용한 경로 보기': 'Show paths used for classification',
    '경로가 많아 앞의 100개만 표시합니다.': 'Showing the first 100 paths.',
    '작업 영역을 추가하면 디렉터리 기준으로 분류할 수 있습니다.': 'Add work areas to organize PRs by directory.',
    '변경 파일이 많아 전체 경로를 확인할 수 없습니다. 공통 채널을 사용합니다.': 'There are too many changed files to check every path. This PR will use the shared channel.',
    '분류할 변경 경로가 없어 공통 채널을 사용합니다.': 'There are no changed paths to classify, so this PR will use the shared channel.',
    '설정한 작업 영역에 해당하는 경로가 없어 공통 채널을 사용합니다.': 'None of the paths match your work areas, so this PR will use the shared channel.',
    '어느 작업 영역에도 속하지 않은 경로가 포함되어 공통 채널을 사용합니다.': 'Some paths do not belong to any work area, so this PR will use the shared channel.',
    '두 개 이상의 작업 영역을 함께 수정한 PR이므로 공통 채널을 사용합니다.': 'This PR changes two or more work areas, so its thread will go in the shared channel.',
    '모든 변경 경로가 하나의 작업 영역에 속합니다.': 'All changed paths belong to one work area.',
    '분류 결과를 다시 확인해 주세요.': 'Please run the preview again.',
    '설정이 적용됐어요.': 'Your settings have been saved.',
    '영역 이름만으로는 분류할 수 없습니다. 이 영역에 넣을 실제 폴더를 하나 이상 체크해 주세요.': 'An area name alone does not tell us which files belong here. Select at least one folder for this area.',
    '영역 이름': 'Area name',
    '영역 추가': 'Add area',
    '디렉터리 선택 비우기': 'Clear directory selection',
    'PR로 분류 미리보기': 'Try the rules on a PR',
    'PR 번호': 'PR number',
    '분류 확인': 'Preview destination',
    '공통 PR 채널': 'Shared PR channel',
    '변경 파일이 많아 공통 채널을 사용합니다.': 'This PR has too many changed files to classify completely. It will use the shared channel.',
    '추가 알림': 'Notification preferences',
    '선택한 기능만 켭니다. 상세 스레드의 활동은 바로 기록됩니다.': 'Turn on only the features you need. Detailed activity is still posted to its thread right away.',
    '리뷰 대기 알림': 'Review reminders',
    '사용 안 함': 'Off',
    '요청 후 4시간': '4 hours after a review request',
    '요청 후 12시간': '12 hours after a review request',
    '요청 후 24시간': '24 hours after a review request',
    '요청 후 48시간': '48 hours after a review request',
    '설정 후 받은 리뷰 요청부터 추적합니다. 초안·종료된 PR은 제외하며, 응답 없는 요청마다 한 번 알립니다.': 'Tracks new review requests received after you enable this feature. Each unanswered request gets one reminder. Draft and closed PRs are excluded.',
    '일반 알림 묶기': 'Group routine notifications',
    '즉시 전달': 'Send right away',
    '1분마다': 'Every minute',
    '5분마다': 'Every 5 minutes',
    '종료·병합·CI 실패·변경 요청은 묶지 않습니다. 조용한 시간에는 함께 대기합니다.': 'Closures, merges, failed CI runs and requests for changes are sent individually. Quiet hours still apply.',
    '열린 PR 현황 요약': 'Open PR digest',
    '매일': 'Daily',
    '매주': 'Weekly',
    '요약 시각': 'Digest time',
    '주간 요약 요일': 'Weekly digest day',
    '월요일': 'Monday', '화요일': 'Tuesday', '수요일': 'Wednesday', '목요일': 'Thursday',
    '금요일': 'Friday', '토요일': 'Saturday', '일요일': 'Sunday',
    '시간대 (UTC 기준)': 'UTC offset',
    '고정 UTC 시차입니다. 서머타임은 자동 변경하지 않습니다.': 'This is a fixed UTC offset. Daylight saving changes are not applied automatically.',
    '조용한 시간 사용': 'Enable quiet hours',
    '조용한 시간 시작': 'Quiet hours start',
    '조용한 시간 종료': 'Quiet hours end',
    '알림 옵션 선택': 'Choose notification preferences',
    '설정 적용': 'Apply settings',
    '선택한 옵션은 아래 버튼을 눌러야 실제 연결에 반영됩니다. 기존 PR 스레드와 대기 중인 알림은 유지됩니다.': 'Your choices take effect when you apply them below. Existing PR threads and queued notifications are preserved.',
    '채널 생성 및 설정 적용': 'Create channels and apply settings',
    '기본 연결 설정으로 돌아가기': 'Back to basic setup',
    '알림을 더 세밀하게 설정할까요?': 'Want more control over notifications?',
    '기본 연결을 그대로 사용하거나, 디렉터리별 PR 채널과 리뷰 대기·묶음·요약 알림을 선택할 수 있습니다.': 'Keep the default setup, or choose directory-based PR channels, review reminders, grouped notifications and digests.',
    '작업 영역은 최대 8개까지 만들 수 있습니다.': 'You can create up to 8 work areas.',
    '작업 영역 설정을 다시 확인해 주세요.': 'Please review your work area settings.',
    '작업 영역 이름은 1~32자로 입력해 주세요.': 'Area names must be between 1 and 32 characters.',
    '작업 영역 이름이 중복됩니다.': 'Each work area needs a different name.',
    '각 작업 영역에 디렉터리를 1~30개 선택해 주세요.': 'Select between 1 and 30 directories for each area.',
    '디렉터리 경로가 올바르지 않습니다.': 'This directory path is not valid.',
    '작업 영역 채널을 확인해 주세요.': 'Please check the work area channel.',
    '목록에서 디렉터리를 선택해 주세요.': 'Choose a directory from the repository list.',
    '디렉터리 목록이 너무 큽니다. 더 작은 저장소 범위로 연결해 주세요.': 'This directory list is too large to load completely.',
    '알림 설정을 확인해 주세요.': 'Please review your notification settings.',
    '알림 시간 설정이 올바르지 않습니다.': 'Please check the notification times.',
    '알림 주기를 확인해 주세요.': 'Please check the notification frequency.',
    '조용한 시간 설정을 확인해 주세요.': 'Please check your quiet hours.',
    '조용한 시간의 시작과 종료를 다르게 설정해 주세요.': 'Quiet hours must start and end at different times.',
    '이 연결을 설정할 권한이 없습니다.': 'You do not have permission to configure this connection.',
    '분류 방식을 선택해 주세요.': 'Choose how to organize PRs.',
    '채널 이름이 중복됩니다. 다른 영역 이름을 입력해 주세요.': 'That channel name is already in use. Choose another area name.',
    'PR 번호를 확인해 주세요.': 'Please check the PR number.',
    '작업 영역을 하나 이상 추가해 주세요.': 'Add at least one work area.',
    '팀 알림 설정을 다시 확인해 주세요.': 'Please review your team notification settings.',
    '연결 정보가 변경됐습니다. 다시 확인해 주세요.': 'This connection has changed. Please reload it and try again.',
})


EN.update({'운영 관리자': 'Administration', 'Threadle 운영 관리': 'Threadle administration', '연결된 서버': 'Connected servers', '연결된 저장소': 'Connected repositories', '일시중지': 'Paused', '재개': 'Resume', '연결 해제': 'Disconnected', '동작 중': 'Running', 'GitHub 설치 중지': 'GitHub installation suspended', '저장소 또는 서버 ID 검색': 'Search by repository or Discord server ID', '검색': 'Search', '전체 보기': 'Show all', '일시중지 중에도 새 웹후크는 대기열에 보관합니다. 재개하면 순서대로 처리합니다. 이미 전송 중인 요청 하나는 완료될 수 있습니다.': 'New webhooks are queued while paused and processed when resumed. A request already being sent may still complete.', '검색 결과 중 처음 50건을 표시합니다. 저장소 이름 또는 서버 ID로 범위를 좁혀 주세요.': 'Showing the first 50 matches. Narrow your search by repository or server ID.', '대기 이벤트': 'Queued events', '· 재시도 중': '· Retrying', '· 예약/묶음 알림': '· Scheduled or batched alerts', '대기열 상태를 읽지 못했습니다.': 'Queue status is temporarily unavailable.', '연결 ID': 'Connection ID', '표시할 연결이 없습니다.': 'No connections found.', '최근 관리자 작업': 'Recent administrator actions', '관리자 인증이 필요합니다.': 'Administrator authentication is required.', '올바른 관리 요청이 아닙니다.': 'This administrative request is invalid.', '연결 상태가 변경됐습니다. 새로고침 후 다시 확인해 주세요.': 'The connection has changed. Refresh the page and review it again.', '현재 상태에서 실행할 수 없습니다.': 'This action is not available in the current state.', '요청을 확인할 수 없습니다.': 'This request could not be verified.', '중복 입력은 허용하지 않습니다.': 'Duplicate form fields are not allowed.', '관리자 화면을 새로고침 후 다시 시도해 주세요.': 'Refresh the administration page and try again.', '연결 상태가 변경됐습니다. 새로고침해 주세요.': 'The connection has changed. Please refresh the page.', '확인 입력이 저장소 이름과 다릅니다.': 'The confirmation does not match the repository name.', '알림 연결만 해제합니다. Discord 채널·스레드와 기록은 삭제하지 않습니다. 사용자는 나중에 다시 연결할 수 있습니다.': 'This disconnects notifications only. Discord channels, threads, and saved history remain. The user can reconnect later.', '대기열과 기존 설정을 보존합니다.': 'The queue and existing settings are preserved.', '확인하려면 저장소 이름을 입력해 주세요:': 'Type the repository name to confirm:', '관리 작업 확인': 'Confirm administrative action'})

def text(value):
    if LANGUAGE.get() != 'en':
        return value
    stripped = value.strip()
    if stripped in EN:
        return value[:len(value)-len(value.lstrip())] + EN[stripped] + value[len(value.rstrip()):]
    patterns = [
        (r'^Discord 서버 ID (\d+) · (app|legacy)$', lambda m: f'Discord server ID {m[1]} · {m[2]}'),
        (r'^분배 대기 웹후크 (\d+) · 디스크 여유 ([0-9.]+) GB · 조회 시각 (.+)$', lambda m: f'Undispatched webhooks {m[1]} · Free disk space {m[2]} GB · Checked {m[3]}'),
        (r'^(Discord|GitHub) 요청이 잠시 제한됐습니다\. (\d+)초 뒤 현재 단계에서 다시 시도해 주세요\. \(확인 번호: ([a-f0-9]+)\)$',
         lambda m: f'{m[1]} has temporarily limited requests. Try this step again in {m[2]} seconds. (Reference: {m[3]})'),
        (r'^(Discord|GitHub) 요청이 잠시 제한됐습니다\. 잠시 후 현재 단계에서 다시 시도해 주세요\. \(확인 번호: ([a-f0-9]+)\)$',
         lambda m: f'{m[1]} has temporarily limited requests. Try this step again shortly. (Reference: {m[2]})'),
        (r'^(Discord|GitHub) 로그인 또는 접근 권한을 다시 확인해 주세요\. \(확인 번호: ([a-f0-9]+)\)$',
         lambda m: f'Check your {m[1]} sign-in and access permissions. (Reference: {m[2]})'),
        (r'^(Discord|GitHub)에서 요청을 완료하지 못했습니다\. 현재 단계에서 다시 시도해 주세요\. \(확인 번호: ([a-f0-9]+)\)$',
         lambda m: f'{m[1]} could not complete the request. Please try this step again. (Reference: {m[2]})'),
        (r'^인증을 완료하지 못했습니다\. 로그인부터 다시 진행해 주세요\. \(확인 번호: ([a-f0-9]+)\)$',
         lambda m: f'We could not complete sign-in. Please start again. (Reference: {m[1]})'),
    ]
    for pattern, render in patterns:
        match = re.fullmatch(pattern, stripped)
        if match:
            return render(match)
    return value


def preference(request):
    values = parse_qs(urlsplit(request.path).query, max_num_fields=16).get('lang', [])
    explicit = values[0] if len(values) == 1 and values[0] in ('ko', 'en') else None
    request.ui_language_choice = explicit
    if explicit:
        return explicit
    try:
        cookies = SimpleCookie(request.headers.get('Cookie', ''))
        if COOKIE in cookies and cookies[COOKIE].value in ('ko', 'en'):
            return cookies[COOKIE].value
    except CookieError:
        pass
    return 'en' if request.headers.get('Accept-Language', '').lower().startswith('en') else 'ko'


def switcher():
    path = PAGE_PATH.get()
    return '<nav class="language-switcher" aria-label="Language">' + ''.join(
        '<a href="' + path + '?lang=' + code + '" lang="' + code + '" translate="no"'
        + (' aria-current="true"' if LANGUAGE.get() == code else '') + '>' + label + '</a>'
        for code, label in [('ko', '한국어'), ('en', 'English')]) + '</nav>'


class LocalizedHTML(HTMLParser):
    void = {'meta', 'link', 'img', 'input', 'br', 'hr', 'source', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.output, self.stack = [], []

    def handle_starttag(self, tag, attrs):
        if tag == 'html':
            attrs = [(name, LANGUAGE.get() if name == 'lang' else value) for name, value in attrs]
        self.output.append('<' + tag + ''.join(' ' + name + ('' if value is None else '="' + html.escape(text(value) if name in ('aria-label', 'placeholder', 'title') else value, quote=True) + '"') for name, value in attrs) + '>')
        if tag not in self.void:
            self.stack.append((tag, tag in ('style', 'script', 'option', 'strong') or dict(attrs).get('translate') == 'no'))

    def handle_endtag(self, tag):
        self.output.append('</' + tag + '>')
        for index in range(len(self.stack)-1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        raw = any(tag in ('style', 'script') for tag, _ in self.stack)
        translated = data if any(skip for _, skip in self.stack) else text(data)
        self.output.append(translated if raw else html.escape(translated, quote=False))

    def handle_entityref(self, name):
        self.output.append('&' + name + ';')

    def handle_charref(self, name):
        self.output.append('&#' + name + ';')

    def handle_decl(self, decl):
        self.output.append('<!' + decl + '>')


def render(source):
    if LANGUAGE.get() == 'ko':
        return source
    parser = LocalizedHTML()
    parser.feed(source)
    parser.close()
    return ''.join(parser.output)
