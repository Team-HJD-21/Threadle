import datetime as dt
from pathlib import Path
import time
from discord_threads import Api, ApiError


class InstallationTokens:
    def __init__(self, app_id, key_file, api_factory=Api, clock=time.time, signer=None):
        self.app_id, self.key_file = app_id, key_file
        self.api_factory, self.clock, self.signer = api_factory, clock, signer
        self.cache = {}

    def token(self, installation, repository):
        key = (installation, repository)
        cached = self.cache.get(key)
        if cached and cached[1] > self.clock() + 60:
            return cached[0]
        app = self.app_api()
        result = app.call('POST', f'/app/installations/{installation}/access_tokens',
                          {'repository_ids': [repository]})
        expires = dt.datetime.fromisoformat(result['expires_at'].replace('Z', '+00:00')).timestamp()
        self.cache[key] = (result['token'], expires)
        return result['token']

    def app_api(self):
        signer = self.signer
        if signer is None:
            import jwt
            signer = jwt.encode
        now = int(self.clock())
        jwt_token = signer({'iat': now - 60, 'exp': now + 540, 'iss': self.app_id},
                           Path(self.key_file).read_bytes(), algorithm='RS256')
        return self.api_factory('GitHub', 'https://api.github.com', jwt_token, 'Bearer')

    def invalidate(self, installation, repository):
        self.cache.pop((installation, repository), None)


class RepositoryApi(Api):
    def __init__(self, binding, tokens=None, legacy_token=None, api_factory=Api):
        self.binding, self.tokens, self.legacy = binding, tokens, legacy_token
        self.api_factory = api_factory

    def call(self, method, path, data=None):
        prefix = '/repos/' + self.binding.repo
        if path == '/graphql':
            owner, name = self.binding.repo.split('/')
            if method != 'POST' or data.get('variables', {}).get('owner') != owner or data.get('variables', {}).get('name') != name:
                raise ValueError('GraphQL repository scope mismatch')
        elif not (method == 'GET' and (path == f'/repositories/{self.binding.repo_id}' or
                  path == prefix or path.startswith(prefix + '/') or path.startswith(prefix + '?'))):
            raise ValueError('Repository API scope mismatch')
        for attempt in range(2):
            token = self.legacy if self.binding.auth == 'legacy' else self.tokens.token(self.binding.installation, self.binding.repo_id)
            client = self.api_factory('GitHub', 'https://api.github.com', token, 'Bearer')
            try:
                return client.call(method, path, data)
            except ApiError as error:
                if error.status != 401 or self.binding.auth == 'legacy' or attempt:
                    raise
                self.tokens.invalidate(self.binding.installation, self.binding.repo_id)

    def fetch_media(self, url):
        from notification_media import installation_image, rendered_installation_image
        token = (self.legacy if self.binding.auth == "legacy" else
                 self.tokens.token(self.binding.installation, self.binding.repo_id))
        return installation_image(url, token) or rendered_installation_image(url, token, self.binding.repo)
