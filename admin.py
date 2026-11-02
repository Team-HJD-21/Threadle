import argparse
import json
from pathlib import Path
import os
from service import Service, verify_channels


def main():
    parser = argparse.ArgumentParser(description='Local administrator only; no public enrollment endpoint')
    parser.add_argument('--state-dir', type=Path, default=Path(__file__).parent)
    commands = parser.add_subparsers(dest='command', required=True)
    legacy = commands.add_parser('migrate-legacy')
    legacy.add_argument('--repo', required=True)
    legacy.add_argument('--config', type=Path, required=True)
    legacy.add_argument('--tenant', default='TeamHJD')
    add = commands.add_parser('connect-app')
    add.add_argument('--repo', required=True)
    add.add_argument('--repo-id', required=True, type=int)
    add.add_argument('--installation', required=True, type=int)
    add.add_argument('--tenant', required=True)
    add.add_argument('--config', required=True, type=Path)
    commands.add_parser('list')
    commands.add_parser('discord-public-key')
    commands.add_parser('register-discord-command')
    disable = commands.add_parser('disconnect')
    disable.add_argument('binding')
    change = commands.add_parser('set-channels')
    change.add_argument('binding')
    change.add_argument('--config', required=True, type=Path)
    args = parser.parse_args()
    service = Service(args.state_dir)
    if args.command == 'discord-public-key':
        import re
        key = service.discord().call('GET', '/oauth2/applications/@me')['verify_key']
        if not re.fullmatch(r'[a-fA-F0-9]{64}', key):
            raise ValueError('Discord application public key is invalid')
        print(key)
        return
    if args.command == 'register-discord-command':
        from discord_connect import register_command
        from connect_auth import Settings
        register_command(service, Settings.load(dict(service.env, ONBOARDING_ENABLED='1')))
        from command_jobs import register_extra_commands
        register_extra_commands(service, Settings.load(dict(service.env, ONBOARDING_ENABLED='1')))
        print('Discord /connect, /summary and /settings commands registered.')
        return
    if args.command == 'list':
        for b in service.registry.active():
            print(json.dumps({'id': b.id, 'tenant': b.tenant, 'repo': b.repo, 'installation': b.installation,
                              'guild': b.config['guild_id'], 'auth': b.auth}, ensure_ascii=False))
        return
    if args.command == 'disconnect':
        service.registry.disable(args.binding)
        print('Disconnected:', args.binding)
        return
    config = json.loads(args.config.read_text(encoding='utf-8-sig'))
    if args.command == 'set-channels':
        binding = service.registry.get(args.binding)
        if binding is None or config['guild_id'] != binding.config['guild_id']:
            raise ValueError('Unknown binding or server change; disconnect and verify a new connection instead')
        verify_channels(service.discord(), config)
        with service.registry.db() as db:
            db.execute('UPDATE bindings SET config=? WHERE id=?', (json.dumps(config), binding.id))
        print('Updated channel configuration:', binding.id)
        return
    if args.command == 'migrate-legacy':
        config.setdefault('workflow_names', ['PR CI'])
    verify_channels(service.discord(), config)
    if args.command == 'migrate-legacy':
        from discord_threads import Api
        actual = Api('GitHub', 'https://api.github.com', os.environ['GITHUB_TOKEN'], 'Bearer').call('GET', '/repos/' + args.repo)
        if actual['full_name'].lower() != args.repo.lower():
            raise ValueError('Repository identity mismatch')
        existing = [b for b in service.registry.active() if b.auth == 'legacy']
        if existing:
            if len(existing) == 1 and existing[0].repo_id == actual['id'] and existing[0].config == config:
                print('Already migrated:', existing[0].id)
                return
            raise ValueError('A different legacy connection is already registered')
        identity = service.registry.add(args.tenant, actual['id'], actual['full_name'], 0, 'legacy', config, True)
    else:
        from registry import Binding
        binding = Binding('pending', args.tenant, args.repo_id, args.repo, args.installation, 'app', config, False)
        actual = service.github(binding).call('GET', '/repos/' + args.repo)
        if int(actual['id']) != args.repo_id:
            raise ValueError('Repository is not part of the requested installation')
        identity = service.registry.add(args.tenant, args.repo_id, actual['full_name'], args.installation, 'app', config)
    print('Connected:', identity)


if __name__ == '__main__':
    main()
