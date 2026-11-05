import base64
from dataclasses import dataclass
import hashlib
import json
import logging
import math
import re
import time
import uuid
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

LOG = logging.getLogger('connect.provider')


class ConnectError(Exception):
    def __init__(self, message, status=400):
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class Settings:
    base: str
    discord_id: str
    discord_secret: str
    github_id: int
    github_client: str
    github_secret: str
    github_slug: str
    session_key: str

    @classmethod
    def load(cls, env):
        if env.get('ONBOARDING_ENABLED') != '1':
            return None
        names = ('PUBLIC_BASE_URL', 'DISCORD_CLIENT_ID', 'DISCORD_CLIENT_SECRET', 'GITHUB_APP_ID',
                 'GITHUB_APP_CLIENT_ID', 'GITHUB_APP_CLIENT_SECRET', 'GITHUB_APP_SLUG', 'CONNECT_SESSION_KEY')
        if any(not env.get(name) for name in names):
            raise ValueError('Public connection settings are incomplete')
        result = cls(*(env[name].rstrip('/') if name == 'PUBLIC_BASE_URL' else env[name] for name in names))
        parsed = urlsplit(result.base)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
            raise ValueError('PUBLIC_BASE_URL must be an HTTPS origin')
        if not result.discord_id.isdigit() or not str(result.github_id).isdigit():
            raise ValueError('Application IDs must be numeric')
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,99}', result.github_slug):
            raise ValueError('Invalid GitHub App slug')
        return cls(result.base, result.discord_id, result.discord_secret, int(result.github_id), result.github_client,
                   result.github_secret, result.github_slug, result.session_key)

    def callback(self, provider):
        return self.base + '/connect/' + provider + '/callback'

    def authorize(self, provider, state, verifier):
        if provider == 'discord':
            return 'https://discord.com/oauth2/authorize?' + urlencode({
                'client_id': self.discord_id, 'response_type': 'code', 'scope': 'identify guilds',
                'redirect_uri': self.callback(provider), 'state': state})
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
        return 'https://github.com/login/oauth/authorize?' + urlencode({
            'client_id': self.github_client, 'redirect_uri': self.callback(provider), 'state': state,
            'code_challenge': challenge, 'code_challenge_method': 'S256'})

    def invite(self, guild):
        permissions = sum(1 << bit for bit in (4, 10, 11, 13, 14, 16, 34, 35, 38))
        return 'https://discord.com/oauth2/authorize?' + urlencode({
            'client_id': self.discord_id, 'scope': 'bot applications.commands', 'permissions': permissions,
            'guild_id': guild, 'disable_guild_select': 'true', 'integration_type': '0'})

    def install(self, guild, state):
        permissions = sum(1 << bit for bit in (4, 10, 11, 13, 14, 16, 34, 35, 38))
        return 'https://discord.com/oauth2/authorize?' + urlencode({
            'client_id': self.discord_id, 'response_type': 'code',
            'scope': 'identify guilds bot applications.commands',
            'redirect_uri': self.callback('discord'), 'state': state,
            'permissions': permissions, 'guild_id': guild,
            'disable_guild_select': 'true', 'integration_type': '0'})


