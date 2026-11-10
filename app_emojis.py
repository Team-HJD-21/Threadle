"""Resolve Threadle's application emojis without making delivery depend on them."""
import base64
from pathlib import Path
import threading
import time

from discord_threads import Api, ApiError


THUMBNAIL_NAMES = {'threadle_good', 'threadle_404', 'threadle_sleep', 'threadle_wake', 'threadle_fixing'}
NAMES = {'threadle_hello', 'threadle_pr_sent', 'threadle_review_please', 'threadle_merge'}
_cache = {}
_lock = threading.Lock()
ASSETS = Path(__file__).with_name('web_assets')


def prefix(api, name):
    if name not in NAMES | THUMBNAIL_NAMES or not isinstance(api, Api) or api.service != 'Discord':
        return ''
    with _lock:
        now = time.monotonic()
        expires, emojis = _cache.get(api.token, (0, {}))
        if expires <= now:
            try:
                application = api.call('GET', '/oauth2/applications/@me')
                path = f"/applications/{application['id']}/emojis"
                rows = api.call('GET', path)['items']
                for missing in sorted(NAMES - {row['name'] for row in rows}):
                    try:
                        image = base64.b64encode((ASSETS / f'{missing}.png').read_bytes()).decode('ascii')
                        rows.append(api.call('POST', path, {'name': missing, 'image': 'data:image/png;base64,' + image}))
                    except (ApiError, OSError):
                        break
                emojis = {
                    row['name']: f"<{'a' if row.get('animated') else ''}:{row['name']}:{row['id']}> "
                    for row in rows if row['name'] in NAMES | THUMBNAIL_NAMES and row.get('available', True)
                }
            except (ApiError, KeyError, TypeError, OSError):
                pass
            _cache[api.token] = (now + 300, emojis)
        return emojis.get(name, '')


def thumbnail(api, name):
    emoji = prefix(api, name).strip()
    if not emoji:
        return None
    emoji_id = emoji.rsplit(':', 1)[1].removesuffix('>')
    extension = 'gif' if emoji.startswith('<a:') else 'png'
    return {'url': f'https://cdn.discordapp.com/emojis/{emoji_id}.{extension}?size=128'}
