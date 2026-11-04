# Threadle 업데이트와 주소 이전

이 패키지는 서버 노트북에서 실행합니다. 개발 PC에서 테스트했으며 운영 노트북 적용, 실제 OAuth 승인, 실제 Discord 전송은 아직 확인하지 않았습니다.

## 1. 기능 업데이트

ZIP을 서버 노트북 Downloads에 복사하고 관리자 Windows PowerShell에서 실행합니다.

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Threadle-NotificationsUpdate.zip" -DestinationPath "$env:USERPROFILE\Threadle-NotificationsUpdate" -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Threadle-NotificationsUpdate\update-onboarding.ps1"
```

`UPDATE PASSED`가 나왔을 때만 명령어를 등록합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\run-admin.ps1" register-discord-command
```

명령어는 영어로 등록됩니다. 기존 연결, 대기 알림, 인증 정보는 보존하며 `/summary`, `/settings`를 추가합니다. 무관한 명령어를 일괄 삭제하지 않습니다.

- `/summary type:PR` / `type:Issue` / `type:Discussion`: 지금 열린 항목 수와 제목 목록. 목록이 길면 15개씩 표시하며 `page:2`처럼 다음 페이지를 조회합니다. Discussion은 미답변 수가 아니라 닫히지 않은 수입니다.
- 여러 저장소가 있으면 `repository`의 연결된 저장소 선택지에서 고릅니다. 저장소 채널에서는 해당 채널의 연결을 먼저 찾습니다.
- `/settings`: 설정 조회. 옵션을 넣으면 변경합니다. 예: `review_hours:24`, `digest:daily`, `digest_hour:9`, `utc_offset:9`, `quiet:true`, `quiet_start:22`, `quiet_end:8`.
- `review_hours:0`은 열린 PR 반복 알림 끄기, `batch_seconds`는 0/60/300초, `digest`는 off/daily/weekly, `digest_weekday`는 월요일 0~일요일 6입니다.
- 서버 관리 권한이 필요하며, 실행 시 서버 권한을 다시 확인합니다. 응답은 명령어 실행자에게만 보입니다.
- PR에 커밋이 추가되는 `pull_request.synchronize`는 기존 PR 스레드로 갑니다. GitHub App의 Pull request 이벤트가 활성화되어 있어야 합니다. 커밋 자체에 달린 댓글 수집은 별도 기능이며 이번 변경에 포함하지 않습니다.
- 리뷰 요청/요청 제거, 담당자 할당/해제, 라벨 추가/제거, 마일스톤 지정/해제, 초안 전환에는 PR 본문을 반복하지 않습니다. 변경된 담당자/라벨/마일스톤은 표시합니다.
- `commented` 리뷰는 결과 문구 없이 실제 작성한 댓글을 표시합니다. 본문 없는 `commented` 리뷰 이벤트는 건너뛰며, 별도로 도착하는 인라인 댓글은 보냅니다.
- 새 PR/Issue/Discussion 스레드 첫 안내에는 작성자를 표시합니다. PR에는 `base` ← `head` 브랜치를 표시하며, 작성 시각은 Discord의 사용자 시간대 표시를 사용합니다. 기존 스레드의 첫 메시지를 일괄 수정하지 않습니다.
- 열린 PR 반복 알림은 담당자와 요청된 리뷰어가 없어도 동작하며 초안도 포함합니다. 설정한 간격마다 반복하고, 종료되면 멈춥니다. 조용한 시간이 끝나면 미뤄진 안내부터 처리합니다. 기존 `review_hours` 설정을 그대로 사용합니다.
- 일일/주간 요약은 열린 PR 제목(최대 15개, 나머지는 `/summary`로 조회), 가장 오래된 미해결 Issue 5개, 최근 완료된 Issue 5개를 보여줍니다. Issue의 작은 하단 숫자는 전체 남은 Issue 수입니다. 완료 이유가 `COMPLETED`인 Issue만 최근 완료에 포함합니다.
- `/summary type:PR|Issue|Discussion repository:<선택> page:1`은 각 종류의 미종료 항목 제목을 조회합니다. 응답은 명령어 실행자에게만 보입니다.
- 이전 서버의 누적 Discord 기록을 매번 다시 읽지 않도록 전송 영수증과 스레드 시작 메시지를 색인합니다. 전송 결과가 불확실한 재시도에서는 기록을 조회해 복구합니다. 한 서버의 가득 찬 대기열이 다른 서버로의 분배를 중단하지 않습니다. 예약 조회도 실시간 웹후크 처리와 분리합니다.
- 관리자 페이지는 현재 연결과 해제 기록을 나눠 보여주며, 서버 이름과 최초 연결한 GitHub 사용자의 닉네임을 표시합니다. 기존 수동 연결에서 사용자를 확인할 수 없으면 ID 또는 확인 불가로 표시합니다. 서버 이름은 시작 시/5분마다 갱신하고 사용자 닉네임은 캐시합니다.
- 모바일 작동 예시도 채널 목록/채널/스레드의 가로 3분할 구성을 유지합니다. 좁은 화면에서는 예시 내용이 함께 축소됩니다.

