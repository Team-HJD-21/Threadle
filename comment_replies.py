"""Resolve Discord replies to scoped GitHub objects; delete inputs only after rendering.

Reply bodies stay in the encrypted outbox. This table stores only routing and
confirmation identifiers in the already encrypted, backed-up registry database.
"""
import copy
import hashlib
import hmac
import json
import re
from urllib.parse import urlsplit

from connect_auth import ConnectError
from discord_threads import ApiError, nonce
from account_links import snowflake


CODE_OR_MENTION = re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|<@!?([0-9]+)>')


def reference(message):
    value = message.get('reference')
    if value is None:
        return None
    if not isinstance(value, dict) or value.get('type', 0) != 0:
        raise ValueError('Unsupported Discord message reference')
    result = {key: str(value.get(key) or message.get(fallback, '')) for key, fallback in (
        ('message_id', '_no_message'), ('channel_id', 'channel_id'), ('guild_id', 'guild_id'))}
    if (not all(snowflake(v) for v in result.values()) or result['channel_id'] != str(message['channel_id']) or
            result['guild_id'] != str(message['guild_id']) or result['message_id'] == str(message['id'])):
        raise ValueError('Reply target must be in the same connected thread')
    return result


class Replies:
    def __init__(self, bridge):
        self.bridge, self.accounts, self.service = bridge, bridge.accounts, bridge.service
        with self.accounts.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS bridge_replies(operation TEXT PRIMARY KEY,mode TEXT NOT NULL,reference TEXT NOT NULL,root TEXT,node TEXT,applied INTEGER NOT NULL DEFAULT 0,cleaned INTEGER NOT NULL DEFAULT 0,next REAL NOT NULL DEFAULT 0,attempts INTEGER NOT NULL DEFAULT 0)')
            if 'input_hash' not in {row[1] for row in db.execute('PRAGMA table_info(bridge_replies)')}:
                db.execute('ALTER TABLE bridge_replies ADD COLUMN input_hash TEXT')

    def fingerprint(self, value):
        files = []
        for item in value.get('attachments', []):
            parsed = urlsplit(item.get('url', ''))
            files.append((parsed.hostname, parsed.path, item.get('filename', '')))
        raw = json.dumps({'content': value.get('content'), 'files': sorted(files)}, ensure_ascii=False, sort_keys=True)
        return hmac.new(self.accounts.settings.session_key.encode(), ('discord-reply:' + raw).encode(), hashlib.sha256).hexdigest()

    def plan(self, job):
        with self.accounts.db() as db:
            row = db.execute('SELECT mode,reference,root,node FROM bridge_replies WHERE operation=?', (job['operation'],)).fetchone()
        return dict(zip(('mode', 'reference', 'root', 'node'), row)) if row else {'mode': 'comment'}

    def save(self, job, plan):
        with self.accounts.db() as db:
            db.execute('INSERT OR IGNORE INTO bridge_replies(operation,mode,reference,root,node,input_hash) VALUES(?,?,?,?,?,?)',
                       (job['operation'], plan['mode'], plan['reference'], plan.get('root'), plan.get('node'), plan.get('input_hash')))

    def mentions(self, api, job, payload):
        """Use actual Discord mention IDs, never guess people from display names."""
        allowed = payload.get('mentions')
        cache = {}
        def convert(match):
            discord = match[1]
            if discord is None or allowed is not None and discord not in allowed:
                return match[0]
            if discord not in cache:
                linked = self.accounts.get(discord, job['guild'])
                login = None
                if linked and (linked['mentions'] or linked['comments']):
                    try:
                        member = self.service.discord().call('GET', f"/guilds/{job['guild']}/members/{discord}")
                        profile = api.call('GET', '/user/' + linked['github'])
                        candidate = profile.get('login', '')
                        if (str(member.get('user', {}).get('id')) == discord and str(profile.get('id')) == linked['github'] and
                                re.fullmatch(r'[A-Za-z0-9-]{1,39}', candidate)):
                            login = candidate
                            with self.accounts.db() as db:
                                db.execute('UPDATE personal_accounts SET login=? WHERE discord=? AND github=?', (login, discord, linked['github']))
                    except ApiError:
                        pass
                cache[discord] = login
            return '@' + cache[discord] if cache[discord] else match[0].replace('<', '&lt;').replace('>', '&gt;')
        return CODE_OR_MENTION.sub(convert, payload['content'])

    @staticmethod
    def api_scope(value, binding, kind, number):
        parsed = urlsplit(str(value))
        return (parsed.scheme == 'https' and parsed.netloc == 'api.github.com' and not parsed.query and not parsed.fragment and
                parsed.path.casefold() == f'/repos/{binding.repo}/{kind}/{number}'.casefold())

    def review(self, api, binding, job, identity, node=None):
        number = int(job['topic'].rsplit(':', 1)[1])
        value = api.call('GET', f'/repos/{binding.repo}/pulls/comments/{identity}')
        if str(value.get('id')) != str(identity) or not self.api_scope(value.get('pull_request_url'), binding, 'pulls', number):
            raise ConnectError('Review reply target is outside this PR.', 403)
        root = str(value.get('in_reply_to_id') or identity)
        if root != str(identity):
            value = api.call('GET', f'/repos/{binding.repo}/pulls/comments/{root}')
            if str(value.get('id')) != root or value.get('in_reply_to_id') or not self.api_scope(value.get('pull_request_url'), binding, 'pulls', number):
                raise ConnectError('Review root does not match this PR.', 403)
        if node is None:
            from discord_threads import Topic
            from review_conversations import snapshot
            topic = Topic(binding.repo, 'pr', number, '', f'https://github.com/{binding.repo}/pull/{number}')
            for thread in snapshot(api, topic):
                if any(str(c.get('databaseId')) == root for c in thread['comments']):
                    node = thread['id']
                    break
        if not node:
            raise ConnectError('This review conversation is no longer available.', 404)
        return root, node

    def discussion_quote(self, api, binding, number, identity=None):
        """Bounded, on-demand lookup, including paginated discussion replies."""
        owner, name = binding.repo.split('/')
        after = None
        requests = 0
        def query(data):
            nonlocal requests
            if requests >= 20:
                raise ConnectError('Discussion quote lookup exceeded its range.', 409)
            requests += 1
            return api.call('POST', '/graphql', data)
        for _ in range(20):
            result = query({'query': '''query($owner:String!,$name:String!,$number:Int!,$after:String){
              repository(owner:$owner,name:$name){databaseId discussion(number:$number){id body url author{login}
              comments(first:100,after:$after){nodes{id databaseId body url author{login}
              replies(first:100){nodes{databaseId body url author{login}} pageInfo{hasNextPage endCursor}}}
              pageInfo{hasNextPage endCursor}}}}}''',
              'variables': {'owner': owner, 'name': name, 'number': number, 'after': after}})
            repository = (result.get('data') or {}).get('repository') or {}
            value = repository.get('discussion')
            if result.get('errors') or repository.get('databaseId') != binding.repo_id or not value:
                raise ConnectError('Discussion quote is unavailable.', 403)
            if identity is None:
                return value.get('body') or '', (value.get('author') or {}).get('login', 'GitHub'), value['url']
            for comment in value['comments']['nodes']:
                if str(comment.get('databaseId')) == identity:
                    return comment['body'], (comment.get('author') or {}).get('login', 'GitHub'), comment['url']
                connection = comment['replies']
                for page in range(20):
                    for reply in connection['nodes']:
                        if str(reply.get('databaseId')) == identity:
                            return reply['body'], (reply.get('author') or {}).get('login', 'GitHub'), reply['url']
                    info = connection['pageInfo']
                    if not info.get('hasNextPage'):
                        break
                    if page == 19 or not info.get('endCursor'):
                        raise ConnectError('Discussion quote lookup exceeded its range.', 409)
                    extra = query({'query': '''query($id:ID!,$cursor:String!){node(id:$id){... on DiscussionComment{
                      discussion{number repository{databaseId}} replies(first:100,after:$cursor){nodes{databaseId body url author{login}}
                      pageInfo{hasNextPage endCursor}}}}}''', 'variables': {'id': comment['id'], 'cursor': info['endCursor']}})
                    node = (extra.get('data') or {}).get('node') or {}
                    scope = node.get('discussion') or {}
                    if extra.get('errors') or scope.get('number') != number or (scope.get('repository') or {}).get('databaseId') != binding.repo_id:
                        raise ConnectError('Discussion reply quote scope mismatch.', 403)
                    connection = node['replies']
            info = value['comments']['pageInfo']
            if not info.get('hasNextPage'):
                break
            if not info.get('endCursor') or info['endCursor'] == after:
                raise ConnectError('Discussion quote pagination did not advance.', 409)
            after = info['endCursor']
        raise ConnectError('The quoted GitHub comment is no longer available.', 404)

    def quote(self, api, binding, job, url):
        from comment_bridge import TOPIC
        topic = TOPIC.fullmatch(job['topic'])
        kind, number = topic[2], int(topic[3])
        parsed = urlsplit(url)
        section = {'pr': 'pull', 'issue': 'issues', 'discussion': 'discussions'}[kind]
        if (parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.query or
                parsed.path.casefold() != f'/{binding.repo}/{section}/{number}'.casefold()):
            raise ConnectError('Reply target belongs to another GitHub item.', 403)
        fragment = parsed.fragment
        if kind == 'discussion':
            match = re.fullmatch(r'discussioncomment-([1-9][0-9]*)', fragment) if fragment else None
            if fragment and match is None:
                raise ConnectError('Unsupported discussion reply target.', 400)
            return self.discussion_quote(api, binding, number, match[1] if match else None)
        match = re.fullmatch(r'issuecomment-([1-9][0-9]*)', fragment)
        review = re.fullmatch(r'pullrequestreview-([1-9][0-9]*)', fragment) if kind == 'pr' else None
        if match:
            value = api.call('GET', f'/repos/{binding.repo}/issues/comments/{match[1]}')
            if str(value.get('id')) != match[1] or not self.api_scope(value.get('issue_url'), binding, 'issues', number):
                raise ConnectError('Comment quote belongs to another issue.', 403)
        elif review:
            value = api.call('GET', f'/repos/{binding.repo}/pulls/{number}/reviews/{review[1]}')
            if str(value.get('id')) != review[1]:
                raise ConnectError('Review quote identity mismatch.', 403)
        elif not fragment:
            value = api.call('GET', f'/repos/{binding.repo}/issues/{number}')
            if value.get('number') != number or bool(value.get('pull_request')) != (kind == 'pr'):
                raise ConnectError('Description quote scope mismatch.', 403)
        else:
            raise ConnectError('This message is not a supported GitHub quote target.', 400)
        return value.get('body') or '', (value.get('user') or {}).get('login', 'GitHub'), url

    def prepare(self, api, binding, job, payload):
        ref = payload.get('reference')
        if not ref:
            return {'mode': 'comment'}, None
        # Do not trust message_reference snapshots or displayed embed text supplied by a client.
        value = self.service.discord().call('GET', f"/channels/{job['thread']}/messages/{ref['message_id']}")
        if str(value.get('id')) != ref['message_id']:
            raise ConnectError('Discord reply target changed.', 403)
        mode, root, node = 'quote', None, None
        url = None
        if value.get('author', {}).get('bot'):
            bot = self.service.discord().call('GET', '/users/@me')
            if not bot.get('bot') or str(value['author'].get('id')) != str(bot.get('id')) or value.get('webhook_id'):
                raise ConnectError('Reply to a Threadle GitHub card or a forwarded comment.', 403)
            queue = self.service.queue(binding)
            with queue.connect() as db:
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                sources = {r[0] for r in db.execute('SELECT source FROM source_pages WHERE message=?', (ref['message_id'],))} if 'source_pages' in tables else set()
                roots = db.execute('SELECT node,root FROM review_conversations WHERE topic=?', (job['topic'],)).fetchall() if 'review_conversations' in tables else []
            for candidate_node, candidate_root in roots:
                if 'github-source:' + nonce(job['topic'] + ':comment:pull_request_review_comment:' + candidate_root) in sources:
                    root, node = self.review(api, binding, job, candidate_root, candidate_node)
                    mode = 'review'
                    break
            if mode != 'review':
                urls = {embed['url'] for embed in value.get('embeds', []) if embed.get('url')}
                if len(urls) != 1:
                    raise ConnectError('The reply target has no unique GitHub source.', 400)
                url = urls.pop()
                parsed = urlsplit(url)
                inline = re.fullmatch(r'discussion_r([1-9][0-9]*)', parsed.fragment)
                if inline:
                    # Validate the URL scope before following an old card without saved mappings.
                    expected = f'/{binding.repo}/pull/{job["topic"].rsplit(":",1)[1]}'
                    if parsed.scheme != 'https' or parsed.netloc != 'github.com' or parsed.path.casefold() != expected.casefold() or parsed.query:
                        raise ConnectError('Review card scope mismatch.', 403)
                    root, node = self.review(api, binding, job, inline[1])
                    mode = 'review'
        else:
            # Never copy a member's Discord-only private message into GitHub automatically.
            with self.accounts.db() as db:
                original = db.execute('SELECT state,remote,url,github,binding,discord FROM comment_outbox WHERE id=?', (ref['message_id'],)).fetchone()
                inherited = db.execute('SELECT root,node FROM bridge_replies WHERE operation=(SELECT operation FROM comment_outbox WHERE id=?) AND mode=\'review\'', (ref['message_id'],)).fetchone()
            if (not original or original[4] != binding.id or str(value.get('author', {}).get('id')) != original[5]):
                raise ConnectError('The referenced message has not been shared to GitHub.', 400)
            if original[0] in ('queued', 'sending', 'uncertain'):
                raise ApiError('GitHub', 429, retry_after=5)
            if original[0] != 'done' or not original[1]:
                raise ConnectError('The referenced comment was not posted on GitHub.', 400)
            if inherited:
                root, node = self.review(api, binding, job, inherited[0], inherited[1])
                mode = 'review'
            else:
                url = original[2]
        plan = {'mode': mode, 'reference': ref['message_id'], 'root': root, 'node': node, 'input_hash': self.fingerprint(payload)}
        quote = self.quote(api, binding, job, url) if mode == 'quote' else None
        self.save(job, plan)
        return plan, quote

    @staticmethod
    def quoted(body, quoted):
        if quoted is None:
            return body
        text, author, url = quoted
        from comment_bridge import MARKER
        text = MARKER.sub('', text or '')
        author = re.sub(r'[^A-Za-z0-9-]', '', author) or 'GitHub'
        # Preserve the full quote when it fits; leave a source link when the API limit requires an excerpt.
        budget = max(1000, 60000 - len(body))
        lines = ('\n'.join('> ' + line for line in text.splitlines())).strip()
        if len(lines) > budget:
            lines = lines[:budget].rsplit('\n', 1)[0] + '\n> …'
        return f'> [{author}]({url})\n>\n{lines}\n\n{body}'

    def context(self, binding):
        return ReviewReplies(self, binding)

    def cleanup(self):
        with self.accounts.db() as db:
            db.row_factory = __import__('state_db').Row
            row = db.execute("SELECT o.*,r.node,r.root,r.input_hash,r.attempts AS cleanup_attempts FROM bridge_replies r JOIN comment_outbox o USING(operation) WHERE r.mode='review' AND r.applied=1 AND r.cleaned=0 AND o.state='done' AND r.next<=? ORDER BY o.created LIMIT 1", (self.bridge.clock(),)).fetchone()
            job = dict(row) if row else None
        if not job:
            return
        try:
            with self.accounts.lock:
                binding = self.service.registry.get(job['binding'])
                account = self.accounts.get(job['discord'], job['guild'])
                target = self.bridge.destination(job['guild'], job['thread'])
                if (not binding or not account or account['generation'] != job['generation'] or not account['comments'] or
                        not target or target[0].id != binding.id or target[1] != job['topic'] or self.bridge.clock() - job['created'] > 86400):
                    with self.accounts.db() as db:
                        db.execute('UPDATE bridge_replies SET cleaned=1 WHERE operation=?', (job['operation'],))
                    return
                self.bridge.check_discord(job, binding, target[2])
                current = self.service.discord().call('GET', f"/channels/{job['thread']}/messages/{job['id']}")
                if (str(current.get('author', {}).get('id')) != job['discord'] or not job['input_hash'] or
                        not hmac.compare_digest(self.fingerprint(current), job['input_hash'])):
                    # Never delete an edited input that has not been mirrored to GitHub.
                    with self.accounts.db() as db:
                        db.execute('UPDATE bridge_replies SET cleaned=1 WHERE operation=?', (job['operation'],))
                    return
                self.service.discord().call('DELETE', f"/channels/{job['thread']}/messages/{job['id']}")
                with self.accounts.db() as db:
                    db.execute('UPDATE bridge_replies SET cleaned=1 WHERE operation=?', (job['operation'],))
        except ApiError as error:
            with self.accounts.db() as db:
                db.execute('UPDATE bridge_replies SET cleaned=?,next=?,attempts=attempts+1 WHERE operation=?',
                           (int(error.status == 404), self.bridge.clock() + 60, job['operation']))
            if error.status == 403 and job['cleanup_attempts'] == 0:
                binding = self.service.registry.get(job['binding'])
                english = binding is not None and binding.config.get('language') == 'en'
                text = ('Your review reply is reflected in the card. Grant Threadle Manage Messages to remove this input automatically.' if english else
                        '리뷰 답글을 카드에 반영했어요. 입력 메시지를 자동 정리하려면 Threadle에 메시지 관리 권한을 허용해 주세요.')
                try:
                    self.service.discord().call('POST', '/channels/' + job['thread'] + '/messages', {
                        'content': text, 'allowed_mentions': {'parse': [], 'replied_user': False}, 'flags': 4096,
                        'message_reference': {'message_id': job['id'], 'fail_if_not_exists': False},
                        'nonce': str(int(hashlib.sha256(('reply-cleanup:' + job['operation']).encode()).hexdigest()[:16], 16)), 'enforce_nonce': True})
                except ApiError:
                    pass
        except ConnectError:
            with self.accounts.db() as db:
                db.execute('UPDATE bridge_replies SET next=? WHERE operation=?', (self.bridge.clock() + 60, job['operation']))


