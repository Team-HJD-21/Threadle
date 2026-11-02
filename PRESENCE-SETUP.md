# Threadle 온라인 표시와 활동 문구

기존 Threadle은 HTTP API로 알림을 보내고 명령어를 수신했습니다. 이 방식은 Discord Gateway에 상시 로그인하지 않으므로, 알림이 동작해도 온라인 표시가 없을 수 있습니다.

이 업데이트는 상태 표시를 위한 Gateway 연결을 Receiver 안의 별도 작업으로 실행합니다. 기존 GitHub 웹후크와 Discord 명령어는 같은 HTTP 경로를 사용합니다. 사용자 메시지·멤버·다른 사용자의 상태를 받는 Gateway Intents는 요청하지 않습니다.

기본 상태는 초록색 온라인, 활동은 `Watching GitHub updates | /connect`입니다. 표시 접두어는 Discord 클라이언트 언어에 따라 달라질 수 있습니다.

## 서버 노트북 설치

Windows Python 3.12 x64용 Discord 라이브러리와 의존성을 포함했습니다. 글로벌 Python 패키지 대신 별도 vendor-presence-v1 폴더를 사용합니다. 다른 OS/Python 버전이면 설치 전에 중단합니다.

직전 AdminUpdate까지 포함하는 누적 패키지입니다. 서버 노트북 Downloads에 ZIP을 복사한 뒤 관리자 PowerShell에서 실행합니다.

```powershell
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Threadle-PresenceUpdate.zip" -DestinationPath "$env:USERPROFILE\Threadle-PresenceUpdate" -Force
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$env:USERPROFILE\Threadle-PresenceUpdate\update-onboarding.ps1"
```

기존 토큰, 저장소 연결, 대기열, Cloudflare Access, Caddy, Perforce 설정은 유지합니다. Receiver 재시작 후 기존 봇 토큰으로 Gateway에 접속합니다. 봇 재초대, privileged intents 활성화, 새 포트포워딩, GitHub Actions는 필요하지 않습니다. 서버의 아웃바운드 HTTPS/WebSocket 연결은 가능해야 합니다.

## 문구 변경

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\configure-presence.ps1" -Text "Weaving GitHub updates | /connect" -Status online
```

상태는 online / idle / dnd 중 하나입니다. 문구는 한 줄, 최대 128자이며 실행 시 Receiver를 재시작합니다.

상태 표시용 연결을 끄려면:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\ProgramData\TeamHJD\GitHubDiscord\configure-presence.ps1" -Disable
```

## 확인

```powershell
Get-Content -LiteralPath "C:\ProgramData\TeamHJD\GitHubDiscord\presence-status.json"
```

- connected: Gateway 로그인 완료.
- connecting / reconnecting: 연결 중 또는 재접속 중.
- authentication-failed: 기존 봇 인증 정보 확인 필요.
- missing-dependency: 상태 표시 라이브러리 설치 확인 필요.
- invalid-settings: presence.json 형식 또는 값 확인 필요.
- disabled / offline: 꺼짐 또는 정상 종료됨.

connected 상태는 30초마다 파일을 갱신합니다. Receiver를 강제 종료하면 마지막 상태 파일이 남을 수 있으므로 checked_at이 최근인지와 실제 Discord 표시도 확인합니다. 온라인 표시는 Gateway 연결 상태이며, 모든 저장소 알림이 성공했다는 건강 상태 보증은 아닙니다.

라이브러리는 연결 유지·재접속을 처리하고, 상태 표시 작업이 실패해도 기존 HTTP 알림 처리를 중단시키지 않습니다. 서버 노트북의 자동 시작 작업이 Receiver를 다시 실행하면 상태 표시도 함께 시작합니다. 실제 Discord 온라인 표시와 재부팅 후 복귀는 운영 노트북 적용 후 확인해야 합니다.

사용 라이브러리와 라이선스는 함께 제공한 vendor-presence-v1의 dist-info 디렉터리에 포함됩니다. 참조: [Discord Gateway](https://docs.discord.com/developers/events/gateway), [discord.py](https://discordpy.readthedocs.io/en/stable/api.html#discord.Client).


## 서버 연결 자동 정리

Gateway는 일반 Guilds intent만 사용합니다. 봇이 추방되거나 서버가 삭제된 것을 확인하면 해당 Discord 서버의 저장소 연결만 해제하며, 기존 전송 기록과 대기열은 보존합니다. 일시적인 unavailable 이벤트는 해제하지 않습니다. Receiver가 꺼져 있거나 온라인 표시를 끈 동안 놓친 변경은 시작 시와 5분마다 서버 멤버십을 재확인합니다. REST에서는 `404 / 10004 Unknown Guild`를 확인하거나, `403 / 50001 Missing Access` 이후 봇의 전체 서버 목록을 끝까지 읽어 해당 서버가 없음을 확인한 경우에만 해제합니다. 단순 권한 오류·시간 초과·요청 제한·서버 장애는 보류합니다. 봇 재초대만으로 과거 연결을 되살리지 않으며 사용자가 다시 연결합니다.
