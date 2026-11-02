# Threadle 관리자 화면

주소: `https://threadle.teamhjd.com/connect/admin`

설정 전에는 인증을 거부합니다. 일반 사용자 Discord/GitHub 로그인으로는 관리자 권한을 얻을 수 없습니다. 실제 Cloudflare 로그인·운영 노트북 적용은 별도 확인이 필요합니다.

## 제공 기능

- 연결된 Discord 서버 수, 저장소 수, 일시중지 수.
- 저장소 이름 또는 Discord 서버 ID 검색. 한 번에 최대 50건 표시.
- 연결별 동작 상태, 대기 이벤트·재시도·예약/묶음 알림 수.
- 일시중지와 재개. 중지 중에도 새 웹후크를 대기열에 보관하며, 재개하면 처리합니다. 이미 외부 API로 전송 중인 요청은 완료될 수 있습니다. 기존 대기열 용량 제한은 유지됩니다.
- 연결 해제. 연결만 해제하며 Discord 채널·스레드, 대기열과 기존 기록은 삭제하지 않습니다. 사용자는 나중에 새로 연결할 수 있습니다. 영구적인 사용자/서버 차단 기능과 다릅니다.
- 최근 관리자 작업 30건, 분배 대기 수, 디스크 여유 공간.
- 모든 변경에 저장소 이름 입력 확인, 오래된 화면에서 변경 시 거절, 관리자 작업 기록.
- 한국어/영어 지원. 비밀키·토큰·알림 본문은 화면에 표시하지 않습니다.

## 설치

이 ZIP은 직전 ProductUpdate의 변경도 포함합니다. 둘 중 최신 AdminUpdate를 사용하면 됩니다. 서버 노트북 Downloads에 ZIP을 복사하고 관리자 Windows PowerShell에서 실행합니다.

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Threadle-AdminUpdate.zip" -DestinationPath "$env:USERPROFILE\Threadle-AdminUpdate" -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Threadle-AdminUpdate\update-onboarding.ps1"
```

Threadle 주소 이전을 아직 하지 않았다면 함께 포함된 `THREADLE-UPDATE.md`를 따릅니다. 기존 주소로 운영 중이라면 그 주소에 `/connect/admin`을 붙여 접근할 수 있지만, Access도 해당 호스트에 맞춰 등록해야 합니다. 관리 기능 추가를 위해 GitHub/Discord 앱의 콜백이나 Secret을 새로 만들 필요는 없습니다.

## Cloudflare Access 등록

Cloudflare Zero Trust → Access → Applications에서 Self-hosted 애플리케이션을 만듭니다.

| 항목 | 값 |
|---|---|
| 이름 | Threadle Admin |
| 호스트 | threadle.teamhjd.com |
| 보호할 경로 | /connect/admin* |
| Allow 정책 | 본인 관리자 이메일만 포함 |
| 로그인 방식 | 본인이 사용할 이메일 PIN 또는 연결한 로그인 제공자 |

`/connect/admin`과 `/connect/admin/confirm`, `/connect/admin/action`이 모두 포함되어야 합니다. 도메인 전체 또는 `/connect/*`에 Access를 적용하지 않습니다. 일반 사용자 연결, GitHub 웹후크, Discord Interactions에는 Access 로그인이 걸리면 안 됩니다.

공개 호스트에서 Access를 사용하려면 DNS의 threadle 레코드를 **Proxied(주황색 구름)**로 운영해야 합니다. DNS only 상태에서는 Access 로그인 화면이 적용되지 않습니다. 서버 인증서가 유효한 상태에서 SSL/TLS는 Full (strict)를 사용합니다. 기존 Caddy 인증서의 갱신도 Cloudflare 프록시 뒤에서 가능한 방식인지 확인해야 합니다. 프록시 도입 후에는 DNS challenge 또는 Cloudflare Origin 인증서 등의 서버 인증서 관리 방식을 사용할 수 있습니다. 인증서 검사를 끄는 방식으로 우회하지 않습니다.

Cloudflare가 발급한 JWT를 노트북 서버도 검증하므로, 기존 DNS나 공인 IP로 직접 접속하거나 인증 이메일 헤더만 위조해도 접근할 수 없습니다. 서명·발급자·이 앱의 Audience·만료·관리자 이메일을 확인합니다.

## 서버에 검증 정보 등록

Cloudflare에서 다음 두 값을 확인합니다. 둘 다 비밀키/토큰이 아닙니다.

1. **Team domain**: `내팀이름.cloudflareaccess.com` 형태. `https://`나 경로를 제외합니다.
2. 방금 만든 관리자 앱의 **Application Audience (AUD) tag**: 64자리 값. 다른 앱의 AUD를 사용하지 않습니다.

설정이 완료되면 노트북 관리자 PowerShell에서 아래 세 값을 실제 값으로 바꿔 실행합니다.

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\configure-admin.ps1" -TeamDomain "내팀이름.cloudflareaccess.com" -Audience "관리자앱의64자리AUD" -Emails "관리자이메일@example.com"
```

서버는 별도의 `admin-access.json`에 공개 검증 설정만 저장합니다. 기존 GitHub/Discord Secret, 연결 정보, Caddy, Perforce 설정은 변경하지 않습니다. Receiver만 재시작합니다. 설정 파일은 서버의 기존 보호된 설치 폴더에 유지하며 저장소에 올리지 않습니다.

관리 페이지를 끄려면:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\configure-admin.ps1" -Disable
```

## 확인

1. 브라우저에서 `/connect/admin` 접속 시 Cloudflare 로그인이 먼저 나타나는지 확인합니다.
2. 허용된 본인 계정으로만 대시보드에 들어갈 수 있어야 합니다.
3. 테스트 연결을 일시중지하고 GitHub 이벤트를 발생시킨 뒤 대기 수 증가 → 재개 후 같은 스레드 전송을 확인합니다.
4. 일반 연결 페이지와 기존 GitHub/Discord 알림은 로그인 없이 계속 동작해야 합니다.
5. 연결 해제는 테스트 연결에서만 확인합니다. 실제 Discord 채널은 삭제되지 않아야 합니다.

별도 관리 비밀번호, 관리자 GitHub 토큰, 데이터베이스 다운로드, 임의 코드 실행·파일 삭제 기능은 제공하지 않습니다. 결제/구독 권한 관리도 아직 포함하지 않습니다.

참고: [Cloudflare JWT 검증](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/validating-json/), [공개 호스트 Access 설정](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/self-hosted-public-app/).


## 웹 업데이트

이번 버전을 한 번 설치하면 관리자 페이지 하단에서 유지관리자가 서명한 Threadle 업데이트 ZIP을 업로드할 수 있습니다. 업로드 검증 후 별도로 UPDATE를 입력하고 적용합니다. 서명 비밀키는 설치 파일에 포함하지 않습니다. 자세한 설치/복구 범위는 THREADLE-UPDATE.md를 참고하세요.