class ReviewReplies:
    def __init__(self, replies, binding):
        self.replies, self.binding = replies, binding

    def expect(self, topic, entries, current):
        for event, payload in entries:
            operation = payload.get('_bridge_review_operation')
            if event != 'pull_request_review_comment' or not operation or payload.get('action')=='deleted':
                continue
            with self.replies.accounts.db() as db:
                row = db.execute('SELECT o.created,o.remote,r.node FROM comment_outbox o JOIN bridge_replies r USING(operation) WHERE o.operation=? AND r.mode=\'review\'', (operation,)).fetchone()
            if row and self.replies.bridge.clock() - row[0] < 86400:
                if not any(thread['id'] == row[2] and any(str(comment.get('databaseId')) == row[1] for comment in thread['comments']) for thread in current):
                    raise RuntimeError('Posted review reply is not yet visible in the snapshot; retrying')

    def clean(self, topic, thread):
        from comment_bridge import MARKER
        result = copy.deepcopy(thread)
        operations = []
        for comment in result['comments']:
            body = comment.get('body') or ''
            match = MARKER.search(body)
            if match is None:
                continue
            receipt = self.replies.bridge.review_receipt(self.binding, topic.number, thread['id'], comment, match)
            if receipt:
                comment['body'] = MARKER.sub('', body)
                operations.append(receipt)
        result['_bridge_operations'] = operations
        return result

    def applied(self, topic, thread, discord_thread):
        if self.replies.service.registry.get(self.binding.id) != self.binding:
            return
        with self.replies.accounts.db() as db:
            for operation in thread.get('_bridge_operations', []):
                db.execute('UPDATE bridge_replies SET applied=1 WHERE operation=? AND mode=\'review\' AND EXISTS(SELECT 1 FROM comment_outbox o WHERE o.operation=bridge_replies.operation AND o.binding=? AND o.topic=? AND o.thread=? AND o.state=\'done\')',
                           (operation, self.binding.id, topic.key, str(discord_thread)))
