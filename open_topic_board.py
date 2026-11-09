"""Pin existing Threadle starter cards for active topics in their own channel."""
import logging

from discord_threads import ApiError, marker


LOG = logging.getLogger(__name__)


def refresh(threads, channel, kind, reconcile=False):
    from delivery import CachedThreads

    if not isinstance(threads, CachedThreads) or not channel:
        return
    try:
        update(threads, str(channel), kind, reconcile)
    except ApiError as error:
        LOG.warning('Could not refresh starter pins channel=%s status=%s', channel, error.status)


def unpin(api, path):
    try:
        api.call('DELETE', path)
    except ApiError as error:
        if error.status != 404:
            raise


def clean_pin_notices(api, messages, bot_id, starters):
    try:
        for notice in api.call('GET', f'{messages}?limit=100'):
            if (
                notice.get('type') == 6
                and notice.get('author', {}).get('id') == bot_id
                and (notice.get('message_reference') or {}).get('message_id') in starters
                and notice.get('id') not in starters
                and notice.get('id')
            ):
                unpin(api, f"{messages}/{notice['id']}")
    except ApiError as error:
        LOG.warning('Could not clean pin notices path=%s status=%s', messages, error.status)
        return False
    return True


def release(threads, channel, kind, message):
    queue = threads.queue
    with queue.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='starter_pins'").fetchone():
            return
        pin = db.execute('SELECT managed FROM starter_pins WHERE channel=? AND kind=? AND message=?',
                         (channel, kind, message)).fetchone()
    if pin is None:
        return
    if pin[0]:
        messages = f'/channels/{channel}/messages'
        unpin(threads.api, f'{messages}/pins/{message}')
        clean_pin_notices(threads.api, messages, threads.bot['id'], {message})
    with queue.connect() as db:
        db.execute('DELETE FROM starter_pins WHERE channel=? AND kind=? AND message=?',
                   (channel, kind, message))


def inactive_starter(starter, topic):
    return any(field.get('name') == 'Current Status'
               and field.get('value', '').split(' ')[0] in ('`Merged`', '`Closed`', '`Answered`', '`Draft`')
               for embed in starter.get('embeds', [])
               if embed.get('footer', {}).get('text', '').split('\n', 1)[0] == topic
               for field in embed.get('fields', []))


def update(threads, channel, kind, reconcile=False):
    queue = threads.queue
    api = threads.api
    messages = f'/channels/{channel}/messages'
    with queue.connect() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='topic_thread_names'").fetchone():
            return
        db.execute('CREATE TABLE IF NOT EXISTS starter_pins (channel TEXT, kind TEXT, message TEXT, managed INTEGER NOT NULL, PRIMARY KEY(channel,kind,message))')
        db.execute('CREATE TABLE IF NOT EXISTS pin_notice_cleanup (channel TEXT, kind TEXT, PRIMARY KEY(channel,kind))')
        scanned = db.execute('SELECT 1 FROM pin_notice_cleanup WHERE channel=? AND kind=?', (channel, kind)).fetchone()
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
        release(threads, channel, kind, message)
        del saved[message]

    new_pins = set()
    for message, topic in desired.items():
        if message in saved and (not reconcile or not saved[message]):
            continue
        try:
            starter = api.call('GET', f'{messages}/{message}')
        except ApiError as error:
            if error.status == 404:
                continue
            raise
        if starter.get('author', {}).get('id') != threads.bot['id'] or topic not in marker(starter):
            continue
        if inactive_starter(starter, topic):
            if saved.get(message):
                release(threads, channel, kind, message)
                del saved[message]
            continue
        if message in saved:
            continue
        # A pin already present when first seen belongs to a user, so leave it alone on closure.
        managed = not starter.get('pinned', False)
        if managed:
            api.call('PUT', f'{messages}/pins/{message}')
            new_pins.add(message)
        with queue.connect() as db:
            db.execute('INSERT OR IGNORE INTO starter_pins VALUES(?,?,?,?)', (channel, kind, message, managed))
            if managed:
                db.execute('DELETE FROM pin_notice_cleanup WHERE channel=? AND kind=?', (channel, kind))

    starters = new_pins | {message for message, managed in saved.items() if managed}
    if starters and (new_pins or not scanned):
        if clean_pin_notices(api, messages, threads.bot['id'], starters):
            with queue.connect() as db:
                db.execute('INSERT OR IGNORE INTO pin_notice_cleanup VALUES(?,?)', (channel, kind))
