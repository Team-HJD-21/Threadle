"""Keep Discord recovery receipts in the delivery database, outside visible embeds."""
import copy
import datetime as dt
import hashlib
import json
import re
import time
from urllib.parse import unquote, urlsplit

PREFIXES = ('github-thread:', 'github-event:', 'github-source:', 'github-alert:', 'threadle-ci:')
MESSAGE_PATH = re.compile(r'/channels/(\d+)/messages(?:/(\d+))?(?:\?.*)?$')


def bind(api, queue):
    if getattr(api, 'service', None) != 'Discord' or queue is None:
        return
    current = getattr(api, 'message_tracking', None)
    if current is None or current.queue.path != queue.path:
        api.message_tracking = MessageTracking(queue)


def clean(data):
    """Strip only generated receipt lines, preserving human-readable footer text."""
    result = copy.deepcopy(data)
    markers = {}
    for index, embed in enumerate(result.get('embeds', [])):
        footer = embed.get('footer', {})
        lines = str(footer.get('text', '')).splitlines()
        hidden = [line for line in lines if line.startswith(PREFIXES)]
        if not hidden:
            continue
        markers[str(index)] = hidden
        visible = [line for line in lines if not line.startswith(PREFIXES)]
        if visible:
            embed['footer'] = dict(footer, text='\n'.join(visible))
        else:
            embed.pop('footer', None)
    return result, markers


