"""Delete only the GitHub comment bound to a deleted human Discord input."""
import logging
from urllib.parse import urlsplit

from connect_auth import ConnectError
from discord_threads import ApiError

LOG = logging.getLogger('comment-deletions')


class Deletions:
    def __init__(self, bridge):
        self.bridge, self.accounts, self.service = bridge, bridge.accounts, bridge.service
        with self.accounts.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS comment_delete_jobs(
                operation TEXT PRIMARY KEY,state TEXT NOT NULL DEFAULT 'queued',
                attempts INTEGER NOT NULL DEFAULT 0,next REAL NOT NULL DEFAULT 0,
                created REAL NOT NULL,error TEXT);
                CREATE INDEX IF NOT EXISTS comment_delete_ready ON comment_delete_jobs(state,next);
            ''')

    def ingest(self, guild, channel, message):
        guild, channel, message = str(guild), str(channel), str(message)
        events = self.service.chat_events
        own_state = events.delete_state(channel, message)
        own = own_state in ('pending','done')
        with self.accounts.lock, self.accounts.db() as db:
            db.row_factory = __import__('state_db').Row
            row = db.execute('SELECT * FROM comment_outbox WHERE id=? AND guild=? AND thread=?',
                             (message, guild, channel)).fetchone()
            events.record('message-delete', guild, channel, message, author=row['discord'] if row else None,
                          actor=getattr(events,'bot_id',None) if own_state=='done' else None,
                          source=('threadle' if own_state=='done' else 'threadle-intent') if own else 'gateway')
            if own or not row:
                return False
            job = dict(row)
            if job['state'] == 'queued' and not job['remote']:
                db.execute("UPDATE comment_outbox SET state='failed',payload=NULL,error='input-deleted',notified=1 WHERE id=?", (message,))
                return True
            if not job['remote'] and job['state'] not in ('sending', 'uncertain', 'deleting') and job['error'] != 'delivery-uncertain':
                return False
            inserted = db.execute("INSERT OR IGNORE INTO comment_delete_jobs(operation,created) VALUES(?,?)", (job['operation'], self.bridge.clock())).rowcount
            if not inserted:
                return False
            # Stop the posting worker. Missing receipts are reconciled, NEVER reposted.
            db.execute("UPDATE comment_outbox SET state='deleting',notified=1 WHERE id=?", (message,))
        return True

    @staticmethod
    def scoped_url(value, binding, kind, number):
        parsed = urlsplit(str(value or ''))
        return (parsed.scheme == 'https' and parsed.netloc == 'api.github.com' and not parsed.query and
                parsed.path.casefold() == f'/repos/{binding.repo}/{kind}/{number}'.casefold())

    def discussion(self, api, binding, job, number):
        owner, name = binding.repo.split('/')
        cursor = None
        for _ in range(20):
            result = api.call('POST', '/graphql', {'query': '''query($owner:String!,$name:String!,$number:Int!,$after:String){
              repository(owner:$owner,name:$name){databaseId discussion(number:$number){number
                comments(first:100,after:$after){nodes{id databaseId author{... on User{databaseId}}}
                  pageInfo{hasNextPage endCursor}}}}}''',
                'variables': {'owner': owner, 'name': name, 'number': number, 'after': cursor}})
            if result.get('errors'):
                raise ApiError('GitHub', 'network')
            repo = result.get('data', {}).get('repository') or {}
            discussion = repo.get('discussion') or {}
            if repo.get('databaseId') != binding.repo_id or discussion.get('number') != number:
                raise ConnectError('Discussion scope changed.', 403)
            connection = discussion['comments']
            for comment in connection['nodes']:
                if str(comment.get('databaseId')) != job['remote']:
                    continue
                if str((comment.get('author') or {}).get('databaseId')) != job['github']:
                    raise ConnectError('Comment author changed.', 403)
                result = api.call('POST', '/graphql', {'query': '''mutation($input:DeleteDiscussionCommentInput!){
                  deleteDiscussionComment(input:$input){clientMutationId}}''',
                    'variables': {'input': {'id': comment['id'], 'clientMutationId': job['operation']}}})
                if result.get('errors'):
                    raise ApiError('GitHub', 'network')
                return
            if not connection['pageInfo']['hasNextPage']:
                return
            cursor = connection['pageInfo']['endCursor']
        raise ConnectError('Deletion receipt search exceeded its bounded range.', 409)

    def remove(self, api, binding, job, kind, number):
        if kind == 'discussion':
            return self.discussion(api, binding, job, number)
        issue = api.call('GET', f'/repos/{binding.repo}/issues/{number}')
        if issue.get('number') != number or bool(issue.get('pull_request')) != (kind == 'pr'):
            raise ConnectError('Comment topic changed.', 403)
        plan = self.bridge.replies.plan(job)
        review = plan['mode'] == 'review'
        section = 'pulls' if review else 'issues'
        path = f'/repos/{binding.repo}/{section}/comments/{job["remote"]}'
        try:
            comment = api.call('GET', path)
        except ApiError as error:
            if error.status == 404:
                return  # Verified repository/topic is accessible; already removed.
            raise
        if (str(comment.get('id')) != job['remote'] or str(comment.get('user', {}).get('id')) != job['github'] or
                not self.scoped_url(comment.get('pull_request_url' if review else 'issue_url'), binding, section, number) or
                review and str(comment.get('in_reply_to_id')) != plan['root']):
            raise ConnectError('Comment receipt scope changed.', 403)
        api.call('DELETE', path)

    def tick(self):
        with self.accounts.lock:
            with self.accounts.db() as db:
                db.row_factory = __import__('state_db').Row
                row = db.execute("SELECT o.*,d.attempts AS delete_attempts,d.created AS delete_created FROM comment_delete_jobs d JOIN comment_outbox o USING(operation) WHERE d.state='queued' AND d.next<=? ORDER BY d.created LIMIT 1", (self.bridge.clock(),)).fetchone()
            if not row:
                return False
            job, outcome = dict(row), 'done'
            retry, error = 0, None
            try:
                account = self.accounts.get(job['discord'], job['guild'])
                target = self.bridge.destination(job['guild'], job['thread'])
                if (not account or not account['comments'] or account['generation'] != job['generation'] or
                        not target or target[0].id != job['binding'] or target[1] != job['topic']):
                    raise ConnectError('Connection authorization changed.', 403)
                binding, _, _, kind, number = target
                api = self.bridge.user_api(self.service, self.accounts.token(job['discord'], job['generation']))
                if str(api.call('GET', '/user').get('id')) != job['github']:
                    raise ConnectError('Account identity changed.', 403)
                repo = api.call('GET', f'/repositories/{binding.repo_id}')
                if repo.get('id') != binding.repo_id or repo.get('full_name', '').casefold() != binding.repo.casefold():
                    raise ConnectError('Repository access changed.', 403)
                if not job['remote']:
                    found = self.bridge.reconcile(api, binding, job, kind, number)
                    if not found:
                        raise ApiError('GitHub', 'network')
                    job['remote'] = found[0]
                    with self.accounts.db() as db:
                        db.execute('UPDATE comment_outbox SET remote=?,url=?,payload=NULL WHERE operation=?', (*found, job['operation']))
                self.remove(api, binding, job, kind, number)
            except ConnectError:
                outcome, error = 'failed', 'authorization-or-scope-changed'
            except ApiError as problem:
                error = 'github-' + str(problem.status)
                if problem.status in (401, 403, 410, 422) and problem.retry_after is None:
                    outcome = 'failed'
                else:
                    outcome = 'queued'
                    try: retry = min(86400, max(5, float(problem.retry_after or 30)))
                    except (ValueError, TypeError): retry = 60
            except Exception as problem:
                LOG.warning('Comment deletion retry error=%s', type(problem).__name__)
                outcome, error, retry = 'queued', 'temporarily-unavailable', 60
            if job['delete_attempts'] >= 9 or self.bridge.clock() - job['delete_created'] > 86400:
                outcome = 'failed'
            with self.accounts.db() as db:
                db.execute('UPDATE comment_delete_jobs SET state=?,attempts=attempts+1,next=?,error=? WHERE operation=?',
                           (outcome, self.bridge.clock()+retry, error, job['operation']))
                if outcome != 'queued':
                    db.execute("UPDATE comment_outbox SET state=?,payload=NULL,error=?,notified=1 WHERE operation=?",
                               ('deleted' if outcome == 'done' else 'delete-failed', error, job['operation']))
                    db.execute('UPDATE bridge_replies SET cleaned=1,deleted_body=NULL WHERE operation=?', (job['operation'],))
            self.service.chat_events.record('github-comment-delete', job['guild'], job['thread'], job['id'],
                                            author=job['discord'], source='threadle', outcome=outcome)
            return True
