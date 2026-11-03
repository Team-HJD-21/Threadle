import argparse
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from runtime_logging import log_exception
from registry import Registry
from auth import InstallationTokens, RepositoryApi
from delivery import Queue, CachedThreads, COALESCE_SECONDS
from deferred_threads import DeferredThread
from alert_feed import AlertFeed
from discord_threads import Api, ApiError, route, make_embed, current_closed, Topic, silent_review, topic_from
from pr_routing import FileIndex, PrRouter
from team_notifications import TeamNotifications

EVENTS = {'issues', 'issue_comment', 'pull_request', 'pull_request_review',
          'pull_request_review_comment', 'discussion', 'discussion_comment', 'workflow_run', 'push'}
LOG = logging.getLogger('service')


class HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, *args, **kwargs):
        self.capacity = threading.BoundedSemaphore(64)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.capacity.acquire(blocking=False):
            try:
                request.settimeout(1)
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nRetry-After: 5\r\nConnection: close\r\n\r\n')
            finally:
                self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.capacity.release()
            raise

    def handle_error(self, request, client_address):
        log_exception(LOG,'HTTP worker failed')

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.capacity.release()


def verify_channels(api, config):
    threads = CachedThreads(api, config['guild_id'], config['channels'], None)
    threads.check()
    for group in config.get('routing_groups', []):
        group_threads = CachedThreads(api, config['guild_id'], {'pr': group['channel']}, None)
        group_threads.check()
    for channel in config['channels'].values():
        if api.call('GET', '/channels/' + channel).get('guild_id') != config['guild_id']:
            raise ValueError('Discord server mismatch')
    if config.get('alert_channel'):
        channel = api.call('GET', '/channels/' + config['alert_channel'])
        if channel.get('guild_id') != config['guild_id'] or channel.get('type') != 0:
            raise ValueError('Alert channel server mismatch')


