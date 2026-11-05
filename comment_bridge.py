"""Durable, opt-in Discord -> GitHub comments, with authenticated echo receipts.

Writes deliberately have no transport-level retries. An ambiguous POST is reconciled
by its signed marker; absence of a receipt is never treated as permission to repost.
"""
import copy
import datetime as dt
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen

from account_links import snowflake
from connect_auth import ConnectError
from discord_threads import ApiError

LOG = logging.getLogger('comment-bridge')
MARKER = re.compile(r'\n?<!-- threadle-comment:([a-f0-9]{32}):([a-f0-9]{64}) -->')
TOPIC = re.compile(r'^github-thread:([\w.-]+/[\w.-]+):(pr|issue|discussion):([1-9][0-9]*)$')


class UserApi:
    def __init__(self, service, token):
        self.service, self.token = service, token

    def call(self, method, path, data=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ValueError('Invalid GitHub API path')
        # Read-only operations can use the existing cooldown/backoff implementation.
        if method == 'GET':
            api = self.service.api_factory('GitHub', 'https://api.github.com', self.token, 'Bearer')
            api.usage_observer = self.service.api_usage.observe
            return api.call(method, path, data)
        request = Request('https://api.github.com' + path, method=method,
                          data=json.dumps(data).encode(), headers={
            'Authorization': 'Bearer ' + self.token, 'User-Agent': 'Threadle/1.0',
            'Content-Type': 'application/json', 'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28'})
        try:
            with urlopen(request, timeout=20) as response:
                self.service.api_usage.observe(method, path, response.headers)
                raw = response.read(2_000_001)
                if len(raw) > 2_000_000:
                    raise ApiError('GitHub', 'network')
                return json.loads(raw)
        except HTTPError as error:
            self.service.api_usage.observe(method, path, error.headers, error.code)
            wait = error.headers.get('Retry-After')
            if error.code == 403 and error.headers.get('X-RateLimit-Remaining') == '0':
                try:
                    wait = max(1, float(error.headers.get('X-RateLimit-Reset', 0)) - time.time())
                except (ValueError, TypeError):
                    wait = 60
            error.close()
            raise ApiError('GitHub', error.code, retry_after=wait) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise ApiError('GitHub', 'network') from None


class CommentBridge:
    def __init__(self, service, accounts, clock=time.time, user_api=UserApi):
        self.service, self.accounts, self.clock, self.user_api = service, accounts, clock, user_api
        self.worker_lock = threading.Lock()
        self.content_enabled = False
        from comment_replies import Replies
        self.replies = Replies(self)

    def discover_intent(self):
        """Only request the privileged intent when the application actually has it."""
        try:
            application = self.service.discord().call('GET', '/oauth2/applications/@me')
            flags = int(application.get('flags', 0))
            self.content_enabled = bool(flags & ((1 << 18) | (1 << 19)))
        except Exception:
            self.content_enabled = False
        if not self.content_enabled:
            LOG.warning('Comment bridge awaits Discord Message Content Intent; existing notifications stay available')
        return self.content_enabled

    def destination(self, guild, thread):
        found = []
        for binding in self.service.registry.active():
            if binding.auth != 'app' or binding.config['guild_id'] != str(guild):
                continue
            with self.service.queue(binding).connect() as db:
                rows = db.execute('SELECT topic,channel FROM links WHERE thread=?', (str(thread),)).fetchall()
            for topic, parent in rows:
                match = TOPIC.fullmatch(topic)
                if match and match[1].casefold() == binding.repo.casefold():
                    found.append((binding, topic, str(parent), match[2], int(match[3])))
        return found[0] if len(found) == 1 else None

    def ingest(self, message):
        """Gateway callback stores the event quickly; no external calls on this path."""
        if message.get('type', 0) not in (0, 19) or message.get('webhook_id') or message.get('author', {}).get('bot'):
            return False
        identity, guild, thread, author = (str(message.get(k, '')) for k in ('id', 'guild_id', 'channel_id', 'author_id'))
        if not all(snowflake(v) for v in (identity, guild, thread, author)):
            return False
        account = self.accounts.get(author, guild)
        if not account or not account['comments'] or not account['authorized']:
            return False
        target = self.destination(guild, thread)
        if not target:
            return False
        content = message.get('content', '')
        attachments = message.get('attachments', [])
        if not isinstance(content, str) or len(content) > 4000 or not isinstance(attachments, list) or len(attachments) > 10:
            return False
        files = []
        for item in attachments:
            url = item.get('url', '')
            parsed = urlsplit(url)
            from comment_media import allowed,CDN
            if not allowed(url,CDN):
                return False
            files.append({'url': url, 'filename': str(item.get('filename', 'attachment'))[:200],
                          'content_type':str(item.get('content_type') or '')[:100]})
        if not content.strip() and not files:
            return False
        from comment_replies import reference
        payload = {'content': content, 'attachments': files, 'reference': reference(message)}
        embeds=message.get('embeds',[])
        if isinstance(embeds,list):
            # Keep only bounded, relevant media metadata in the encrypted outbox.
            payload['embeds']=[{key:item[key] for key in ('type','url','image','thumbnail') if key in item}
                               for item in embeds[:10] if isinstance(item,dict) and item.get('type') in ('gifv','image','video')]
            if len(json.dumps(payload['embeds']))>50000:return False
        if 'mentions' in message:
            mentions = message['mentions']
            if not isinstance(mentions, list) or len(mentions) > 100 or not all(snowflake(v) for v in mentions):
                return False
            payload['mentions'] = [str(v) for v in mentions]
        with self.accounts.lock, self.accounts.db() as db:
            # Recheck after acquiring the disconnect lock.
            current = db.execute('SELECT generation FROM personal_accounts WHERE discord=? AND credential IS NOT NULL', (author,)).fetchone()
            consent = db.execute('SELECT comments FROM personal_guilds WHERE discord=? AND guild=?', (author, guild)).fetchone()
            if not current or current[0] != account['generation'] or not consent or not consent[0]:
                return False
            if db.execute("SELECT count(*) FROM comment_outbox WHERE state IN ('queued','sending','uncertain')").fetchone()[0] >= 1000:
                raise RuntimeError('Comment forwarding queue is full')
            result = db.execute('INSERT OR IGNORE INTO comment_outbox(id,binding,discord,github,generation,guild,thread,topic,operation,payload,created) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                       (identity, target[0].id, author, account['github'], account['generation'], guild, thread,
                        target[1], secrets.token_hex(16), self.accounts.encode(payload), self.clock()))
        return bool(result.rowcount)

    def signature(self, operation):
        return hmac.new(self.accounts.settings.session_key.encode(), ('threadle-comment:' + operation).encode(), hashlib.sha256).hexdigest()

    def marker(self, operation):
        return '\n<!-- threadle-comment:' + operation + ':' + self.signature(operation) + ' -->'

    def echo(self, binding, event, payload):
        payload.pop('_bridge_review_echo', None)
        if event not in ('issue_comment', 'discussion_comment', 'pull_request_review_comment'):
            return False
        comment = payload.get('comment', {})
        body = comment.get('body', '')
        match = MARKER.search(body) if isinstance(body, str) else None
        if not match or not hmac.compare_digest(match[2], self.signature(match[1])):
            return False
        if event == 'pull_request_review_comment':
            number = (payload.get('pull_request') or {}).get('number', 0)
            identity = self.review_receipt(binding, number, None, comment, match, webhook=True)
            if identity:
                comment['body'] = MARKER.sub('', body)
                payload['_bridge_review_operation'] = identity
                with self.accounts.db() as db:
                    origin = db.execute('SELECT binding FROM comment_outbox WHERE operation=?', (identity,)).fetchone()
                payload['_bridge_review_echo'] = bool(origin and origin[0] == binding.id)
            # Review replies must reach the aggregate card instead of being swallowed as echoes.
            return False
        with self.accounts.db() as db:
            row = db.execute('SELECT binding,github,topic,state,remote FROM comment_outbox WHERE operation=?', (match[1],)).fetchone()
        number = (payload.get('issue') or payload.get('discussion') or {}).get('number')
        topic = TOPIC.fullmatch(row[2]) if row else None
        identity = str(comment.get('id', ''))
        original = self.service.registry.get(row[0]) if row else None
        if (not row or not topic or not original or original.repo_id != binding.repo_id or
                int(number or 0) != int(topic[3]) or str(comment.get('user', {}).get('id')) != row[1] or
                (event == 'discussion_comment') != (topic[2] == 'discussion') or
                row[4] is not None and row[4] != identity):
            return False
        # Remove only our authenticated marker before normal forwarding to OTHER servers.
        comment['body'] = MARKER.sub('', body)
        if payload.get('action') != 'created':
            return False
        # A valid receipt can arrive after disconnect canceled an uncertain row,
        # or after a backup restored its pre-send state. Never echo those either.
        with self.accounts.db() as db:
            db.execute("UPDATE comment_outbox SET state='done',remote=?,url=?,payload=NULL,error=NULL WHERE operation=? AND state<>'done'",
                       (identity, comment.get('html_url', ''), match[1]))
        return row[0] == binding.id

    def review_receipt(self, binding, number, node, comment, match, webhook=False):
        if not hmac.compare_digest(match[2], self.signature(match[1])):
            return None
        with self.accounts.db() as db:
            row = db.execute('SELECT o.binding,o.github,o.topic,o.remote,r.root,r.node FROM comment_outbox o JOIN bridge_replies r USING(operation) WHERE o.operation=? AND r.mode=\'review\'', (match[1],)).fetchone()
        original = self.service.registry.get(row[0]) if row else None
        actor = str((comment.get('user') or {}).get('id')) if webhook else str((comment.get('author') or {}).get('databaseId'))
        identity = str(comment.get('id') if webhook else comment.get('databaseId'))
        topic = TOPIC.fullmatch(row[2]) if row else None
        if (not row or not original or original.repo_id != binding.repo_id or not topic or topic[2] != 'pr' or
                int(topic[3]) != int(number or 0) or actor != row[1] or not identity.isdigit() or
                row[3] is not None and row[3] != identity or
                webhook and str(comment.get('in_reply_to_id')) != row[4] or
                not webhook and node != row[5]):
            return None
        with self.accounts.db() as db:
            db.execute("UPDATE comment_outbox SET state='done',remote=?,url=COALESCE(?,url),payload=NULL,error=NULL WHERE operation=?",
                       (identity, comment.get('html_url') if webhook else comment.get('url'), match[1]))
        return match[1]

    def body(self, job, payload, api=None):
        content = self.replies.mentions(api, job, payload) if api is not None else payload['content']
        content = content.replace('@everyone', '@\u200beveryone').replace('@here', '@\u200bhere')
        # Never accept a user-supplied receipt, even a copied legitimate one.
        content = MARKER.sub('', content)
        from comment_media import render
        content=render(content,payload)
        return content.strip() + self.marker(job['operation'])

    def check_discord(self, job, binding, parent):
        api = self.service.discord()
        member = api.call('GET', f"/guilds/{job['guild']}/members/{job['discord']}")
        if str(member.get('user', {}).get('id')) != job['discord']:
            raise ConnectError('Discord membership changed.', 403)
        thread = api.call('GET', '/channels/' + job['thread'])
        if (str(thread.get('guild_id')) != job['guild'] or str(thread.get('parent_id')) != parent or thread.get('type') != 11 or
                thread.get('thread_metadata', {}).get('locked')):
            raise ConnectError('The connected thread is unavailable.', 403)
        message = api.call('GET', f"/channels/{job['thread']}/messages/{job['id']}")
        if str(message.get('author', {}).get('id')) != job['discord'] or message.get('webhook_id') or message.get('author', {}).get('bot'):
            raise ConnectError('Discord message ownership changed.', 403)
        from connect_auth import channel_permissions
        channel = api.call('GET', '/channels/' + parent)
        roles = api.call('GET', f"/guilds/{job['guild']}/roles")
        permissions = channel_permissions(job['guild'], member, roles, channel)
        if permissions & (1 << 10) == 0 or permissions & (1 << 38) == 0:
            raise ConnectError('You can no longer post in this thread.', 403)
        return message

    def discussion(self, api, binding, number, after=None):
        owner, name = binding.repo.split('/')
        result = api.call('POST', '/graphql', {'query': '''query($owner:String!,$name:String!,$number:Int!,$after:String){
          repository(owner:$owner,name:$name){databaseId discussion(number:$number){id number locked
          comments(first:100,after:$after){nodes{databaseId body url author{... on User{databaseId}}}
          pageInfo{hasNextPage endCursor}}}}}''',
          'variables': {'owner': owner, 'name': name, 'number': number, 'after': after}})
        repository = result.get('data', {}).get('repository')
        if result.get('errors') or not repository or repository.get('databaseId') != binding.repo_id or not repository.get('discussion'):
            raise ConnectError('Discussion access is unavailable.', 403)
        return repository['discussion']

    def reconcile(self, api, binding, job, kind, number):
        marker = self.marker(job['operation']).strip()
        plan = self.replies.plan(job)
        if kind == 'discussion':
            cursor = None
            for _ in range(20):
                discussion = self.discussion(api, binding, number, cursor)
                for comment in discussion['comments']['nodes']:
                    if marker in comment.get('body', '') and str((comment.get('author') or {}).get('databaseId')) == job['github']:
                        return str(comment['databaseId']), comment['url']
                page = discussion['comments']['pageInfo']
                if not page['hasNextPage']:
                    return None
                cursor = page['endCursor']
        else:
            since = dt.datetime.fromtimestamp(job['created'] - 300, dt.timezone.utc).isoformat().replace('+00:00', 'Z')
            for page in range(1, 21):
                section = 'pulls' if plan['mode'] == 'review' else 'issues'
                comments = api.call('GET', '/repos/' + binding.repo + f'/{section}/{number}/comments?' + urlencode({'since': since, 'per_page': 100, 'page': page}))
                for comment in comments:
                    if (marker in comment.get('body', '') and str(comment.get('user', {}).get('id')) == job['github'] and
                            (plan['mode'] != 'review' or str(comment.get('in_reply_to_id')) == plan['root'])):
                        return str(comment['id']), comment['html_url']
                if len(comments) < 100:
                    return None
        raise ConnectError('Comment receipt search exceeded its bounded range.', 409)

    def update(self, job, state, error=None, remote=None, url=None):
        with self.accounts.db() as db:
            db.execute('UPDATE comment_outbox SET state=?,error=?,remote=COALESCE(?,remote),url=COALESCE(?,url),attempts=attempts+1,next=?,payload=CASE WHEN ? IN (\'done\',\'failed\') THEN NULL ELSE payload END WHERE id=? AND state<>\'done\'',
                       (state, error, remote, url, self.clock() + min(300, 5 * 2 ** min(job['attempts'], 6)), state, job['id']))

    def tick(self):
        if not self.worker_lock.acquire(blocking=False):
            return False
        try:
            result = self._tick()
            self.replies.cleanup()
            return result
        finally:
            self.worker_lock.release()

    def _tick(self):
        with self.accounts.db() as db:
            db.row_factory = __import__('state_db').Row
            row = db.execute("SELECT * FROM comment_outbox WHERE state IN ('queued','sending','uncertain') AND next<=? ORDER BY created,id LIMIT 1", (self.clock(),)).fetchone()
            job = dict(row) if row else None
        if not job:
            self.feedback()
            return False
        if job['created'] < self.clock() - 86400:
            self.update(job, 'failed', 'expired')
            self.feedback()
            return True
        sending = job['state'] != 'queued'
        try:
            with self.accounts.lock:
                account = self.accounts.get(job['discord'], job['guild'])
                target = self.destination(job['guild'], job['thread'])
                if (not account or not account['comments'] or account['generation'] != job['generation'] or not target or
                        target[0].id != job['binding'] or target[1] != job['topic']):
                    raise ConnectError('Account or repository connection changed.', 403)
                binding, _, parent, kind, number = target
                api = self.user_api(self.service, self.accounts.token(job['discord'], job['generation']))
                if str(api.call('GET', '/user').get('id')) != job['github']:
                    raise ConnectError('GitHub account identity changed.', 403)
                actual = api.call('GET', f'/repositories/{binding.repo_id}')
                if actual.get('id') != binding.repo_id or actual.get('full_name', '').casefold() != binding.repo.casefold():
                    raise ConnectError('Repository access changed.', 403)
                if sending:
                    found = self.reconcile(api, binding, job, kind, number)
                    if found:
                        self.update(job, 'done', remote=found[0], url=found[1])
                    else:
                        self.update(job, 'failed' if job['attempts'] >= 5 else 'uncertain', 'delivery-uncertain')
                    return True
                current_message=self.check_discord(job, binding, parent)
                saved = self.accounts.decode(job['payload'])
                plan, quoted = self.replies.prepare(api, binding, job, saved)
                # A restored backup can contain a pre-send row for an already posted comment.
                found = self.reconcile(api, binding, job, kind, number)
                if found:
                    self.update(job, 'done', remote=found[0], url=found[1])
                    return True
                from comment_media import prepare
                saved=prepare(saved,current_message)
                body = self.replies.quoted(self.body(job, saved, api), quoted)
                discussion = self.discussion(api, binding, number) if kind == 'discussion' else None
                if discussion is not None and discussion.get('locked'):
                    raise ConnectError('This discussion is locked.', 403)
                if kind != 'discussion':
                    issue = api.call('GET', f'/repos/{binding.repo}/issues/{number}')
                    if issue.get('number') != number or bool(issue.get('pull_request')) != (kind == 'pr') or issue.get('locked'):
                        raise ConnectError('This issue or PR is unavailable for comments.', 403)
                if self.service.registry.get(binding.id) != binding:
                    raise ConnectError('Repository connection changed.', 403)
                # A durable intent exists before POST, so an early webhook can match it.
                self.update(job, 'sending')
                sending = True
                if kind == 'discussion':
                    result = api.call('POST', '/graphql', {'query': '''mutation($input:AddDiscussionCommentInput!){
                      addDiscussionComment(input:$input){comment{databaseId url}}}''', 'variables': {'input': {
                        'discussionId': discussion['id'], 'body': body, 'clientMutationId': job['operation']}}})
                    if result.get('errors'):
                        raise ApiError('GitHub', 'network')
                    comment = result['data']['addDiscussionComment']['comment']
                    remote, url = str(comment['databaseId']), comment['url']
                elif plan['mode'] == 'review':
                    comment = api.call('POST', f'/repos/{binding.repo}/pulls/{number}/comments/{plan["root"]}/replies', {'body': body})
                    remote, url = str(comment['id']), comment['html_url']
                else:
                    comment = api.call('POST', f'/repos/{binding.repo}/issues/{number}/comments', {'body': body})
                    remote, url = str(comment['id']), comment['html_url']
                self.update(job, 'done', remote=remote, url=url)
        except ConnectError:
            self.update(job, 'failed', 'delivery-uncertain' if job['state'] != 'queued' else 'authorization-required')
        except ApiError as error:
            status = error.status
            if status == 429 or status == 403 and error.retry_after is not None:
                # A rate-limited receipt lookup must never turn an ambiguous POST
                # back into a queued fresh write.
                unknown = job['state'] != 'queued'
                self.update(job, 'failed' if job['attempts'] >= 5 else 'uncertain' if unknown else 'queued',
                            'delivery-uncertain' if unknown else 'temporarily-unavailable')
                try:
                    delay = min(86400, max(1, float(error.retry_after)))
                except (TypeError, ValueError):
                    delay = 60
                with self.accounts.db() as db:
                    db.execute('UPDATE comment_outbox SET next=MAX(next,?) WHERE id=?', (self.clock() + delay, job['id']))
            elif status in (401, 403, 404, 410, 422):
                self.update(job, 'failed', 'delivery-uncertain' if job['state'] != 'queued' else 'authorization-required')
                if status == 401:
                    self.accounts.disconnect(github=job['github'])
            elif sending:
                self.update(job, 'uncertain', 'delivery-uncertain')
            else:
                self.update(job, 'failed' if job['attempts'] >= 5 else 'queued', 'temporarily-unavailable')
        except Exception as error:
            LOG.warning('Comment bridge retry error=%s', type(error).__name__)
            self.update(job, 'uncertain' if sending else 'failed', 'delivery-uncertain' if sending else 'temporarily-unavailable')
        self.feedback()
        return True

    def feedback(self):
        with self.accounts.db() as db:
            row = db.execute("SELECT id,thread,state,error,url,binding FROM comment_outbox WHERE state IN ('done','failed') AND notified=0 ORDER BY created LIMIT 1").fetchone()
        if not row:
            return
        identity, thread, state, error, url, binding_id = row
        try:
            # Reaction failures never trigger another GitHub write.
            emoji = '\u2705' if state == 'done' else '\u26a0\ufe0f'
            api = self.service.discord()
            reaction_failed = False
            try:
                api.call('PUT', f'/channels/{thread}/messages/{identity}/reactions/{quote(emoji, safe="")}/@me')
            except ApiError as problem:
                reaction_failed = problem.status == 403
            if state == 'failed' or reaction_failed:
                binding = self.service.registry.get(binding_id)
                english = binding is not None and binding.config.get('language') == 'en'
                if state == 'done':
                    text = ('Your comment was posted on GitHub.' if english else 'GitHub에 댓글을 등록했어요.')
                elif error == 'delivery-uncertain':
                    text = ('GitHub comment delivery could not be confirmed. Check GitHub before posting again.' if english else
                            'GitHub 댓글 등록 여부를 확인하지 못했어요. 중복 작성을 막기 위해 다시 보내지 않았어요. GitHub에서 확인한 뒤 다시 작성해 주세요.')
                else:
                    text = ('GitHub comment was not sent. Check /account status, repository access, and GitHub App permissions.' if english else
                            'GitHub 댓글을 보내지 못했어요. /account status와 저장소 접근 권한, GitHub App의 댓글 작성 권한을 확인해 주세요.')
                api.call('POST', '/channels/' + thread + '/messages', {'content': text,
                         'message_reference': {'message_id': identity, 'fail_if_not_exists': False},
                         'allowed_mentions': {'parse': []}, 'flags': 1 << 12,
                         'nonce': str(int(hashlib.sha256(('bridge:' + identity).encode()).hexdigest()[:16], 16)), 'enforce_nonce': True})
        except ApiError:
            pass
        with self.accounts.db() as db:
            db.execute('UPDATE comment_outbox SET notified=1 WHERE id=?', (identity,))

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick()
                with self.accounts.db() as db:
                    db.execute('DELETE FROM mention_receipts WHERE created<?', (self.clock() - 30 * 86400,))
                    # Keep successful echo receipts for recovery; discard message bodies promptly.
                    db.execute("UPDATE comment_outbox SET payload=NULL,state='failed',error='expired' WHERE created<? AND state IN ('queued','sending','uncertain')", (self.clock() - 86400,))
                    db.execute("DELETE FROM comment_outbox WHERE state IN ('done','failed') AND created<?", (self.clock() - 30 * 86400,))
                    db.execute('DELETE FROM bridge_replies WHERE NOT EXISTS(SELECT 1 FROM comment_outbox o WHERE o.operation=bridge_replies.operation)')
            except Exception as error:
                LOG.warning('Comment worker retry error=%s', type(error).__name__)
            stop.wait(.5)

    def start(self, stop):
        worker = threading.Thread(target=self.run, args=(stop,), name='comment-bridge', daemon=True)
        worker.start()
        return worker