def fingerprint(message):
    """Ignore server-added embed fields while retaining source links and media identity."""
    filenames = {str(item.get('url')): item.get('filename') for item in message.get('attachments', [])}
    filenames.update({str(item.get('proxy_url')): item.get('filename') for item in message.get('attachments', [])})
    def media(value):
        url = str(value.get('url', ''))
        if filenames.get(url):
            return 'attachment://' + filenames[url]
        # Edits retain attachment IDs only; Discord's CDN URL still identifies its filename.
        parsed = urlsplit(url)
        if parsed.hostname in ('cdn.discordapp.com', 'media.discordapp.net') and parsed.path.startswith('/attachments/'):
            return 'attachment://' + unquote(parsed.path.rsplit('/', 1)[-1])
        return url
    embeds = []
    sanitized, _ = clean(message)
    for embed in sanitized.get('embeds', []):
        row = {key: embed[key] for key in ('title', 'description', 'url', 'color') if key in embed}
        if embed.get('timestamp'):
            try: row['timestamp'] = dt.datetime.fromisoformat(embed['timestamp'].replace('Z', '+00:00')).isoformat()
            except (ValueError, TypeError): row['timestamp'] = embed['timestamp']
        if embed.get('author'):
            row['author'] = {key: value for key, value in embed['author'].items() if key in ('name', 'url', 'icon_url')}
        if embed.get('footer'):
            row['footer'] = {key: value for key, value in embed['footer'].items() if key in ('text', 'icon_url')}
        if embed.get('fields'):
            row['fields'] = [dict(name=item.get('name', ''), value=item.get('value', ''), inline=bool(item.get('inline', False))) for item in embed['fields']]
        for key in ('image', 'thumbnail'):
            if embed.get(key): row[key] = media(embed[key])
        embeds.append(row)
    raw = json.dumps({'content': message.get('content') or '', 'embeds': embeds}, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return hashlib.sha256(raw.encode()).hexdigest()


class MessageTracking:
    def __init__(self, queue):
        self.queue = queue
        with queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS hidden_message_markers(channel TEXT NOT NULL,message TEXT NOT NULL,markers TEXT NOT NULL,PRIMARY KEY(channel,message))')
            db.execute('CREATE TABLE IF NOT EXISTS pending_message_markers(identity TEXT PRIMARY KEY,channel TEXT NOT NULL,message TEXT,fingerprint TEXT NOT NULL,markers TEXT NOT NULL,created REAL NOT NULL)')
            if 'nonce' not in {row[1] for row in db.execute('PRAGMA table_info(pending_message_markers)')}:
                db.execute('ALTER TABLE pending_message_markers ADD COLUMN nonce TEXT')
            db.execute('CREATE INDEX IF NOT EXISTS pending_marker_channel ON pending_message_markers(channel,fingerprint)')
            db.execute('CREATE TABLE IF NOT EXISTS message_marker_owner(id TEXT PRIMARY KEY)')
            saved = db.execute('SELECT id FROM message_marker_owner LIMIT 1').fetchone()
            self.owner = saved[0] if saved else None

    def set_owner(self, owner):
        if owner and owner != self.owner:
            with self.queue.connect() as db:
                db.execute('DELETE FROM message_marker_owner')
                db.execute('INSERT INTO message_marker_owner VALUES(?)', (owner,))
            self.owner = owner

    def prepare(self, method, path, data):
        match = MESSAGE_PATH.fullmatch(path)
        if method not in ('POST', 'PATCH') or not match or not isinstance(data, dict):
            return data, None
        sanitized, markers = clean(data)
        if not markers:
            return data, None
        channel, message = match.groups()
        encoded = json.dumps(markers, sort_keys=True)
        digest = fingerprint(sanitized)
        identity = hashlib.sha256(json.dumps([method, channel, message, digest, encoded]).encode()).hexdigest()
        # Commit the recovery record before Discord receives the message.
        with self.queue.connect() as db:
            sent_nonce = str(data['nonce']) if data.get('nonce') is not None else None
            db.execute('INSERT OR IGNORE INTO pending_message_markers(identity,channel,message,fingerprint,markers,created,nonce) VALUES(?,?,?,?,?,?,?)', (identity, channel, message, digest, encoded, time.time(), sent_nonce))
        return sanitized, (identity, channel, message, encoded)

    def remember(self, operation, result):
        if operation is None:
            return
        identity, channel, target, markers = operation
        message = str((result or {}).get('id') or target or '')
        if not message:
            return
        self.set_owner((result or {}).get('author', {}).get('id'))
        with self.queue.connect() as db:
            db.execute('INSERT OR REPLACE INTO hidden_message_markers VALUES(?,?,?)', (channel, message, markers))
            db.execute('DELETE FROM pending_message_markers WHERE identity=?', (identity,))

    def restore_message(self, channel, message, owner):
        owner = owner or self.owner
        if not owner or message.get('author', {}).get('id') != owner or not message.get('id'):
            return message
        identity = str(message['id'])
        # Visible legacy receipts are direct evidence of ownership, stronger than any pending match.
        if clean(message)[1]:
            return message
        digest = fingerprint(message)
        try: created = (int(identity) >> 22) / 1000 + 1420070400
        except (TypeError, ValueError): created = 0
        with self.queue.connect() as db:
            saved = db.execute('SELECT markers FROM hidden_message_markers WHERE channel=? AND message=?', (channel, identity)).fetchone()
            # Only an explicit PATCH to this ID may supersede its already-known source mapping.
            pending = db.execute('SELECT identity,markers FROM pending_message_markers WHERE channel=? AND fingerprint=? AND message=? ORDER BY created DESC', (channel, digest, identity)).fetchall()
            if not pending and not saved:
                candidates = db.execute('SELECT identity,markers,created,nonce FROM pending_message_markers WHERE channel=? AND fingerprint=? AND message IS NULL ORDER BY created DESC', (channel, digest)).fetchall()
                received_nonce = str(message['nonce']) if message.get('nonce') is not None else None
                if received_nonce is not None:
                    # A mismatched nonce must never fall back to coincidentally identical content.
                    pending = [(row[0], row[1]) for row in candidates if row[3] == received_nonce]
                elif any(embed.get('url') for embed in message.get('embeds', [])):
                    # Discord may omit nonce. Only source-linked, recent content is eligible then;
                    # image-only pages cannot safely identify their source by rendered content.
                    pending = [(row[0], row[1]) for row in candidates if created - 300 <= row[2] <= created + 5]
            # Identical rendered pages with different sources must never be adopted ambiguously.
            choices = {row[1] for row in pending}
            if len(choices) == 1:
                markers = pending[0][1]
                db.execute('INSERT OR REPLACE INTO hidden_message_markers VALUES(?,?,?)', (channel, identity, markers))
                db.executemany('DELETE FROM pending_message_markers WHERE identity=?', [(row[0],) for row in pending])
            else:
                markers = saved[0] if saved else None
        if not markers:
            return message
        result, _ = clean(message)
        for index, lines in json.loads(markers).items():
            if int(index) >= len(result.get('embeds', [])):
                continue
            embed = result['embeds'][int(index)]
            visible = str(embed.get('footer', {}).get('text') or '')
            embed['footer'] = dict(embed.get('footer', {}), text='\n'.join(lines + ([visible] if visible else [])))
        return result

    def restore(self, path, result, owner):
        match = MESSAGE_PATH.fullmatch(path)
        if not match:
            return result
        channel, _ = match.groups()
        if isinstance(result, list):
            return [self.restore_message(channel, row, owner) for row in result]
        if isinstance(result, dict):
            return self.restore_message(channel, result, owner)
        return result

    def deleted(self, path):
        match = MESSAGE_PATH.fullmatch(path)
        if match and match.group(2):
            with self.queue.connect() as db:
                db.execute('DELETE FROM hidden_message_markers WHERE channel=? AND message=?', match.groups())
                db.execute('DELETE FROM pending_message_markers WHERE channel=? AND message=?', match.groups())
