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
        intents.guild_messages = True
        intents.guild_reactions = True
        intents.moderation = True
        bridge = getattr(self.service, 'comment_bridge', None)
        if bridge is not None:
            enabled = await asyncio.to_thread(bridge.discover_intent)
            intents.message_content = enabled
        client = discord.Client(intents=intents,
                                activity=discord.CustomActivity(name=text),
                                status=getattr(discord.Status, status))

        @client.event
        async def on_ready():
            self.state('connected')
            if getattr(client,'user',None):
                self.service.chat_events.bot_id = str(client.user.id)
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
            if message.guild is None:
                return
            try:
                await asyncio.to_thread(self.service.chat_events.record,'message-create',
                    str(message.guild.id),str(message.channel.id),str(message.id),
                    author=str(message.author.id),actor=str(message.author.id),
                    parent=getattr(message.channel,'parent_id',None))
                if bridge is None or message.author.bot or message.webhook_id:
                    return
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

        async def metadata(kind, payload, author=None, actor=None):
            if not payload.guild_id:
                return
            try:
                await asyncio.to_thread(self.service.chat_events.record,kind,str(payload.guild_id),
                    str(payload.channel_id),str(payload.message_id),author=author,actor=actor)
            except Exception as error:
                log_exception(LOG,'Chat event intake failed error=%s',type(error).__name__,level=logging.WARNING)

        @client.event
        async def on_raw_message_edit(payload):
            author = payload.data.get('author',{}).get('id')
            # A message's author is known; the actual editor is not in this event.
            await metadata('message-edit',payload,author=author)

        @client.event
        async def on_raw_message_delete(payload):
            if not payload.guild_id:
                return
            try:
                if bridge is not None:
                    await asyncio.to_thread(bridge.deletions.ingest,payload.guild_id,payload.channel_id,payload.message_id)
                else:
                    await metadata('message-delete',payload)
            except Exception as error:
                log_exception(LOG,'Comment deletion intake failed error=%s',type(error).__name__,level=logging.WARNING)

        @client.event
        async def on_raw_bulk_message_delete(payload):
            for identity in sorted(payload.message_ids):
                from types import SimpleNamespace
                await on_raw_message_delete(SimpleNamespace(guild_id=payload.guild_id,
                    channel_id=payload.channel_id,message_id=identity))

        @client.event
        async def on_raw_reaction_add(payload):
            await metadata('reaction-add',payload,actor=str(payload.user_id))

        @client.event
        async def on_raw_reaction_remove(payload):
            await metadata('reaction-remove',payload,author=str(payload.user_id))

        @client.event
        async def on_raw_reaction_clear(payload):
            await metadata('reactions-clear',payload)

        @client.event
        async def on_raw_reaction_clear_emoji(payload):
            await metadata('reaction-clear-emoji',payload)

        async def thread_event(kind,thread):
            if not thread.guild:
                return
            try:
                await asyncio.to_thread(self.service.chat_events.record,kind,str(thread.guild.id),str(thread.id),
                    parent=thread.parent_id)
            except Exception as error:
                log_exception(LOG,'Thread event intake failed error=%s',type(error).__name__,level=logging.WARNING)

        @client.event
        async def on_thread_create(thread):
            await thread_event('thread-create',thread)

        @client.event
        async def on_raw_thread_update(payload):
            try:
                await asyncio.to_thread(self.service.chat_events.record,'thread-update',str(payload.guild_id),
                    str(payload.thread_id),parent=payload.data.get('parent_id'))
            except Exception as error:
                log_exception(LOG,'Thread update intake failed error=%s',type(error).__name__,level=logging.WARNING)

        @client.event
        async def on_raw_thread_delete(payload):
            try:
                await asyncio.to_thread(self.service.chat_events.record,'thread-delete',str(payload.guild_id),
                    str(payload.thread_id),parent=payload.parent_id)
            except Exception as error:
                log_exception(LOG,'Thread deletion intake failed error=%s',type(error).__name__,level=logging.WARNING)

        @client.event
        async def on_audit_log_entry_create(entry):
            if entry.action.value not in (72,73):
                return
            try:
                channel = getattr(entry.extra,'channel',None)
                await asyncio.to_thread(self.service.chat_events.audit,{
                    'id':str(entry.id),'guild':str(entry.guild.id),
                    'channel':str(channel.id) if channel else None,
                    'actor':str(entry.user_id) if entry.user_id else None,
                    'target':str(entry.target.id) if entry.target else None,
                    'action':entry.action.value,'count':getattr(entry.extra,'count',None)})
            except Exception as error:
                log_exception(LOG,'Audit event intake failed error=%s',type(error).__name__,level=logging.WARNING)

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
