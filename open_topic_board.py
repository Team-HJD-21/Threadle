"""Pin existing Threadle starter cards for active topics in their own channel."""
import logging

from discord_threads import ApiError, marker


LOG = logging.getLogger(__name__)


def refresh(threads, channel, kind):
    from delivery import CachedThreads

    if not isinstance(threads, CachedThreads) or not channel:
        return
    try:
        update(threads, str(channel), kind)
    except ApiError as error:
        LOG.warning('Could not refresh starter pins channel=%s status=%s', channel, error.status)


def unpin(api, path):
    try:
        api.call('DELETE', path)
    except ApiError as error:
        if error.status != 404:
            raise


def update(threads, channel, kind):
    queue = threads.queue
    api = threads.api
    messages = f'/channels/{channel}/messages'
    with queue.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_thread_names'").fetchone():
            return
        db.execute('CREATE TABLE IF NOT EXISTS starter_pins (channel TEXT, kind TEXT, message TEXT, managed INTEGER NOT NULL, PRIMARY KEY(channel,kind,message))')
        legacy = db.execute("SELECT 1 FROM sqlite_master WHERE name='open_topic_boards'").fetchone()
        board = db.execute('SELECT message FROM open_topic_boards WHERE channel=? AND kind=?', (channel, kind)).fetchone() if legacy else None
        saved = dict(db.execute('SELECT message,managed FROM starter_pins WHERE channel=? AND kind=?', (channel, kind)))
        desired = dict(db.execute('''SELECT l.thread,l.topic FROM links l JOIN topic_thread_names n ON n.topic=l.topic
                                    WHERE l.channel=? AND l.topic LIKE ? AND n.name LIKE ?
                                    ORDER BY length(l.thread),l.thread''',
                                  (channel, '%:' + kind + ':%', '[' + kind.upper() + ' #% · Open]%')))

    if board:
        unpin(api, f'{messages}/pins/{board[0]}')
        with queue.connect() as db:
            db.execute('DELETE FROM open_topic_boards WHERE channel=? AND kind=?', (channel, kind))

    for message in saved.keys() - desired.keys():
        if saved[message]:
            unpin(api, f'{messages}/pins/{message}')
        with queue.connect() as db:
            db.execute('DELETE FROM starter_pins WHERE channel=? AND kind=? AND message=?', (channel, kind, message))

    for message, topic in desired.items():
        if message in saved:
            continue
        try:
            starter = api.call('GET', f'{messages}/{message}')
        except ApiError as error:
            if error.status == 404:
                continue
            raise
        if starter.get('author', {}).get('id') != threads.bot['id'] or topic not in marker(starter):
            continue
        # A pin already present when first seen belongs to a user, so leave it alone on closure.
        managed = not starter.get('pinned', False)
        if managed:
            api.call('PUT', f'{messages}/pins/{message}')
        with queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO starter_pins VALUES(?,?,?,?)', (channel, kind, message, managed))