검증: 외부 전송 없는 회귀 검사와 설치/롤백 모의 검사를 수행합니다. 이 실행 환경에서 localhost 연결이 차단되어 HTTP 통합 검사와 실제 OAuth/Discord 전송은 검증하지 못했습니다. 브라우저는 로컬 HTML 파일을 직접 열어 모바일 3분할 및 요금표 정렬을 별도로 확인합니다. 운영 노트북에 적용한 뒤 실제 알림과 저장소 선택지를 확인해야 합니다.

## 2. DNS 준비

Cloudflare의 teamhjd.com DNS에 다음 레코드를 추가합니다.

| 항목 | 값 |
|---|---|
| 유형 | A |
| 이름 | threadle |
| IPv4 | 현재 github-hook.teamhjd.com의 A 레코드와 같은 서버 공인 IP |
| 프록시 | DNS only로 시작 |
| TTL | Auto |

기존 github-hook 레코드는 유지합니다. iptime.org를 CNAME 대상으로 사용하지 않습니다. 기존 포트포워딩 TCP 443 → 서버 노트북 443과 Windows 방화벽 허용은 그대로 사용합니다. 다른 컴퓨터나 휴대전화 데이터망에서도 새 주소가 서버에 도달하는지 확인합니다.

## 3. 새 주소 HTTPS 준비

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\migrate-threadle-domain.ps1" -PrepareOnly
```

이 단계는 Caddy에 새 호스트와 검색용 경로를 추가합니다. 앱의 현재 주소는 아직 바꾸지 않습니다. Caddy가 인증서를 발급한 뒤 `https://threadle.teamhjd.com/health`가 `receiver-ready`를 반환하는지 외부에서 확인합니다. 인증서/접속이 실패하면 다음 단계로 진행하지 않습니다.

## 4. GitHub App에서 직접 변경

GitHub → Settings → Developer settings → GitHub Apps → 현재 앱 `teamhjd-threadle`의 설정을 엽니다. **새 앱을 만들지 않고 기존 앱을 수정합니다.**

| 설정 | 새 값 |
|---|---|
| Homepage URL | https://threadle.teamhjd.com/ |
| Callback URL | https://threadle.teamhjd.com/connect/github/callback |
| Setup URL | https://threadle.teamhjd.com/connect/github/installed |
| Webhook URL | https://threadle.teamhjd.com/github/app-webhook |

Callback URL은 전환 중 기존 주소도 남기고 새 주소를 추가합니다. Webhook Active는 유지하고 기존 Webhook Secret도 그대로 둡니다. Delivery에서 새 주소의 응답을 확인합니다. `/connect/github/installed`는 Setup URL이며 OAuth Callback URL과 다릅니다.

기존 저장소 Settings → Webhooks에 `/github/webhook` 또는 `/github/app-webhook`로 연결한 **노트북 수신용 레거시 훅이 아직 있는 경우에만**, 호스트를 threadle.teamhjd.com으로 바꿉니다. 이미 비활성화한 훅이나 GitHub→Discord 직접 훅은 다시 켜지 않습니다. 중복 알림이 생길 수 있습니다.

App ID, Client ID, Client secret, private key는 재발급할 필요가 없습니다. 앱 표시 이름을 변경하면 slug가 달라질 수 있으므로, 이번에는 기존 slug를 유지합니다. 표시 이름까지 바꾸려면 서버의 GITHUB_APP_SLUG도 함께 바꿔야 합니다.

