"""Project thread embeds into Discord Components V2 and recover their card fields."""
import datetime as dt
import re

V2 = 1 << 15
CONTAINER_BASE = 16000
CARD_STRIDE = 100
TEXT, SECTION, THUMBNAIL, GALLERY, CONTAINER = 10, 9, 11, 12, 17
TITLE, AUTHOR, BODY, FOOTER, TIME, IMAGE, ACCESSORY, THUMB, SOURCE = range(1, 10)
FIELD_BASE = 20
SAFE_LINK = re.compile(r'https://[^\s<>]+$')
DATE = re.compile(r'<t:(\d+):f>$')


def card_text(content, identity):
    return {'type': TEXT, 'id': identity, 'content': content}


def safe_heading(value):
    return re.sub(r'([\\*_\[\]~\x60<>|@])', r'\\\1', value)


def original_heading(value):
    return re.sub(r'\\([\\*_\[\]~\x60<>|@])', r'\1', value)


def components(embeds):
    result = []
    for index, item in enumerate(embeds):
        base = CONTAINER_BASE + CARD_STRIDE * index
        children = []
        if item.get('title'):
            title = safe_heading(item['title'])
            url = item.get('url') or ''
            if SAFE_LINK.fullmatch(url):
                title = f'[{title}](<{url}>)'
            children.append(card_text(f'**{title}**', base + TITLE))
        elif SAFE_LINK.fullmatch(item.get('url') or ''):
            children.append(card_text(f'<{item["url"]}>', base + SOURCE))
        if item.get('author', {}).get('name'):
            children.append(card_text('-# ' + safe_heading(item['author']['name']), base + AUTHOR))
        if item.get('description'):
            children.append(card_text(item['description'], base + BODY))
        thumbnail = item.get('thumbnail', {}).get('url')
        if thumbnail:
            first = children.pop(0) if children else card_text('\u200b', base + SOURCE)
            children.insert(0, {'type': SECTION, 'id': base + ACCESSORY, 'components': [first],
                                'accessory': {'type': THUMBNAIL, 'id': base + THUMB,
                                              'media': {'url': thumbnail}}})
        for field_index, field in enumerate(item.get('fields', [])):
            # The ID preserves the inline property for later edits of the same card.
            identity = base + FIELD_BASE + field_index * 2 + int(bool(field.get('inline')))
            children.append(card_text('**' + safe_heading(field['name']) + '**\n' + field['value'], identity))
        if item.get('image', {}).get('url'):
            children.append({'type': GALLERY, 'id': base + IMAGE,
                             'items': [{'media': {'url': item['image']['url']}}]})
        if item.get('footer', {}).get('text'):
            children.append(card_text('-# ' + item['footer']['text'], base + FOOTER))
        if item.get('timestamp'):
            timestamp = dt.datetime.fromisoformat(item['timestamp'].replace('Z', '+00:00'))
            children.append(card_text(f'-# <t:{int(timestamp.timestamp())}:f>', base + TIME))
        if children:
            card = {'type': CONTAINER, 'id': base, 'components': children}
            if item.get('color') is not None:
                card['accent_color'] = item['color']
            result.append(card)
    return result


def fits(cards):
    children = [child for card in cards for child in card['components']]
    text = [child for child in children if child['type'] == TEXT]
    sections = [child for child in children if child['type'] == SECTION]
    text.extend(section['components'][0] for section in sections)
    units = sum(len(child['content'].encode('utf-16-le')) // 2 for child in text)
    return bool(cards) and units <= 4000 and len(cards) + len(children) + 2 * len(sections) <= 40


def render(data):
    cards = components(data['embeds'])
    if not fits(cards):
        return None
    rendered = {key: value for key, value in data.items() if key not in ('embeds', 'content')}
    rendered['flags'] = data.get('flags', 0) | V2
    rendered['components'] = cards
    return rendered


def restore(message, owner=None):
    """Expose the owned card's fields internally for source tracking and edits."""
    if isinstance(message, list):
        return [restore(row, owner) for row in message]
    if not isinstance(message, dict) or not message.get('flags', 0) & V2:
        return message
    if owner is not None and str(message.get('author', {}).get('id')) != str(owner):
        return message
    cards = message.get('components') or []
    if not cards or any(
        card.get('type') != CONTAINER or card.get('id') != CONTAINER_BASE + CARD_STRIDE * index
        for index, card in enumerate(cards)
    ):
        return message
    embeds = []
    for card in cards:
        base = card['id']
        item = {}
        if card.get('accent_color') is not None:
            item['color'] = card['accent_color']
        children = list(card.get('components') or [])
        for child in children:
            identity = child.get('id', 0) - base
            if child.get('type') == SECTION and identity == ACCESSORY:
                item['thumbnail'] = {'url': child['accessory']['media']['url']}
                children.extend(child['components'])
            elif child.get('type') == GALLERY and identity == IMAGE:
                item['image'] = {'url': child['items'][0]['media']['url']}
            elif child.get('type') == TEXT:
                value = child.get('content', '')
                if identity == TITLE:
                    title, separator, url = value[2:-2].rpartition('](<')
                    if separator and url.endswith('>)'):
                        item['url'] = url[:-2]
                        value = '**' + title[1:] + '**'
                    item['title'] = original_heading(value[2:-2])
                elif identity == SOURCE and value.startswith('<'):
                    item['url'] = value[1:-1]
                elif identity == AUTHOR:
                    item['author'] = {'name': original_heading(value[3:])}
                elif identity == BODY:
                    item['description'] = value
                elif identity == FOOTER:
                    item['footer'] = {'text': value[3:]}
                elif identity == TIME:
                    match = DATE.fullmatch(value[3:])
                    if match:
                        item['timestamp'] = dt.datetime.fromtimestamp(int(match[1]), dt.timezone.utc).isoformat()
                elif identity >= FIELD_BASE:
                    name, _, detail = value.partition('\n')
                    item.setdefault('fields', []).append({'name': original_heading(name[2:-2]), 'value': detail,
                                                          'inline': bool((identity - FIELD_BASE) % 2)})
        embeds.append(item)
    return dict(message, embeds=embeds)
