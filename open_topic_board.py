"""Keep one editable board per connection, channel, and topic kind."""
import logging
import re

from discord_threads import ApiError, clip, nonce


LOG = logging.getLogger(__name__)
TITLES = {'pr': ('리뷰 가능한 PR', 'PRs ready for review'),
          'issue': ('열린 Issue', 'Open issues'),
          'discussion': ('답변을 기다리는 Discussion', 'Unanswered discussions')}
LIMIT = 25


def refresh(threads, channel, kind):
    from delivery import CachedThreads

    if not isinstance(threads, CachedThreads) or not channel:
        return
    try:
        update(threads, str(channel), kind)
    except ApiError as error:
        LOG.warning('Could not refresh open-topic board channel=%s status=%s', channel, error.status)


def update(threads, channel, kind):
    queue = threads.queue
    with queue.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_thread_names'").fetchone():
            return
        db.execute('CREATE TABLE IF NOT EXISTS open_topic_boards (channel TEXT, kind TEXT, message TEXT, digest TEXT, pinned INTEGER, PRIMARY KEY(channel,kind))')
        saved = db.execute('SELECT message,digest,pinned FROM open_topic_boards WHERE channel=? AND kind=?', (channel, kind)).fetchone()
        rows = db.execute('''SELECT l.thread,n.name FROM links l JOIN topic_thread_names n ON n.topic=l.topic
                             WHERE l.channel=? AND l.topic LIKE ? AND n.name LIKE ?
                             ORDER BY length(l.thread), l.thread LIMIT ?''',
                          (channel, '%:' + kind + ':%', '[' + kind.upper() + ' #% · Open]%', LIMIT + 1)).fetchall()
    if not rows and not saved:
        return

    english = threads.language == 'en'
    lines = []
    for thread, name in rows[:LIMIT]:
        number, _, label = name.partition(' · Open]')
        label = label.strip().partition(' · ')[2] or label.strip()
        label = clip(re.sub(r'([\\`*_\[\]<>~|])', r'\\\1', label), 64).rstrip('\\')
        number = number.removeprefix('[')
        lines.append(f'• [{number} · {label}](https://discord.com/channels/{threads.guild}/{thread})')
    if len(rows) > LIMIT:
        lines.append('Showing the oldest 25.' if english else '오래된 항목 25개를 표시해요.')
    description = '\n'.join(lines) or ('Nothing open right now.' if english else '지금 표시할 항목이 없어요.')
    title = TITLES[kind][english]
    digest = nonce(title + '\n' + description)
    path = f'/channels/{channel}/messages'
    data = {'embeds': [{'title': title, 'description': description, 'color': 0x2DA44E}],
            'allowed_mentions': {'parse': []}}
    if saved and saved[1] != digest:
        try:
            threads.api.call('PATCH', f'{path}/{saved[0]}', data)
        except ApiError as error:
            if error.status != 404:
                raise
            saved = None
    if not saved:
        key = nonce(f'open-board:{queue.path}:{channel}:{kind}')
        message = threads.api.call('POST', path, dict(data, flags=4096, nonce=key, enforce_nonce=True))
        saved = (message['id'], digest, 0)
    with queue.connect() as db:
        if saved[1] != digest or not saved[2]:
            db.execute('INSERT OR REPLACE INTO open_topic_boards VALUES(?,?,?,?,?)',
                       (channel, kind, saved[0], digest, saved[2]))
    if not saved[2]:
        threads.api.call('PUT', f'{path}/pins/{saved[0]}')
        with queue.connect() as db:
            db.execute('UPDATE open_topic_boards SET pinned=1 WHERE channel=? AND kind=?', (channel, kind))