## 5. Discord 앱에서 직접 변경

Discord Developer Portal → 앱 ID `1565225748699021395`를 선택합니다.

| 위치 | 설정 | 새 값 |
|---|---|---|
| OAuth2 | Redirects에 추가 | https://threadle.teamhjd.com/connect/discord/callback |
| General Information | Interactions Endpoint URL | https://threadle.teamhjd.com/discord/interactions |

Redirects는 기존 콜백도 당분간 유지합니다. Interactions Endpoint 저장 시 Discord의 서명 PING 검증이 통과해야 합니다. Bot Token, Public Key, Client Secret, 봇 초대는 그대로 사용합니다. 봇을 추방하거나 다시 초대하지 않습니다.

선택 사항: General Information의 이름은 Threadle, 아이콘은 제공한 로켓 이미지로 바꿀 수 있습니다. Installation의 Custom URL, App Directory, 설명/프로필에 **기존 사이트 링크를 수동 등록해 둔 항목이 있다면** https://threadle.teamhjd.com/ 으로 바꿉니다. Privacy/Terms URL이 회사 메인 사이트를 가리킨다면 그대로 둡니다. 기존 client_id 기반 기본 초대 링크는 바뀌지 않습니다.

## 6. 서버의 기준 주소 전환

GitHub와 Discord에 새 주소를 저장한 뒤 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\migrate-threadle-domain.ps1"
```

보호된 PUBLIC_BASE_URL과 public-settings.json을 새 주소로 변경하고 Receiver를 재시작합니다. Caddy는 기존 주소도 계속 수신하므로 이전 웹후크가 갑자기 끊기지 않습니다. 실패하면 설정을 복원합니다. 백업 위치는 명령 결과에 표시됩니다.

기존 브라우저 로그인 세션은 다른 도메인에서 공유되지 않습니다. 새 주소에서 다시 로그인하면 됩니다. 저장소 연결과 Discord 채널/스레드는 유지됩니다. 진행 중이던 이전 주소의 연결 절차는 종료하고 새 주소에서 시작합니다.

## 7. 최종 확인과 검색 등록

1. 새 주소에서 한국어/영어, 기능·설정 방법·요금제·FAQ 이동 확인.
2. Discord 로그인 → 서버 선택 → GitHub 승인 → 저장소 연결 완료까지 확인.
3. `/summary` 세 종류 및 `/settings` 조회/변경 확인.
4. 실제 PR 댓글·리뷰 요청·추가 커밋이 같은 PR 스레드에 오는지 확인.
5. `https://threadle.teamhjd.com/robots.txt`, `/connect/sitemap.xml`, `/favicon.ico` 확인.
6. Search Console에서 새 URL prefix 속성을 등록하거나 기존 teamhjd.com 도메인 속성을 사용하고, `https://threadle.teamhjd.com/connect/sitemap.xml`을 제출합니다. 홈 URL 검사 후 색인 생성을 요청합니다. 제목/아이콘 반영과 회사 검색 결과 하위 표시 여부·시점은 Google이 결정합니다.
7. 회사 메인 사이트 관련 사이트, Docs, README, 프로필, 공유했던 안내 링크에 기존 주소가 있다면 새 주소로 교체합니다. Lemon Squeezy 제품 웹사이트 주소도 새 주소로 갱신합니다.
8. 정상 확인 후 GitHub/Discord의 이전 OAuth Callback URL을 제거해도 됩니다. 기존 DNS/호스트는 남은 레거시 웹후크를 확인한 뒤 별도로 정리합니다.

## 요금제 상태

출시 예정 비교는 Free: 서버당 저장소 1개·기본 알림, Pro: 서버당 저장소 5개·모든 팀 기능입니다. 현재 베타는 결제 및 유료 기능 제한을 적용하지 않습니다. 실제 Lemon Squeezy 결제/구독 권한·저장소 한도 적용은 아직 구현/연결되지 않았습니다. 승인 완료 전 유료 결제가 가능한 것처럼 표시하지 않습니다.

