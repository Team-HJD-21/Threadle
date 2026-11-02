# 노트북 서버와 GitHub Actions 연결

운영 서버 경로는 `C:\ProgramData\TeamHJD\GitHubDiscord`입니다. 웹과 봇은 계속 노트북에서 실행합니다. GitHub는 검사와 배포 시작을 담당하고, 노트북의 전용 runner가 배포를 실행합니다.

## 최초 연결

1. `Team-HJD-21/Threadle` → Settings → Actions → Runners → New self-hosted runner에서 Windows / x64를 선택합니다.
2. 노트북의 관리자 PowerShell에서 GitHub 화면에 나온 다운로드·설정 명령을 실행합니다. 설치 위치는 `C:\actions-runner-threadle`로 합니다. 서버 프로그램 폴더 안에 설치하지 않습니다.
3. 추가 label에는 `threadle-production`을 입력합니다. 서비스로 실행하도록 선택하고, 기존 Threadle 예약 작업 제어와 `C:\ProgramData\TeamHJD\GitHubDiscord` 접근이 가능한 Windows 계정으로 실행합니다. 현재 SYSTEM 예약 작업을 제어하는 기존 관리자 권한이 필요합니다. 서비스 등록 시 재부팅 후에도 runner가 시작됩니다.
4. runner 목록에서 Online / Idle을 확인합니다. 등록용 토큰은 GitHub 화면에서 직접 사용하며 저장소나 채팅에 저장하지 않습니다.
5. 봇 토큰, GitHub App 키 등 운영 인증 정보는 기존 노트북 파일을 그대로 사용합니다. 첫 배포 확인 전에는 자동 배포 변수를 켜지 않습니다.
6. main에 반영한 뒤 Actions → Threadle CI and deployment → Run workflow를 엽니다. 첫 실행은 `preflight_only`를 체크합니다. 기존 서비스는 정지하지 않고 서버 파일이 지원하는 기준본인지 검사합니다.
7. `PREFLIGHT PASSED`를 확인하면 Run workflow에서 `preflight_only` 체크를 해제하고 실행합니다.
8. 실제 알림 전달까지 확인한 뒤 Settings → Secrets and variables → Actions → Variables에 `THREADLE_CD_ENABLED`를 만들고 값은 `true`로 설정합니다. 이후 main 변경은 검사 통과 후 자동 배포됩니다.

## 적용 범위와 실패 처리

이 릴리스의 `changes.json`에 명시한 파일만 변경합니다. 그 외 앱 파일, 인증 파일, 데이터베이스, 이벤트 대기열, Caddy, Perforce는 배포 대상에 포함하지 않습니다. 서버 파일이 기준본 또는 이번 결과와 다르면 재시작 전에 실패합니다. 커스텀 서버 파일을 확인 없이 덮어쓰지 않습니다.

배포는 서버에 후보 파일을 준비하고 Python 문법·모듈·미디어 인증 의존성을 확인한 후 Receiver 예약 작업만 정지합니다. 변경 파일을 백업하고 적용한 다음 `/health`와 `/connect` 응답을 확인합니다. 적용이나 응답 확인에 실패하면 백업을 복원하고 Receiver를 다시 시작합니다. GitHub 실행 결과는 실패로 남겨 원인을 확인할 수 있습니다.

백업과 결과는 서버의 `cd-deployments\<시각-작업ID>` 아래에 남습니다. `result.txt`의 `passed`는 로컬 HTTP 응답 확인까지 통과했다는 뜻입니다. 실제 GitHub → Discord 전달 및 비공개 이미지 접근까지 증명하지는 않습니다. 첫 운영 배포 후 공개·비공개 저장소 각각에서 텍스트·이미지·GIF 이벤트를 확인합니다.

PR 검사에서는 GitHub 호스팅 runner만 사용합니다. 노트북 runner는 main 배포에서만 실행합니다. 외부에 공개할 저장소와 운영 runner를 공유하지 않습니다. 배포 중에는 기존 관리자 ZIP 업데이트를 동시에 실행하지 않습니다.

## 다음 릴리스

`changes.json`의 before는 현재 운영 파일, after는 새 파일의 정규화된 SHA-256입니다. 이번 기준은 롤백한 2026.11.02.3의 런타임(2026.11.02.2 공개 미디어 수정 포함)입니다. 업데이트 도구는 수정하지 않습니다.

다음 배포에서는 현재 배포한 after를 before로 옮기고 수정 파일의 after를 다시 계산해야 합니다. 이 목록 없이 앱을 변경하면 CI가 실패합니다. 배포 성공을 확인하기 전에는 이전 패치의 before를 임의로 바꾸지 않습니다.

## 개인정보 보완 릴리스 2026.11.02.6

이 패치는 기존 지원 기준본과 2026.11.02.5 결과를 모두 확인하고 지정 파일만 바꾼다. `previous`는 직전 패치의 정규화된 파일 해시 목록이며 임의의 서버 파일을 허용하는 옵션이 아니다. 동의 기록은 별도 테이블로 추가해 기존 연결 테이블과 롤백 호환성을 유지한다. 기존 사용자에게 과거 동의를 만들어 넣거나 현재 알림을 강제로 중단하지 않는다.

첫 적용은 main에 병합 후 `preflight_only`로 확인하고 실제 적용한다. 적용 후 새 저장소 연결 화면에서 미선택 상태로는 채널이 생성되지 않는지, 확인 후 연결과 기존 알림이 모두 정상인지 확인한다. 운영자 메일함·외부 수동 백업·Cloudflare 설정은 자동 변경하지 않는다. `docs/privacy-operations.md`의 수동 운영 절차를 함께 확인한다.

Pillow 보안 수정은 별도의 `vendor-pillow-13` 폴더에만 설치한다. 최초 preflight는 PyPI 공식 wheel을 약 7MB 다운로드하며 서버 Python이 Windows 3.12 x64인지 PNG/GIF 검사로 확인한다. 다운로드·체크섬·호환성 검사가 실패하면 Receiver를 멈추지 않는다. 기존 vendor 폴더·인증·대기열은 바꾸지 않는다. 이미 설치된 동일 파일은 재사용하고, 예상 밖 변경은 적용 전에 중단한다.
