"""Personal account flow, isolated from the administrator repository setup flow."""
import time
from connect_auth import ConnectError
from web_locale import LANGUAGE


def say(ko, en):
    return en if LANGUAGE.get() == 'en' else ko


class AccountWeb:
    def __init__(self, web):
        self.web = web

    def pending(self, session):
        value = session.data.get('account_pending') if session else None
        if not value or value['expires'] <= time.time():
            raise ConnectError(say('연결 링크가 만료됐습니다. Discord에서 /account connect를 다시 실행해 주세요.',
                                   'This link expired. Run /account connect in Discord again.'), 401)
        return value

    def get(self, request, session, query):
        from connect_web import page, form
        web = self.web
        if query.get('ticket'):
            try:
                pending = web.sessions.consume_ticket(query['ticket'], purpose='account')
            except ValueError:
                raise ConnectError('This account link was used or expired. Run /account connect again.', 401) from None
            fresh = web.sessions.create({'account_pending': pending})
            return web.redirect(request, '/connect/account', fresh)
        pending = self.pending(session)
        if not session.data.get('account_discord_verified'):
            body = '<div class="card"><p>' + say('명령어를 실행한 본인의 Discord 계정인지 확인합니다.',
                    'First, confirm the Discord account that ran the command.') + '</p>'
            body += form('/connect/account/discord', session, '<button>' + say('Discord 계정 확인', 'Verify my Discord account') + '</button>')
        else:
            body = '<div class="card"><p>' + say('이 서버에서 사용할 기능을 직접 선택해 주세요.', 'Choose which features to enable in this server.') + '</p>'
            choices = ('<label><input type="checkbox" name="mentions" value="1" checked> ' + say(
                'GitHub에서 내 계정이 언급되면 Discord에서도 멘션 받기', 'Mention me in Discord when my GitHub account is mentioned') + '</label><br>'
                '<label><input type="checkbox" name="comments" value="1"> ' + say(
                'Threadle의 PR·Issue·Discussion 스레드에 내가 쓴 새 메시지를 내 GitHub 계정의 댓글로 전송',
                'Forward my new messages in Threadle PR, Issue, and Discussion threads as comments from my GitHub account') + '</label><p>' + say(
                '댓글 전송을 켜면 이 서버의 연결된 저장소에서 적용됩니다. 다른 채널·봇 메시지는 보내지 않습니다. 파일은 링크로 전달됩니다. Discord에서 수정하거나 삭제한 메시지는 GitHub에 자동 반영되지 않습니다. /account disconnect로 언제든 연결을 해제할 수 있습니다.',
                'Comment forwarding applies to connected repositories in this server. Other channels and bot messages are excluded. Attachments are sent as links. Later Discord edits and deletions are not synchronized. Disconnect at any time with /account disconnect.') + '</p><p>' + say(
                '댓글 작성용 토큰은 암호화해서 보관합니다. 작성 권한은 본인과 GitHub App 모두 접근할 수 있는 저장소로 제한됩니다.',
                'Comment credentials are encrypted at rest. Access is limited to repositories available to both you and the GitHub App.') + '</p>'
                '<p><a href="/connect/terms" target="_blank" rel="noopener">' + say('이용약관', 'Terms') + '</a> · '
                '<a href="/connect/privacy" target="_blank" rel="noopener">' + say('개인정보처리방침', 'Privacy Policy') + '</a></p>'
                '<label><input type="checkbox" name="terms" value="1" required> ' + say('계정 연결과 선택한 전달 기능에 관한 이용약관에 동의합니다.', 'I agree to the terms for account linking and my selected forwarding features.') + '</label><br>'
                '<label><input type="checkbox" name="age" value="1" required> ' + say('만 14세 이상이며 GitHub·Discord와 해당 국가의 이용 연령 요건을 충족합니다.', 'I am at least 14 and meet the age requirements of GitHub, Discord, and my country.') + '</label>'
                '<button>' + say('선택한 기능으로 GitHub 연결', 'Authorize my GitHub account') + '</button>')
            body += form('/connect/account/github', session, choices)
        return web.send(request, 200, page(say('내 GitHub 계정 연결', 'Connect my GitHub account'), body + '</div>'), session=session)

    def post(self, request, path, session, fields):
        web = self.web
        self.pending(session)
        if path == '/connect/account/discord':
            provider = 'discord'
        elif path == '/connect/account/github' and session.data.get('account_discord_verified'):
            provider = 'github'
            if fields.get('terms') != '1' or fields.get('age') != '1':
                raise ConnectError(say('약관과 이용 연령을 확인해 주세요.', 'Confirm the terms and age requirements.'))
            if any(fields.get(key) not in (None, '1') for key in ('mentions', 'comments')):
                raise ConnectError('Invalid account preferences.')
            session.data['account_choices'] = {key: fields.get(key) == '1' for key in ('mentions', 'comments')}
            if not any(session.data['account_choices'].values()):
                raise ConnectError(say('사용할 기능을 하나 이상 선택해 주세요.', 'Choose at least one feature.'))
            web.sessions.save(session)
        else:
            raise ConnectError('Start with /account connect in Discord.', 400)
        state, verifier = web.sessions.state(session, 'account:' + provider)
        url = web.settings.authorize(provider, state, verifier)
        if provider == 'github':
            # Ask for an expiring user token + refresh token, never broad OAuth repo scopes.
            url += '&scope=offline_access'
        return web.redirect(request, url, session)

    def callback(self, request, provider, session, query):
        from connect_web import page
        web = self.web
        pending = self.pending(session)
        try:
            verifier = web.sessions.consume(session, 'account:' + provider, query.get('state', ''))
        except ValueError:
            raise ConnectError('Account authorization expired or did not match this browser.', 403) from None
        if query.get('error') or not query.get('code'):
            raise ConnectError('Account authorization was canceled.', 400)
        if provider == 'discord' and query.get('iss', 'https://discord.com') != 'https://discord.com':
            raise ConnectError('Authorization provider mismatch.', 403)
        if provider == 'github' and (not session.data.get('account_discord_verified') or not session.data.get('account_choices')):
            raise ConnectError('Verify your Discord account and select your preferences first.', 403)
        result = web.users.exchange(provider, query['code'], verifier)
        token = result.get('access_token')
        if not token or result.get('token_type', '').lower() != 'bearer':
            raise ConnectError('Authorization could not be verified.', 403)
        if provider == 'discord':
            profile = web.users.discord(token, '/users/@me')
            if str(profile.get('id')) != pending['user']:
                raise ConnectError(say('명령어를 실행한 Discord 계정으로 로그인해 주세요.', 'Sign in with the Discord account that ran /account connect.'), 403)
            if not any(str(g.get('id')) == pending['guild'] for g in web.users.guilds(token)):
                raise ConnectError('You no longer belong to this Discord server.', 403)
            session.data['account_discord_verified'] = True
            session.data['account_pending']['expires'] = time.time() + 900
            fresh = web.sessions.rotate(session)
            return web.redirect(request, '/connect/account', fresh)
        profile = web.users.github(token, '/user')
        choices = session.data['account_choices']
        web.service.accounts.link(pending['user'], pending['guild'], profile, result, **choices)
        # No OAuth tokens remain in the browser session after the connection finishes.
        web.sessions.remove(session)
        return web.send(request, 200, page(say('계정 연결이 완료됐어요.', 'Your account is connected.'),
            '<div class="card"><p>' + say('Discord로 돌아가 /account status로 확인해 주세요. 선택한 기능은 이 서버에서만 적용됩니다.',
            'Return to Discord and use /account status. Your selected features apply only in this server.') + '</p></div>'))