class UserApis:
    def __init__(self, settings, opener=urlopen, sleep=time.sleep, clock=time.monotonic):
        self.settings, self.opener, self.sleep, self.clock = settings, opener, sleep, clock

    def request(self, url, token=None, form=None):
        headers = {'Accept': 'application/json', 'User-Agent': 'GitHub-Discord-Connect/1.0'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if url.startswith('https://api.github.com/'):
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        body = None
        if form is not None:
            headers['Content-Type'] = 'application/x-www-form-urlencoded'
            body = urlencode(form).encode()
        provider = 'Discord' if urlsplit(url).hostname == 'discord.com' else 'GitHub'
        operation = 'read' if form is None else 'token-exchange'
        reference = uuid.uuid4().hex[:10]
        deadline = self.clock() + 15
        for attempt in range(3):
            status, detail, delay = 'network', {}, None
            try:
                with self.opener(Request(url, data=body, headers=headers), timeout=min(10, max(.1, deadline - self.clock()))) as response:
                    data = response.read(2_000_001)
                    if len(data) > 2_000_000:
                        raise ConnectError('응답이 너무 큽니다. 저장소 범위를 줄여 다시 시도해 주세요.', 502)
                    result = json.loads(data)
                if isinstance(result, dict) and result.get('error'):
                    reason = result['error'] if result['error'] in ('invalid_client', 'invalid_grant', 'invalid_request', 'bad_verification_code', 'incorrect_client_credentials') else 'unspecified'
                    LOG.warning('Provider failure provider=%s operation=%s status=oauth-denied reason=%s ref=%s',
                                provider, operation, reason, reference)
                    raise ConnectError(f'인증을 완료하지 못했습니다. 로그인부터 다시 진행해 주세요. (확인 번호: {reference})', 403)
                return result
            except HTTPError as error:
                status = error.code
                try:
                    detail = json.loads(error.read(16384))
                    if not isinstance(detail, dict):
                        detail = {}
                except (ValueError, UnicodeError, OSError):
                    detail = {}
                raw_delay = error.headers.get('Retry-After', detail.get('retry_after'))
                error.close()
                if raw_delay is not None:
                    try:
                        delay = float(raw_delay)
                    except (TypeError, ValueError):
                        try:
                            delay = parsedate_to_datetime(str(raw_delay)).timestamp() - time.time()
                        except (TypeError, ValueError, OverflowError):
                            delay = None
                if delay is not None and not math.isfinite(delay):
                    delay = None
                retryable = status == 429 or (form is None and status in (500, 502, 503, 504))
            except (URLError, TimeoutError, OSError):
                retryable = form is None
            except (ValueError, UnicodeError):
                status, retryable = 'invalid-response', form is None
            reason = detail.get('error')
            reason = reason if reason in ('invalid_client', 'invalid_grant', 'invalid_request', 'invalid_scope') else 'unspecified'
            LOG.warning('Provider failure provider=%s operation=%s status=%s reason=%s attempt=%s ref=%s',
                        provider, operation, status, reason, attempt + 1, reference)
            pause = max(.1, delay) if delay is not None else .5 * (2 ** attempt)
            if retryable and attempt < 2 and pause <= 5 and self.clock() + pause + .5 < deadline:
                self.sleep(pause)
                continue
            if status == 429:
                wait = f'{max(1, math.ceil(delay))}초 뒤' if delay is not None else '잠시 후'
                raise ConnectError(f'{provider} 요청이 잠시 제한됐습니다. {wait} 현재 단계에서 다시 시도해 주세요. (확인 번호: {reference})', 429)
            if status in (401, 403):
                raise ConnectError(f'{provider} 로그인 또는 접근 권한을 다시 확인해 주세요. (확인 번호: {reference})', 403)
            raise ConnectError(f'{provider}에서 요청을 완료하지 못했습니다. 현재 단계에서 다시 시도해 주세요. (확인 번호: {reference})', 502)

    def exchange(self, provider, code, verifier):
        s = self.settings
        if provider == 'discord':
            return self.request('https://discord.com/api/v10/oauth2/token', form={
                'client_id': s.discord_id, 'client_secret': s.discord_secret, 'grant_type': 'authorization_code',
                'code': code, 'redirect_uri': s.callback(provider)})
        return self.request('https://github.com/login/oauth/access_token', form={
            'client_id': s.github_client, 'client_secret': s.github_secret, 'code': code,
            'redirect_uri': s.callback(provider), 'code_verifier': verifier})

    def discord(self, token, path):
        return self.request('https://discord.com/api/v10' + path, token)

    def github(self, token, path):
        return self.request('https://api.github.com' + path, token)

    def guilds(self, token):
        rows, after = [], '0'
        for _ in range(50):
            page = self.discord(token, '/users/@me/guilds?' + urlencode({'limit': 200, 'after': after}))
            rows.extend(page)
            if len(page) < 200:
                return rows
            cursor = str(page[-1]['id'])
            if cursor == after:
                break
            after = cursor
        raise ConnectError('서버 목록을 끝까지 확인하지 못했습니다. 다시 시도해 주세요.', 502)

    def github_pages(self, token, path, key):
        for page in range(1, 101):
            data = self.github(token, path + '?' + urlencode({'per_page': 100, 'page': page}))
            rows = data[key]
            yield from rows
            if len(rows) < 100:
                return
        raise ConnectError('저장소 목록을 끝까지 확인하지 못했습니다. 앱의 저장소 접근 범위를 줄여 주세요.', 502)

    def installations(self, token):
        return [i for i in self.github_pages(token, '/user/installations', 'installations')
                if int(i['app_id']) == self.settings.github_id and not i.get('suspended_at')]

    def repositories(self, token, installation):
        return [r for r in self.github_pages(token, f'/user/installations/{installation}/repositories', 'repositories')
                if r.get('permissions', {}).get('admin') is True and not r.get('disabled')]


def manages_guild(guild):
    permissions = int(guild.get('permissions', 0))
    return guild.get('owner') is True or bool(permissions & ((1 << 3) | (1 << 5)))


def channel_permissions(guild_id, member, roles, channel):
    role_ids = {str(role) for role in member.get('roles', [])} | {str(guild_id)}
    permissions = 0
    for role in roles:
        if str(role['id']) in role_ids:
            permissions |= int(role['permissions'])
    if permissions & (1 << 3):
        return (1 << 64) - 1
    overwrites = channel.get('permission_overwrites', [])
    for overwrite in overwrites:
        if int(overwrite['type']) == 0 and str(overwrite['id']) == str(guild_id):
            permissions = permissions & ~int(overwrite['deny']) | int(overwrite['allow'])
    deny, allow = 0, 0
    for overwrite in overwrites:
        if int(overwrite['type']) == 0 and str(overwrite['id']) in role_ids - {str(guild_id)}:
            deny |= int(overwrite['deny'])
            allow |= int(overwrite['allow'])
    permissions = permissions & ~deny | allow
    for overwrite in overwrites:
        if int(overwrite['type']) == 1 and str(overwrite['id']) == str(member['user']['id']):
            permissions = permissions & ~int(overwrite['deny']) | int(overwrite['allow'])
    return permissions
