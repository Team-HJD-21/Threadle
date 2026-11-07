"""Translate GitHub comment HTML into Discord-readable text, without rendering it."""
import html
from html.parser import HTMLParser
import re
import uuid
from urllib.parse import quote, urlsplit


BLOCKS = {'p', 'div', 'section', 'article', 'header', 'footer', 'details', 'blockquote',
          'ul', 'ol', 'li', 'table', 'thead', 'tbody', 'tfoot', 'tr', 'td', 'th',
          'pre', 'summary', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}
INLINE = {'a', 'img', 'br', 'hr', 'wbr', 'strong', 'b', 'em', 'i', 'del', 's', 'strike',
          'code', 'kbd', 'samp', 'span', 'small', 'sub', 'sup', 'input', 'script', 'style'}
VOID = {'img', 'br', 'hr', 'wbr', 'input'}


class Node:
    def __init__(self, tag='', attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []


class CommentHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node()
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        if tag not in BLOCKS | INLINE:
            self.stack[-1].children.append(self.get_starttag_text())
            return
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in VOID and len(self.stack) < 64:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack)-1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return
        if tag not in BLOCKS | INLINE:
            self.stack[-1].children.append('</' + tag + '>')

    def handle_data(self, data):
        self.stack[-1].children.append(data.replace('\xa0', ' '))


def safe_url(value, image=False):
    value = (value or '').strip()
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ({'https'} if image else {'http', 'https'}) or not parsed.hostname
                or parsed.username or parsed.password or len(value) > 2048
                or re.search(r'[\x00-\x20\x7f]', value)):
            return None
    except ValueError:
        return None
    return quote(value, safe="/:?#@!$&'*+,;=%~._-")


def literal(node):
    return ''.join(child if isinstance(child, str) else literal(child) for child in node.children)


def contains_image(node):
    return node.tag == 'img' or any(contains_image(child) for child in node.children if isinstance(child, Node))


def table_rows(node):
    for child in node.children:
        if isinstance(child, Node):
            if child.tag == 'tr':
                yield child
            elif child.tag in ('thead', 'tbody', 'tfoot'):
                yield from table_rows(child)


def render_table(node):
    headers, lines = [], []
    for row in table_rows(node):
        cells = [child for child in row.children if isinstance(child, Node) and child.tag in ('td', 'th')]
        values = [render(cell).strip() for cell in cells]
        if not values:
            continue
        if all(cell.tag == 'th' for cell in cells):
            headers = values
        elif headers:
            lines.append('• ' + ' · '.join('**' + (headers[n] if n < len(headers) else str(n+1)) + '**: ' + value
                                          for n, value in enumerate(values)))
        elif len(values) == 2:
            label, value = values
            if not label.startswith('**'):
                label = '**' + label.rstrip(':') + ':**'
            lines.append(label + ' ' + value)
        else:
            lines.append(' · '.join(values))
    return '\n\n' + '\n\n'.join(lines or headers) + '\n\n'


def code_block(text, language=''):
    fence = '`' * max(3, max((len(match[0])+1 for match in re.finditer(r'`+', text)), default=3))
    return '\n' + fence + language + '\n' + text + '\n' + fence + '\n'


def render(node):
    if isinstance(node, str):
        return node
    tag = node.tag
    if tag in ('script', 'style'):
        return ''
    if tag == 'table':
        return render_table(node)
    if tag == 'pre':
        text = literal(node).strip('\n')
        code = next((child for child in node.children if isinstance(child, Node) and child.tag == 'code'), node)
        language = re.search(r'(?:^|\s)language-([a-zA-Z0-9_+#-]+)', code.attrs.get('class') or '')
        return code_block(text, language[1] if language else '')
    if tag == 'img':
        url = safe_url(node.attrs.get('src'), image=True)
        return '<img src="' + html.escape(url, quote=True) + '">' if url else node.attrs.get('alt', '')
    if tag == 'br':
        return '\n'
    if tag == 'hr':
        return '\n\n———\n\n'
    if tag == 'wbr':
        return ''
    if tag == 'input':
        return ('☑ ' if 'checked' in node.attrs else '☐ ') if node.attrs.get('type') == 'checkbox' else ''
    text = ''.join(render(child) for child in node.children)
    if tag == 'a':
        url = safe_url(node.attrs.get('href'))
        label = ' '.join(text.split())
        if contains_image(node):
            return text
        label = label.replace('[', '\\[').replace(']', '\\]')
        return '[' + label + '](' + url + ')' if url and label else text
    if tag in ('code', 'kbd', 'samp'):
        text = literal(node)
        return code_block(text) if '\n' in text or '`' in text else '`' + text + '`' if text else ''
    marker = {'strong': '**', 'b': '**', 'em': '*', 'i': '*', 'del': '~~', 's': '~~', 'strike': '~~', 'summary': '**'}.get(tag)
    if marker:
        if not text.strip():
            return ''
        leading = text[:len(text)-len(text.lstrip())]
        trailing = text[len(text.rstrip()):]
        return leading + marker + text.strip() + marker + trailing + ('\n' if tag == 'summary' else '')
    if re.fullmatch(r'h[1-6]', tag):
        return '\n\n' + '#' * min(int(tag[1]), 3) + ' ' + text.strip() + '\n\n'
    if tag == 'li':
        return '\n• ' + text.strip() + '\n'
    if tag == 'blockquote':
        return '\n' + '\n'.join('> ' + line for line in text.strip().splitlines()) + '\n'
    if tag in BLOCKS and tag not in ('td', 'th'):
        return '\n' + text.strip() + '\n'
    return text


def to_markdown(text):
    # Markdown URLs are literal; entity names in query parameters must not be decoded.
    # HTML attribute URLs still go through HTMLParser's normal attribute decoding.
    urls, prefix = [], '\uE002URL' + uuid.uuid4().hex + ':'
    def save_url(match):
        if match[0].startswith('<'):
            return match[0]
        urls.append(match[0])
        return prefix + str(len(urls)-1) + '\uE003'
    text = re.sub(r'''<(?:[^<>"']|"[^"]*"|'[^']*')*>|https?://[^\s<>]+''',
                  save_url, text, flags=re.I)
    parser = CommentHTML()
    parser.feed(text)
    parser.close()
    pattern = re.compile(re.escape(prefix) + r'(\d+)\uE003')
    def restore(node, inside_html=False):
        for index, child in enumerate(node.children):
            if isinstance(child, str):
                node.children[index] = pattern.sub(lambda match: html.unescape(urls[int(match[1])]) if inside_html else urls[int(match[1])], child)
            else:
                restore(child, True)
    restore(parser.root)
    return render(parser.root)
