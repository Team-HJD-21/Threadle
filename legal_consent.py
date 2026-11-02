import time

VERSION = '2026-11-02-r2'


def fields(session=None):
    from product_web import t
    scope = str(session.data.get('guild', '')) + '|' + session.data.get('repository', '') if session else ''
    previous = session.data.get('legal_acceptance', {}) if session else {}
    if previous.get('version') == VERSION and previous.get('scope') == scope:
        return '<p class="muted">' + t('이번 연결의 약관·공개 범위 확인이 기록됐습니다.', 'Your terms acceptance and audience confirmation for this connection have been recorded.') + '</p>'
    body = '<fieldset class="legal-confirmation"><legend>' + t('연결 전 확인', 'Before you connect') + '</legend>'
    body += '<p>' + t('로그인 식별자와 연결 설정은 요청한 서비스를 제공하기 위해 처리합니다. 개인정보 안내는 아래에서 확인할 수 있으며, 광고 수신 동의는 요청하지 않습니다.', 'We use account identifiers and connection settings to provide the service you request. Read the privacy notice below. We do not ask you to opt into marketing.') + '</p>'
    body += '<p><a href="/connect/terms" target="_blank" rel="noopener noreferrer">' + t('이용약관', 'Terms of Service') + '</a> · <a href="/connect/privacy" target="_blank" rel="noopener noreferrer">' + t('개인정보처리방침', 'Privacy Policy') + '</a></p>'
    labels = [('terms', '이용약관에 동의합니다.', 'I agree to the Terms of Service.'),
              ('age', '만 14세 이상이며 GitHub·Discord와 해당 국가의 이용 연령 요건을 충족합니다.', 'I am at least 14 and meet the age requirements of GitHub, Discord, and my country.'),
              ('audience', '저장소 내용을 전달할 권한이 있으며, 비공개 내용과 이미지도 연결한 Discord 채널 구성원에게 공개됨을 확인했습니다.', 'I am authorized to forward this repository’s content and understand that private content and attachments will be visible to members of the connected Discord channels.')]
    for name, ko, en in labels:
        body += '<label class="legal-check"><input type="checkbox" name="legal_' + name + '" value="1" required> <span>' + t(ko, en) + '</span></label>'
    return body + '<input type="hidden" name="legal_version" value="' + VERSION + '"></fieldset>'


def receipt(session, fields, scope, clock=time.time):
    from connect_auth import ConnectError
    previous = session.data.get('legal_acceptance', {})
    if not any(key.startswith('legal_') for key in fields) and previous.get('scope') == scope and previous.get('version') == VERSION:
        return dict(previous)
    if fields.get('legal_version') != VERSION or any(fields.get('legal_' + key) != '1' for key in ('terms', 'age', 'audience')):
        from product_web import t
        raise ConnectError(t('연결 전 약관·이용 연령·공개 범위 확인 항목을 선택해 주세요.', 'Please review the terms, age requirements, and channel audience before connecting.'))
    return {'version': VERSION, 'scope': scope, 'accepted_at': clock()}
