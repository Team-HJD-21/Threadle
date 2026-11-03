import json
import re
import time
from urllib.parse import urlencode

from connect_auth import Settings, manages_guild
from connect_store import Sessions


class DiscordConnect:
    def __init__(self, service, settings, key, sessions=None, clock=time.time):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        self.service, self.settings, self.clock = service, settings, clock
        self.key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(key))
        self.sessions = sessions or Sessions(service.root / 'connect.sqlite3', settings.session_key)
        from command_jobs import CommandJobs
        self.jobs = CommandJobs(service, self.sessions)

    @classmethod
    def create(cls, service):
        key = service.env.get('DISCORD_PUBLIC_KEY')
        if not key:
            return None
        settings = Settings.load(dict(service.env, ONBOARDING_ENABLED='1'))
        return cls(service, settings, key)

    @staticmethod
    def message(text, url=None):
        data = {'content': text, 'flags': 64, 'allowed_mentions': {'parse': []}}
        if url:
            data['components'] = [{'type': 1, 'components': [{'type': 2, 'style': 5, 'label': 'Connect a GitHub repository', 'url': url}]}]
        return 200, {'type': 4, 'data': data}

    def receive(self, body, signature, timestamp):
        from cryptography.exceptions import InvalidSignature
        try:
            if not timestamp or not timestamp.isdigit() or abs(self.clock() - int(timestamp)) > 300:
                return 401, {'error': 'invalid signature'}
            self.key.verify(bytes.fromhex(signature or ''), timestamp.encode() + body)
        except (ValueError, InvalidSignature):
            return 401, {'error': 'invalid signature'}
        try:
            payload = json.loads(body)
            if payload.get('type') == 1:
                return 200, {'type': 1}
            if str(payload.get('application_id')) != self.settings.discord_id:
                return 403, {'error': 'application mismatch'}
            if payload.get('type') == 4:
                choices = []
                data = payload.get('data', {})
                if data.get('name') in ('summary', 'settings', 'settings-history') and manages_guild(payload.get('member', {})):
                    focused = next((v for v in data.get('options', []) if v.get('focused')), {})
                    if focused.get('name') == 'repository':
                        guild = str(payload.get('guild_id', ''))
                        search = str(focused.get('value', '')).casefold()
                        bindings = sorted((b for b in self.service.registry.active()
                                          if b.auth == 'app' and b.config['guild_id'] == guild and search in b.repo.casefold()),
                                          key=lambda b:b.repo.casefold())
                        choices = [{'name': b.repo[:100], 'value': b.id} for b in bindings[:25]]
                return 200, {'type': 8, 'data': {'choices': choices}}
            if payload.get('type') == 2 and payload.get('data', {}).get('name') in ('summary', 'settings', 'settings-history'):
                if not all(re.fullmatch(r'[1-9][0-9]*', str(value or '')) for value in (payload.get('guild_id'), payload.get('channel_id'), payload.get('id'), payload.get('member', {}).get('user', {}).get('id'))):
                    return self.message('Run this command in a connected server text channel.')
                error = self.jobs.enqueue(payload)
                return self.message(error) if error else (200, {'type': 5, 'data': {'flags': 64}})
            if payload.get('type') != 2 or payload.get('data', {}).get('name') != 'connect':
                return self.message('Run /connect in your Discord server to get started.')
            if self.service.web is None:
                return self.message('Connection setup is not ready yet. Please try again shortly.')
            member = payload.get('member', {})
            guild = str(payload.get('guild_id', ''))
            user = str(member.get('user', {}).get('id', ''))
            channel = str(payload.get('channel_id', payload.get('channel', {}).get('id', '')))
            identity = str(payload.get('id', ''))
            if not all(re.fullmatch(r'[1-9][0-9]*', value) for value in (guild, user, channel, identity)):
                return self.message('Run this command in a text channel of the Discord server you want to connect.')
            if not manages_guild(member):
                return self.message('You need Manage Server permission to connect a GitHub repository.')
            code = self.sessions.ticket(identity, user, guild, channel)
            if code is None:
                return self.message('This connection link was used or has expired. Run /connect again.')
            url = self.settings.base + '/connect?' + urlencode({'ticket': code})
            return self.message('Connect a GitHub repository to this server. This private link expires in 5 minutes and can be used once.', url)
        except (ValueError, TypeError, KeyError, AttributeError):
            return 400, {'error': 'invalid interaction'}


def command_definition():
    return {'name': 'connect', 'type': 1, 'description': 'Connect GitHub repository notifications to this server.',
            'name_localizations': None, 'description_localizations': None,
            'default_member_permissions': str(1 << 5), 'contexts': [0], 'integration_types': [0]}


def register_command(service, settings):
    api = service.discord()
    path = '/applications/' + settings.discord_id + '/commands'
    commands = api.call('GET', path)
    if not isinstance(commands, list):
        raise ValueError('Could not read existing application commands')
    definition = command_definition()
    existing = next((c for c in commands if c.get('name') == 'connect' and c.get('type') == 1), None)
    result = api.call('PATCH', path + '/' + str(existing['id']), {k: v for k, v in definition.items() if k != 'type'}) if existing else api.call('POST', path, definition)
    if not isinstance(result, dict) or not str(result.get('id', '')).isdigit():
        raise ValueError('Could not verify the /connect command; existing commands were preserved')
    for command in commands:
        if command.get('name') == '연결' and command.get('type') == 1:
            api.call('DELETE', path + '/' + str(command['id']))
    return result
