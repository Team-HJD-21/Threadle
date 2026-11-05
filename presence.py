import asyncio
import json
import logging
from runtime_logging import log_exception
import threading
import time


LOG = logging.getLogger('presence')
DEFAULT_TEXT = 'Delivering GitHub updates'


def settings(root):
    path = root / 'presence.json'
    values = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
    if not isinstance(values, dict):
        raise ValueError('Invalid presence settings')
    enabled = values.get('enabled', True)
    text = values.get('text', DEFAULT_TEXT)
    if text == 'GitHub updates | /connect':
        text = DEFAULT_TEXT
    status = values.get('status', 'online')
    if type(enabled) is not bool or not isinstance(text, str) or not text.strip() or not 1 <= len(text) <= 128 or any(ord(c) < 32 for c in text):
        raise ValueError('Invalid presence settings')
    if status not in ('online', 'idle', 'dnd'):
        raise ValueError('Invalid presence status')
    return enabled, text, status


class Presence:
    def __init__(self, service):
        self.service = service

    def state(self, status):
        try:
            target = self.service.root / 'presence-status.json'
            temporary = target.with_suffix('.new')
            temporary.write_text(json.dumps({'state': status, 'checked_at': time.time()}), encoding='utf-8')
            temporary.replace(target)
        except OSError:
            LOG.warning('Presence status could not be saved')

    async def session(self, stop, discord, text, status):
        intents = discord.Intents.none()
        intents.guilds = True
        bridge = getattr(self.service, 'comment_bridge', None)
        if bridge is not None:
            enabled = await asyncio.to_thread(bridge.discover_intent)
            intents.guild_messages = enabled
            intents.message_content = enabled
        client = discord.Client(intents=intents,
                                activity=discord.CustomActivity(name=text),
                                status=getattr(discord.Status, status))

        @client.event
        async def on_ready():
            self.state('connected')
            LOG.info('Discord presence connected')

        @client.event
        async def on_resumed():
            self.state('connected')

        @client.event
        async def on_disconnect():
            self.state('reconnecting')

        @client.event
        async def on_guild_remove(guild):
            await asyncio.to_thread(self.service.guilds.removed, guild)

        @client.event
        async def on_guild_update(before, after):
            if before.name != after.name:
                await asyncio.to_thread(self.service.registry.save_guild_name, str(after.id), after.name)

        @client.event
        async def on_message(message):
            if bridge is None or message.guild is None or message.author.bot or message.webhook_id:
                return
            try:
                await asyncio.to_thread(bridge.ingest, {
                    'id': str(message.id), 'guild_id': str(message.guild.id),
                    'channel_id': str(message.channel.id), 'author_id': str(message.author.id),
                    'author': {'bot': message.author.bot}, 'webhook_id': message.webhook_id,
                    'type': message.type.value, 'content': message.content,
                    'reference': ({'message_id': str(message.reference.message_id),
                                   'channel_id': str(message.reference.channel_id or message.channel.id),
                                   'guild_id': str(message.reference.guild_id or message.guild.id),
                                   'type': getattr(message.reference.type, 'value', 0)} if message.reference else None),
                    'mentions': [str(member.id) for member in message.mentions],
                    'embeds':[embed.to_dict() for embed in message.embeds],
                    'attachments': [{'url': a.url, 'filename': a.filename,'content_type':a.content_type} for a in message.attachments]})
            except Exception as error:
                log_exception(LOG, 'Comment intake failed error=%s', type(error).__name__, level=logging.WARNING)

        task = asyncio.create_task(client.start(self.service.env['DISCORD_BOT_TOKEN'], reconnect=True))
        last_status = 0
        try:
            while not stop.is_set() and not task.done():
                if time.monotonic() - last_status >= 30:
                    self.state('connected' if client.is_ready() else 'connecting')
                    last_status = time.monotonic()
                await asyncio.sleep(.25)
            if task.done():
                await task
        finally:
            await client.close()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def run(self, stop):
        try:
            enabled, text, status = settings(self.service.root)
        except (ValueError, TypeError, OSError):
            self.state('invalid-settings')
            LOG.warning('Presence settings are invalid; notifications remain available')
            return
        if not enabled or not self.service.env.get('DISCORD_BOT_TOKEN'):
            self.state('disabled')
            return
        try:
            import discord
        except ImportError:
            self.state('missing-dependency')
            LOG.warning('Presence dependency is missing; notifications remain available')
            return
        delay = 3
        while not stop.is_set():
            self.state('connecting')
            try:
                asyncio.run(self.session(stop, discord, text, status))
            except discord.LoginFailure:
                self.state('authentication-failed')
                LOG.error('Presence login failed; check the existing bot credentials')
                return
            except Exception as error:
                self.state('reconnecting')
                log_exception(LOG,'Presence retry error=%s', type(error).__name__, level=logging.WARNING)
            if not stop.is_set():
                stop.wait(delay)
                delay = min(60, delay * 2)
        self.state('offline')

    def start(self, stop):
        thread = threading.Thread(target=self.run, args=(stop,), daemon=True, name='discord-presence')
        thread.start()
        return thread