class Service:
    def __init__(self, root, env=None, api_factory=Api, token_provider=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        media = self.root / 'vendor-pillow-13'
        if media.is_dir() and str(media) not in sys.path:
            sys.path.insert(0,str(media))
        self.env = os.environ if env is None else env
        self.registry = Registry(self.root / 'connections.sqlite3')
        from merge_conflicts import ConflictMonitor
        self.conflicts=ConflictMonitor(self)
        self.api_factory = api_factory
        self.tokens = token_provider
        if self.tokens is None and self.env.get('GITHUB_APP_ID'):
            self.tokens = InstallationTokens(self.env['GITHUB_APP_ID'], self.env['GITHUB_APP_PRIVATE_KEY_FILE'])
        self.queues = {}
        self.file_index = FileIndex(self.root / 'repository-files.sqlite3')
        from connect_web import Onboarding
        self.web = Onboarding.create(self)
        from discord_connect import DiscordConnect
        self.discord_connect = DiscordConnect.create(self)
        from operator_web import OperatorWeb
        self.operator = OperatorWeb(self)
        from guild_lifecycle import GuildLifecycle
        self.guilds = GuildLifecycle(self)

    def queue(self, binding):
        if binding.id not in self.queues:
            state = self.root if binding.legacy_state else self.root / 'connections' / binding.id
            state.mkdir(parents=True, exist_ok=True)
            self.queues[binding.id] = Queue(state / 'queue.sqlite3')
        return self.queues[binding.id]

    def github(self, binding):
        if binding.auth == 'app' and not self.tokens:
            raise RuntimeError('GitHub App credentials are not configured')
        return RepositoryApi(binding, self.tokens, self.env.get('GITHUB_TOKEN'), self.api_factory)

    def discord(self):
        return self.api_factory('Discord', 'https://discord.com/api/v10', self.env['DISCORD_BOT_TOKEN'], 'Bot')

    def receive(self, path, signature, event, delivery, body):
        mode = {'/github/webhook': 'legacy', '/github/app-webhook': 'app'}.get(path)
        if mode is None:
            return 404
        secret = self.env.get('WEBHOOK_SECRET' if mode == 'legacy' else 'GITHUB_APP_WEBHOOK_SECRET')
        if not secret:
            return 503
        digest = 'sha256=' + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not isinstance(signature, str) or not signature.isascii() or not hmac.compare_digest(digest, signature):
            return 403
        if not re.fullmatch(r'[a-fA-F0-9-]{16,64}', delivery):
            return 400
        payload = json.loads(body)
        if not isinstance(payload, dict):
            return 400
        if event == 'ping':
            return 200
        if event not in EVENTS | {'installation', 'installation_repositories'}:
            return 200
        if mode == 'legacy' and event not in EVENTS:
            return 200
        if event == 'workflow_run' and payload.get('action') not in ('requested', 'in_progress', 'completed'):
            return 200
        return self.registry.receive(mode, delivery, event, payload)

    def dispatch(self):
        for identity, delivery, event, raw, recipients in self.registry.pending():
            payload = json.loads(raw)
            with self.registry.db() as db:timing=db.execute('SELECT received FROM intake_timing WHERE id=?',(identity,)).fetchone()
            complete = True
            for binding_id in json.loads(recipients):
                binding = self.registry.get(binding_id, include_paused=True)
                if binding:
                    try:
                        self.queue(binding).put(delivery, event, payload,received=timing[0] if timing else None)
                        self.conflicts.signal(binding,delivery,event,payload)
                    except sqlite3.OperationalError:
                        complete = False
                        log_exception(LOG,'Fan-out will retry binding=%s delivery=%s', binding.id, delivery, level=logging.WARNING)
            if complete:
                self.registry.finish(identity)

    def process(self, binding, threads, feed, notices=None, window_seconds=0):
        queue = self.queue(binding)
        job = queue.next(window_seconds)
        if not job:
            return False
        grouped = queue.group(job,window_seconds)
        if grouped is None:
            return False
        job, group_ids = grouped
        delivery, event, raw, attempts = job
        from delivery_metrics import begin,end,deferred as mark_deferred
        timing_token=begin(queue,group_ids)
        try:
            current = self.registry.get(binding.id)
            if current is None or current != binding:
                return False
            payload = json.loads(raw)
            if payload.get('_silent'):
                queue.finish_group(group_ids)
                return True
            if int(payload['repository']['id']) != binding.repo_id or (
                binding.auth == 'app' and int(payload.get('installation', {}).get('id', 0)) != binding.installation):
                raise ValueError('Queued event connection scope mismatch')
            if event == 'threadle_bundle':
                for item in payload['_events']:
                    child=item['payload']
                    if int(child['repository']['id']) != binding.repo_id or (binding.auth == 'app' and int(child.get('installation',{}).get('id',0)) != binding.installation):
                        raise ValueError('Grouped event connection scope mismatch')
            names = binding.config.get('workflow_names', [])
            if event == 'workflow_run' and names and payload.get('workflow_run', {}).get('name') not in names:
                queue.finish(delivery)
                return True
            if silent_review(event, payload):
                queue.finish(delivery)
                return True
            github = self.github(binding)
            threads.media_fetcher = getattr(github, "fetch_media", None)
            actual = github.call('GET', f'/repositories/{binding.repo_id}')
            if int(actual['id']) != binding.repo_id:
                raise ValueError('Repository identity changed')
            canonical = actual.get('full_name', binding.repo)
            if canonical != binding.repo:
                self.registry.rename(binding.id, canonical)
                return False
            payload['repository']['full_name'] = binding.repo
            from commit_notifications import enrich_commits
            payload = enrich_commits(github,binding.repo,event,payload)
            route_event = ('pull_request' if payload.get('pull_request') else 'discussion' if payload.get('discussion') else 'issues') if event == 'threadle_bundle' else event
            if feed and event == 'workflow_run':
                feed.ci_detail(binding.repo, payload, 'webhook:' + delivery)
                (notices or feed).ci(binding.repo, payload, 'webhook:' + delivery)
            from dnd_notifications import delivery_mode
            dnd_until=delivery_mode(queue,delivery,event,binding.config.get('team_notifications',{}),time.time())
            topics = route(route_event, payload, github)
            for topic in topics:
                from root_status import TopicRoots
                roots=TopicRoots(threads,github)
                if roots.handle_deleted(topic,event,payload):continue
                from notification_layout import hydrate_issue_fields, resolve_changed_fields
                resolve_changed_fields(topic,payload,github)
                topic = hydrate_issue_fields(topic,payload,github)
                if topic.kind == 'pr' and not topic.metadata.get('head'):
                    with queue.connect() as db:
                        known = db.execute('SELECT 1 FROM links WHERE topic=?', (topic.key,)).fetchone()
                    if not known:
                        topic = topic_from(binding.repo, 'pr', github.call('GET', f'/repos/{binding.repo}/pulls/{topic.number}'))
                if event == 'threadle_bundle':
                    payload[{'pr':'pull_request','issue':'issue','discussion':'discussion'}[topic.kind]]=topic.metadata
                    queue.save_group(delivery,payload)
                if self.registry.get(binding.id) != binding:
                    return False
                destination, targets, reason = binding.config['channels'].get(topic.kind), [], ''
                routed = threads
                if topic.kind == 'pr' and binding.config.get('routing_groups'):
                    destination, targets, reason = PrRouter(queue, self.file_index).resolve(binding, topic, route_event, payload, github)
                    routed = CachedThreads(threads.api, binding.config['guild_id'], dict(binding.config['channels'], pr=destination), queue, binding.config.get('language', 'ko'))
                    routed.bot = threads.bot
                    routed.media_fetcher = getattr(github, "fetch_media", None)
                routed.silent_notifications=lambda:bool(delivery_mode(queue,delivery,event,binding.config.get('team_notifications',{}),time.time()))
                roots.threads=routed
                from deferred_threads import defer
                deferred=defer(routed,roots,topic,event,payload,delivery,binding.config.get('team_notifications',{}),time.time())
                if deferred=='gone':continue
                if deferred:
                    mark_deferred()
                    if notices:
                        location=roots.location(topic)
                        notices.topic(topic,event,payload,'webhook:'+delivery,location[1],[binding.config['alert_channel']],dnd_until=dnd_until)
                    return True
                with queue.connect() as db:had_thread=db.execute('SELECT 1 FROM links WHERE topic=?',(topic.key,)).fetchone()
                from message_edits import deliver_events
                thread = deliver_events(routed,topic,event,payload,'webhook:'+delivery,
                                        lambda topic=topic: roots.closed(topic),binding.config.get('language','ko'))
                if thread is None:continue
                if topic.kind=='pr' and not had_thread:self.conflicts.signal(binding,delivery+':thread-ready','pull_request',{'action':'opened'})
                if notices:
                    if event == 'threadle_bundle':
                        for index,item in enumerate(payload['_events']):
                            notices.observe(topic,item['event'],item['payload'],'webhook:'+delivery+':part:'+str(index))
                    else:
                        notices.observe(topic, event, payload, 'webhook:' + delivery)
                    channels = {g['channel'] for g in targets}
                    if not channels or destination == binding.config['channels']['pr']:
                        channels.add(binding.config['alert_channel'])
                    dnd_until=delivery_mode(queue,delivery,event,binding.config.get('team_notifications',{}),time.time())
                    notices.topic(topic, event, payload, 'webhook:' + delivery, thread, channels, dnd_until=dnd_until)
                elif feed:
                    feed.topic(topic, event, payload, 'webhook:' + delivery, thread)
            queue.finish_group(group_ids)
            from deferred_threads import finish
            finish(queue,delivery)
            LOG.info('Delivered binding=%s event=%s delivery=%s', binding.id, event, delivery)
        except DeferredThread:
            mark_deferred()
            LOG.info('Thread deferred until DND ends binding=%s delivery=%s',binding.id,delivery)
        except Exception as error:
            log_exception(LOG,'Retry binding=%s event=%s error=%s', binding.id, event, type(error).__name__)
            if isinstance(error,ApiError) and error.retry_after is not None:queue.retry(delivery,attempts,retry_after=error.retry_after)
            else:queue.retry(delivery, attempts)
        finally:
            end(timing_token)
        return True

    def run_binding(self, binding, stop):
        while not stop.is_set():
            try:
                api = self.discord()
                threads = CachedThreads(api, binding.config['guild_id'], binding.config['channels'], self.queue(binding), binding.config.get('language', 'ko'))
                threads.check()
                from deferred_threads import wake
                wake(self.queue(binding))
                feed = AlertFeed(api, binding.config, self.queue(binding)) if binding.config.get('alert_channel') else None
                if feed:
                    feed.check()
                options = binding.config.get('team_notifications', {})
                from digest_settings import enabled as digest_enabled
                enabled = (binding.config.get('routing_groups') or options.get('review_hours') or options.get('batch_seconds')
                           or options.get('quiet') or digest_enabled(options,'daily') or digest_enabled(options,'weekly'))
                runtime = TeamNotifications(binding, self.queue(binding), api)
                notices = runtime if enabled or runtime.has_pending() else None
                from connection_welcome import ConnectionWelcome
                welcome = ConnectionWelcome(self,binding,api,self.queue(binding))
                scheduled_stop = threading.Event()
                def scheduled(runtime=runtime, scheduled_stop=scheduled_stop):
                    while not stop.is_set() and not scheduled_stop.is_set() and self.registry.get(binding.id) == binding:
                        try:
                            github = self.github(binding)
                            if welcome.pending():
                                welcome.tick(github)
                            if notices:
                                runtime.tick(github)
                            elif not welcome.pending():
                                return
                        except Exception as error:
                            log_exception(LOG,'Scheduled notifications retry binding=%s error=%s', binding.id, type(error).__name__)
                        scheduled_stop.wait(1)
                scheduler = threading.Thread(target=scheduled, daemon=True) if notices or welcome.pending() else None
                if scheduler:
                    scheduler.start()
                try:
                    while not stop.is_set() and self.registry.get(binding.id) == binding:
                        if not self.process(binding, threads, feed, notices, window_seconds=COALESCE_SECONDS):
                            stop.wait(.2)
                finally:
                    scheduled_stop.set()
                    if scheduler:
                        scheduler.join(timeout=1)
                return
            except Exception as error:
                log_exception(LOG,'Connection retry binding=%s error=%s', binding.id, type(error).__name__)
                stop.wait(5)

    def run(self, stop):
        workers = {}
        from presence import Presence
        presence_worker = Presence(self).start(stop)
        self.guilds.start(stop)
        self.conflicts.start(stop)
        from state_backups import StateBackups
        StateBackups(self.root).start(stop)
        from channel_topics import ChannelTopics
        ChannelTopics(self).start(stop)
        from ci_channel_migration import CiChannelMigration
        CiChannelMigration(self).start(stop)
        from webhook_recovery import WebhookRecovery
        WebhookRecovery(self).start(stop)
        from data_retention import Retention
        Retention(self).start(stop)
        if self.discord_connect is not None:
            self.discord_connect.jobs.start(stop)
            from command_sync import CommandSync
            CommandSync(self,self.discord_connect.settings).start(stop)
        next_cleanup = 0
        try:
            while not stop.is_set():
                if time.monotonic() >= next_cleanup:
                    sessions = self.web.sessions if self.web is not None else self.discord_connect.sessions if self.discord_connect is not None else None
                    if sessions is not None:
                        sessions.cleanup()
                    next_cleanup = time.monotonic() + 60
                bindings = {b.id: b for b in self.registry.active()}
                for identity, (binding, thread, signal) in list(workers.items()):
                    if bindings.get(identity) != binding:
                        signal.set()
                    if not thread.is_alive():
                        del workers[identity]
                for identity, binding in bindings.items():
                    if identity not in workers:
                        signal = threading.Event()
                        thread = threading.Thread(target=self.run_binding, args=(binding, signal), daemon=True)
                        workers[identity] = binding, thread, signal
                        thread.start()
                self.dispatch()
                stop.wait(.2)
        except Exception:
            log_exception(LOG,'Coordinator stopped; receiver will exit with failure')
            raise
        finally:
            stop.set()
            for _, thread, signal in workers.values():
                signal.set()
            presence_worker.join(timeout=5)


def handler(service):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, *_):
            pass

        def respond(self, code, text, content_type='text/plain; charset=utf-8'):
            body = text.encode()
            self.send_response(code)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if urlsplit(self.path).path in ('/', '/robots.txt', '/sitemap.xml', '/favicon.ico') or urlsplit(self.path).path.startswith('/connect'):
                if service.web is not None:
                    return service.web.handle(self)
                return self.respond(503, 'Connection setup is not enabled yet')
            self.respond(200 if self.path == '/health' else 404,
                         'receiver-ready' if self.path == '/health' else 'not found')

        def do_POST(self):
            if urlsplit(self.path).path == '/discord/interactions':
                if service.discord_connect is None:
                    return self.respond(503, 'Discord command setup is not enabled yet')
                try:
                    length = int(self.headers.get('Content-Length', 0))
                    if self.headers.get('Transfer-Encoding') or not 0 < length <= 65536:
                        return self.respond(413, 'invalid length')
                    body = self.rfile.read(length)
                    if len(body) != length:
                        return self.respond(400, 'incomplete body')
                    status, result = service.discord_connect.receive(body, self.headers.get('X-Signature-Ed25519'),
                                                                    self.headers.get('X-Signature-Timestamp'))
                    return self.respond(status, json.dumps(result, ensure_ascii=False), 'application/json; charset=utf-8')
                except (ValueError, TypeError):
                    return self.respond(400, 'invalid interaction')
                except Exception as error:
                    log_exception(LOG,'Discord command unavailable error=%s', type(error).__name__)
                    return self.respond(503, 'retry later')
            if urlsplit(self.path).path.startswith('/connect/'):
                if service.web is not None:
                    return service.web.handle(self)
                return self.respond(503, 'Connection setup is not enabled yet')
            try:
                length = int(self.headers.get('Content-Length', 0))
                if self.headers.get('Transfer-Encoding') or not 0 < length <= 25 * 1024 * 1024:
                    return self.respond(413, 'invalid length')
                body = self.rfile.read(length)
                if len(body) != length:
                    return self.respond(400, 'incomplete body')
                code = service.receive(self.path, self.headers.get('X-Hub-Signature-256'),
                                       self.headers.get('X-GitHub-Event', ''), self.headers.get('X-GitHub-Delivery', ''), body)
                self.respond(code, 'accepted' if code in (200, 202) else 'rejected')
            except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
                self.respond(400, 'invalid payload')
            except Exception:
                log_exception(LOG,'Ingress unavailable')
                self.respond(503, 'retry later')
    return Handler


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, default=Path(__file__).parent)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--smoke-alerts', action='store_true')
    parser.add_argument('--binding')
    parser.add_argument('--port', type=int, default=8787)
    args = parser.parse_args()
    from runtime_logging import configure
    configure(args.state_dir)
    service = Service(args.state_dir)
    bindings = service.registry.active()
    LOG.info('Service initialized active_bindings=%s',len(bindings))
    if not bindings and service.web is None:
        raise RuntimeError('No active connections; migrate or register one with admin.py first')
    if args.check:
        if service.web is not None:
            service.web.check()
        for binding in bindings:
            verify_channels(service.discord(), binding.config)
            if int(service.github(binding).call('GET', '/repos/' + binding.repo)['id']) != binding.repo_id:
                raise ValueError('Repository identity mismatch')
        print('Verified active connections:', len(bindings))
        return
    if args.smoke or args.smoke_alerts:
        selected = [b for b in bindings if b.id == args.binding] if args.binding else [b for b in bindings if b.auth == 'legacy']
        if len(selected) != 1:
            raise ValueError('Select exactly one active binding for a smoke test')
        binding = selected[0]
        api = service.discord()
        english = binding.config.get('language') == 'en'
        threads = CachedThreads(api, binding.config['guild_id'], binding.config['channels'], service.queue(binding), binding.config.get('language', 'ko'))
        threads.check()
        feed = AlertFeed(api, binding.config, service.queue(binding)) if binding.config.get('alert_channel') else None
        key = 'service-smoke:' + str(uuid.uuid4())
        for kind in ('issue', 'pr', 'discussion'):
            topic = Topic(binding.repo, kind, 0, '[TEST] Notification thread check' if english else '[TEST] 알림 스레드 연결 확인', 'https://github.com/' + binding.repo)
            if args.smoke_alerts:
                if feed is None:
                    raise ValueError('No alert channel configured')
                feed.topic(topic, 'issue_comment', {'action': 'created'}, key, threads.ensure(topic)['id'])
            else:
                threads.deliver(topic, {'title': '[TEST] Service connection verification'}, key, lambda: True)
        print('Smoke passed for binding:', binding.id)
        return
    stop = threading.Event()
    server = HttpServer(('127.0.0.1', args.port), handler(service))
    LOG.info('HTTP receiver listening port=%s',server.server_address[1])
    failed = threading.Event()
    def coordinator():
        try:
            service.run(stop)
        except Exception:
            log_exception(LOG,'Coordinator failure is stopping HTTP receiver')
            failed.set()
            server.shutdown()
    threading.Thread(target=coordinator, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        stop.set()
        server.server_close()
    LOG.info('Receiver stopped coordinator_failed=%s',failed.is_set())
    if failed.is_set():
        raise RuntimeError('Coordinator failed; restart required')


if __name__ == '__main__':
    main()