설정 참고: [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app), [Discord OAuth2](https://docs.discord.com/developers/topics/oauth2), [Discord Interactions](https://docs.discord.com/developers/interactions/receiving-and-responding).


## 관리자 페이지에서 다음 업데이트 적용하기

이번 ZIP은 한 번 기존 PowerShell 방식으로 설치해야 합니다. 설치 후 `/connect/admin` 아래의 Threadle 업데이트 영역에서 다음 서명된 ZIP을 올리고, 표시된 버전을 확인한 뒤 `UPDATE`를 입력해 적용합니다. ZIP은 압축을 풀지 않은 원본을 올립니다. 업로드 검사만으로 실행하지 않으며 적용 전에 취소할 수 있습니다.

- 유지관리자의 Ed25519 서명과 각 파일의 SHA-256을 확인합니다. 서명이 없거나 변조된 ZIP, 경로 탈출, 인증/런타임 설정 파일을 덮어쓰는 ZIP은 거부합니다. 서명 비밀키는 개발 PC에만 있으며 배포 ZIP과 서버에는 포함하지 않습니다.
- Cloudflare Access 관리자 인증과 이메일 제한, 관리자 세션, 요청 출처와 CSRF 검증을 모두 거칩니다. 동시에 하나만 적용합니다.
- 별도의 SYSTEM 예약 작업에서 파일 백업 → 적용 → Receiver 재시작/응답 확인 → Discord 명령어 재등록을 수행합니다. 재시작 실패 시 이전 파일을 복구하고 다시 시작합니다. 상태는 관리자 페이지에서 확인합니다.
- 인증 정보·DB·연결·대기열·Caddy·Perforce 설정을 덮어쓰지 않습니다. 업데이트 작업용 파일과 상태는 `admin-updates` 아래에 남습니다. 파일 복구는 DB를 과거 시점으로 되돌리지 않습니다.
- 적용 중 노트북이 재부팅되면 시작 시 작업을 다시 확인합니다. 파일 변경이 시작된 미완료 작업은 기존 백업으로 복구합니다. 완료한 작업은 재부팅 때 반복 적용하지 않습니다.
- 업데이트 재시작 동안 HTTPS 수신이 잠시 끊길 수 있습니다. 이미 저장된 대기열은 보존됩니다. 재시작 구간에 GitHub에서 전송 실패로 기록된 웹후크는 필요하면 GitHub의 Recent deliveries에서 재전송해야 합니다.

추가 페이지: `/connect/changelog` (한국어/영어 패치노트). 사이트 로고·작동 예시의 Threadle 프로필·사이트 아이콘은 기존에 만든 Threadle 캐릭터 이미지로 맞췄습니다. Discord 앱 자체의 아바타는 이번 설치가 변경하지 않습니다.


## 2026.11.01.2: 명령어·약관·보관 기간

- `Threadle-LegalUpdate.zip`은 이전 변경을 포함하는 누적 업데이트입니다. 이전 버전에서 웹 업데이트를 설치했다면 ZIP을 관리자 페이지에 그대로 업로드합니다. 아직 웹 업데이트 기능이 없다면 기존처럼 ZIP을 풀고 update-onboarding.ps1을 한 번 실행합니다.
- Discord 명령어를 서버 시작 시 확인하고, 변경된 이름·옵션을 전역 명령어로 자동 등록합니다. 같은 구성이면 다시 쓰지 않으며 실패하면 60초 뒤 재시도합니다. 정상 등록 후에도 Discord 클라이언트의 캐시가 갱신되기 전까지 예전 옵션이 보일 수 있습니다. 봇 재초대나 저장소 재연결은 필요 없습니다.
- 관리자 페이지에 명령어 등록 상태를 표시합니다. `registered`는 Discord API에서 정의를 확인한 상태이며 모든 사용자의 앱 화면 캐시가 동시에 갱신됐다는 뜻은 아닙니다.
- `/connect/terms`, `/connect/privacy`를 로그인 없이 열 수 있는 한국어·영어 페이지로 추가하고, 푸터와 사이트맵에 연결합니다. Discord Developer Portal의 Terms of Service URL과 Privacy Policy URL에 해당 공개 URL을 등록합니다.
- 해제한 연결의 30일 보관 기간을 적용합니다. 정리는 서비스 시작 시와 24시간마다 실행합니다. 기존에 해제 시각이 없던 기록은 이 버전 설치일부터 계산합니다. 일시중지는 삭제 대상이 아닙니다.
- 삭제 범위는 만료한 연결의 설정·감사 기록·큐 DB·전송 식별자, 미사용 계정 표시 정보 및 해당 연결의 레이아웃 캐시입니다. 다른 연결에도 필요한 계정·저장소 정보는 보존하고, 이미 Discord에 보낸 메시지는 삭제하지 않습니다. 해제 후 재연결한 경우 새 연결은 보존하고 예전 연결 기록만 삭제합니다.
- 기존 테이블의 열 개수는 바꾸지 않고 별도 보관 시각 테이블과 트리거를 사용합니다. 파일 롤백 뒤에도 이전 코드의 데이터 삽입을 깨지 않도록 했습니다.


## 2026.11.01.3: 메뉴와 반응형

Threadle-InterfaceUpdate.zip은 약관·30일 보관 정책을 포함한 누적 업데이트입니다. 관리자 페이지에서 원본 ZIP을 업로드해 적용합니다. 모든 페이지의 기본 선택 메뉴를 공통 디자인으로 개선하며, 키보드/터치·필수 선택·폼 제출 값·동적으로 갱신한 설정을 유지합니다. 설정 카드·입력·버튼은 작은 화면에서 줄바꿈하며 Discord 예시의 가로 3분할은 유지합니다.


## 2026.11.01.4: 생성 템플릿·짧은 변경 묶음·통합 요약

`Threadle-ReadableNotifications.zip`은 이전 변경을 포함한 누적 업데이트입니다. 관리자 페이지에 원본 ZIP을 올립니다. 운영 서버에 설치하기 전까지는 반영되지 않습니다.

- 생성 템플릿은 PR(브랜치·작성자·담당자·리뷰어/팀·라벨·마일스톤·Draft), Issue(작성자·유형·담당자·라벨·마일스톤·추가 필드), Discussion(작성자·카테고리·라벨·채택 답변)로 구분합니다. 설정이 없으면 없음, GitHub가 정보를 제공하지 않거나 조회 권한이 없으면 정보 미제공으로 표시합니다.
- Issue 필드는 하드코딩한 Priority/Effort 목록 대신 실제 필드명을 사용합니다. 생성 시 필요한 경우 Issues 읽기 API의 issue-field-values를 조회합니다. 선택형 값은 옵션 이름, 숫자 0도 그대로 표시합니다. field_added는 최초 설정과 값 변경, field_removed는 해제를 뜻합니다. Projects 자체의 별도 필드는 이 기능과 다릅니다.
- 같은 항목의 최초 수신부터 8초 동안 모으며, 다른 저장소/서버/항목은 합치지 않습니다. 생성 직후 변경은 템플릿에 합치고, 댓글은 내용과 변경자를 유지해 같은 메시지에 넣습니다. 8초 뒤 들어온 변경은 새 묶음입니다. CI는 별도 즉시 경로를 유지합니다.
- 한 묶음은 최대 20개 이벤트입니다. Discord 메시지/임베드 제한을 넘으면 여러 메시지로 이어 보냅니다. 묶음은 SQLite에 먼저 확정하여 재시작·재시도 때 새 이벤트가 이미 전송한 묶음에 끼지 않게 합니다. 세부 알림과 요약 알림이 모두 성공해야 원본 이벤트를 완료 처리합니다. 각 묶음은 github-alert에도 한 번 안내됩니다.
- 시간은 설명의 큰 날짜 줄 대신 Discord embed timestamp로 표시합니다. 식별 코드 뒤에 클라이언트의 현지 시각이 표시되고 원래 중복 방지/스레드 복구 식별자는 유지합니다. PR의 브랜치가 작성자보다 먼저 나옵니다. 기존에 보낸 메시지는 소급 수정하지 않습니다.
- /summary는 추가 옵션 없이 현재 서버의 모든 연결 저장소를 나눠 조회합니다. PR은 전부, Issue/Discussion은 5개 이하면 전부, 그보다 많으면 가장 오래된 최대 10개를 제목 링크로 보여주고 나머지 개수를 표시합니다. 길면 비공개 후속 메시지로 이어 보내며 이미 전송이 확인된 페이지는 재시도 때 건너뜁니다. 완료 목록은 요약에서 제외합니다.
- 명령어 정의가 바뀌므로 시작 시 자동 등록됩니다. Discord 화면 캐시는 늦게 갱신될 수 있습니다. /settings의 저장소 선택은 유지됩니다.

공식 형식 근거: https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#issues 및 https://docs.github.com/en/rest/issues/issue-field-values . GitHub의 필드 기능/페이로드는 미리보기 변경 가능성이 있으며, 정보를 받지 못한 값을 추측해 채우지 않습니다.

- GitHub 본문·댓글의 HTML img, Markdown 이미지, GIF 주소를 Discord embed 이미지로 표시합니다. 한 항목에 이미지가 여러 개면 같은 메시지 안에서 나누고, 메시지 제한을 넘으면 이어 보냅니다. 서비스가 사용자 URL을 직접 내려받거나 GitHub 인증 토큰을 이미지 주소로 전달하지 않습니다. Discord가 접근할 수 없는 비공개 첨부는 원문 링크로 확인해야 합니다.
- 추가 필드 조회에는 해당 엔드포인트에 한해 GitHub API 2026-03-10 형식을 사용하며 기존 API 호출 버전은 유지합니다.


## 2026.11.02.1: Windows 업데이트 복구

기존 2026.11.01.1에서 이 누적 버전으로 바로 이동합니다. Legal/Interface/Readable ZIP을 순서대로 적용하지 않습니다. 최초 한 번은 Threadle-RecoveryUpdate.zip을 서버 노트북 Downloads에 복사하고 같은 이름 폴더에 압축을 푼 뒤 repair-update.ps1을 관리자 PowerShell에서 실행합니다. 원본 ZIP도 Downloads에 둡니다. 기본 설치 경로는 C:\ProgramData\TeamHJD\GitHubDiscord입니다.

repair-update.ps1은 서명·추출 파일을 검증하고, 실행 중인 업데이트 작업이 없을 때 Receiver만 정지합니다. 남아 있는 미완료 작업은 기존 로그/백업을 보존한 채 복구 작업으로 대체되었다고 기록합니다. Caddy·Perforce와 인증·연결·대기열 DB는 교체하지 않습니다. 새 작업의 백업은 admin-updates/<job>/backup에 남습니다. 실패 시 서비스 코드 파일 복구와 응답을 확인하며, 실패한 롤백을 성공으로 표시하지 않습니다.

수정한 업데이터는 Receiver 정지 → 변경 파일 백업 → 원자적 백업 목록 확정 → 파일 교체와 해시 검증 → 재시작 확인 순서입니다. 바뀌지 않은 파일을 덮어쓰지 않으며, 복구 전에 모든 필요한 백업을 확인합니다. PowerShell 5.1의 Python stderr는 종료 코드를 확인할 때까지 기록하며 실패 단계가 apply.log에 남습니다. Discord 명령어 등록은 실행 서비스의 자동 동기화에 맡깁니다. 이후 업데이트는 관리자 페이지에서 서명 ZIP을 업로드합니다.

## 2026.11.02.4: 비공개 저장소 이미지·GIF 전달

이 릴리스는 비공개 저장소 이벤트에 포함된 GitHub 첨부 이미지를 저장소별 앱 권한으로 가져와 Discord 메시지 파일로 전달합니다. 앱 인증 정보는 GitHub의 원본 첨부 요청에만 보내고, GitHub가 승인한 이미지 CDN으로 리디렉션하면 인증 없이 다시 요청합니다. 허용된 GitHub 이미지 호스트와 이미지 형식만 처리하고, 원본은 최대 20 MiB·Discord 파일은 최대 4 MiB로 제한합니다. 크기가 큰 정적 이미지는 축소·압축하고, GIF는 애니메이션 프레임을 보존해 축소합니다. 지원할 수 없는 첨부는 기존 링크가 남습니다.

미디어 파일 바이트는 전송 중 메모리에서 처리하며 별도 파일로 저장하지 않습니다. 원본 URL은 대기 중인 이벤트에 남을 수 있습니다. 개인정보처리방침과 이용약관에도 반영했습니다. 이 방식이 private user-attachment에 실제로 허용되는지와 Discord에서 렌더링되는지는 관리자 업데이트 후 비공개 저장소에서 별도 실전 확인이 필요합니다. 이전 2026.11.01.4 안내의 “토큰을 이미지 주소로 전달하지 않음/비공개 첨부는 링크 확인”은 이 릴리스 전 구현을 설명합니다.

