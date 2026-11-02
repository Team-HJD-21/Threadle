import html
from html.parser import HTMLParser
import re
import json
import uuid
import io
import threading
import warnings
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.parse import urlsplit


def safe_image_url(value):
    value=html.unescape(str(value or '')).strip()
    try:
        parsed=urlsplit(value)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or len(value)>2048:
            return None
        return value
    except ValueError:
        return None


class Images(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls=[]

    def handle_starttag(self,tag,attrs):
        if tag.lower()=='img':
            value=safe_image_url(dict(attrs).get('src'))
            if value:self.urls.append(value)


def media_embeds(original):
    embed=dict(original)
    from notification_markdown import protect_code,adapt
    body,restore=protect_code(embed.get('description',''))
    body=adapt(body)
    parser=Images()
    parser.feed(body)
    urls=list(parser.urls)
    def markdown(match):
        url=safe_image_url(match.group(2).strip('<>'))
        if not url:return match.group(0)
        urls.append(url)
        return '['+(match.group(1) or 'Image')+']('+url+')'
    body=re.sub(r'!\[([^\]]*)\]\((https://[^\s)]+)(?:\s+"[^"]*")?\)',markdown,body)
    def image_tag(match):
        image=Images();image.feed(match.group(0))
        return '\n[Image]('+image.urls[0]+')\n' if image.urls else ''
    body=re.sub(r'<img\b[^>]*>',image_tag,body,flags=re.I|re.S)
    for value in re.findall(r'https://[^\s<>\]"\)]+',body):
        url=safe_image_url(value)
        if url and (re.search(r'\.(gif|png|jpe?g|webp)(?:[?#]|$)',url,re.I) or urlsplit(url).hostname=='github.com' and '/user-attachments/assets/' in urlsplit(url).path):
            urls.append(url)
    urls=list(dict.fromkeys(urls))
    from discord_threads import clip
    embed['description']=clip(restore(body).strip(),3500)
    if not urls:return [embed]
    embed['image']={'url':urls[0]}
    result=[embed]
    for url in urls[1:]:
        result.append({'image':{'url':url},'color':embed.get('color',0x2DA44E)})
    if embed.get('timestamp') and len(result)>1:
        result[-1]['timestamp']=embed.pop('timestamp')
    return result


MAX_IMAGE_BYTES = 4 * 1024 * 1024
MAX_SOURCE_BYTES = 20 * 1024 * 1024
IMAGE_WORKER = threading.BoundedSemaphore(1)


def fit_image(raw, extension, mime):
    if len(raw) <= MAX_IMAGE_BYTES:
        return raw,extension,mime
    if len(raw) > MAX_SOURCE_BYTES:
        return None
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    if int(Image.__version__.split('.')[0]) < 13:
        return None
    try:
        with IMAGE_WORKER, warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw), formats=('PNG','JPEG','GIF','WEBP')) as original:
                pixels=original.width*original.height
                frames=getattr(original,'n_frames',1)
                if pixels>24_000_000 or frames>150 or pixels*frames>120_000_000:
                    return None
                if frames>1:
                    resized=[];durations=[]
                    for index in range(frames):
                        original.seek(index)
                        frame=original.convert('RGBA')
                        frame.thumbnail((640,640),Image.Resampling.LANCZOS)
                        resized.append(frame.quantize(colors=128,method=Image.Quantize.FASTOCTREE))
                        durations.append(max(20,int(original.info.get('duration',100))))
                    output=io.BytesIO()
                    resized[0].save(output,format='GIF',save_all=True,append_images=resized[1:],
                                    duration=durations,loop=original.info.get('loop',0),disposal=2,optimize=False)
                    result=output.getvalue()
                    return (result,'gif','image/gif') if len(result)<=MAX_IMAGE_BYTES else None
                frame=ImageOps.exif_transpose(original)
                transparent=frame.mode in ('RGBA','LA') or 'transparency' in frame.info
                frame=frame.convert('RGBA' if transparent else 'RGB')
                for edge in (1600,1200,960):
                    frame.thumbnail((edge,edge),Image.Resampling.LANCZOS)
                    output=io.BytesIO()
                    if transparent:
                        frame.save(output,format='PNG',compress_level=6)
                        result=output.getvalue();kind=('png','image/png')
                    else:
                        frame.save(output,format='JPEG',quality=82,optimize=True)
                        result=output.getvalue();kind=('jpg','image/jpeg')
                    if len(result)<=MAX_IMAGE_BYTES:
                        return result,*kind
    except (OSError,ValueError,EOFError,Image.DecompressionBombError,Image.DecompressionBombWarning):
        return None
    return None


class NoMediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_media_url(url):
    if not safe_image_url(url):
        return False
    parsed = urlsplit(url)
    try:
        port=parsed.port
    except ValueError:
        return False
    if port not in (None,443):
        return False
    host = parsed.hostname.lower()
    return ((host == 'github.com' and parsed.path.startswith('/user-attachments/assets/'))
            or host in ('user-images.githubusercontent.com','private-user-images.githubusercontent.com',
                        'github-production-user-asset-6210df.s3.amazonaws.com'))


def image_payload(raw):
    if raw.startswith((b'GIF87a', b'GIF89a')):
        return fit_image(raw,'gif','image/gif')
    if raw.startswith(b'\x89PNG\r\n\x1a\n'):
        return fit_image(raw,'png','image/png')
    if raw.startswith(b'\xff\xd8\xff'):
        return fit_image(raw,'jpg','image/jpeg')
    if raw[:4] == b'RIFF' and raw[8:12] == b'WEBP':
        return fit_image(raw,'webp','image/webp')
    return None


def public_image(url):
    opener = build_opener(NoMediaRedirect())
    for _ in range(4):
        if not github_media_url(url):
            return None
        request = Request(url, headers={'User-Agent':'Threadle-Media/1.0','Accept':'image/*'})
        try:
            with opener.open(request, timeout=3) as response:
                if int(response.headers.get('Content-Length','0')) > MAX_SOURCE_BYTES:
                    return None
                raw = response.read(MAX_SOURCE_BYTES+1)
            if len(raw) > MAX_SOURCE_BYTES:
                return None
            return image_payload(raw)
        except HTTPError as error:
            location = error.headers.get('Location','')
            status = error.code
            error.close()
            if status not in (301,302,303,307,308):
                return None
            from urllib.parse import urljoin
            url = urljoin(url,location)
        except (URLError,OSError,ValueError):
            return None
    return None


def installation_image(url, token):
    """Fetch a private GitHub attachment with repo-scoped auth; never forward auth on redirects."""
    if not token or not github_media_url(url):
        return None
    parsed=urlsplit(url)
    if parsed.hostname != 'github.com' or not parsed.path.startswith('/user-attachments/assets/'):
        return None
    opener=build_opener(NoMediaRedirect())
    request=Request(url,headers={'User-Agent':'Threadle-Media/1.0','Accept':'image/*',
                                 'Authorization':'Bearer '+token})
    try:
        with opener.open(request,timeout=5) as response:
            if int(response.headers.get('Content-Length','0')) > MAX_SOURCE_BYTES:
                return None
            raw=response.read(MAX_SOURCE_BYTES+1)
        if len(raw)>MAX_SOURCE_BYTES:
            return None
        return image_payload(raw)
    except HTTPError as error:
        location=error.headers.get('Location','')
        status=error.code
        error.close()
        if status not in (301,302,303,307,308) or not location:
            return None
        from urllib.parse import urljoin
        redirected=urljoin(url,location)
        if not github_media_url(redirected):
            return None
        target=urlsplit(redirected)
        if target.hostname == 'github.com':
            return None
        return public_image(redirected)
    except (URLError,OSError,ValueError):
        return None


def attach_public_images(embeds, authenticated_fetcher=None):
    files=[]; seen={}; attempted=0
    for embed in embeds:
        url=embed.get('image',{}).get('url','')
        try:
            supported=github_media_url(url)
        except ValueError:
            supported=False
        if not supported:
            continue
        if url in seen:
            embed['image']={'url':seen[url]}
            continue
        if attempted >= 2:
            continue
        attempted+=1
        try:
            image=public_image(url)
        except Exception:
            image=None
        if image is None and authenticated_fetcher and urlsplit(url).hostname in ('github.com', 'private-user-images.githubusercontent.com'):
            try:
                image=authenticated_fetcher(url)
            except Exception:
                image=None
        if image:
            raw,extension,mime=image
            name='threadle-image-'+str(len(files))+'.'+extension
            files.append((name,mime,raw))
            seen[url]='attachment://'+name
            embed['image']={'url':seen[url]}
    return files


def multipart_message(data):
    files=data['_files']
    payload={key:value for key,value in data.items() if key!='_files'}
    boundary='threadle-'+uuid.uuid4().hex
    parts=[]
    payload['attachments']=[{'id':i,'filename':name} for i,(name,_,_) in enumerate(files)]
    parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n').encode()+json.dumps(payload).encode()+b'\r\n')
    for index,(name,mime,raw) in enumerate(files):
        if not re.fullmatch(r'threadle-image-\d+\.(gif|png|jpg|webp)',name) or len(raw)>MAX_IMAGE_BYTES:
            raise ValueError('Invalid media attachment')
        parts.append(('--'+boundary+'\r\nContent-Disposition: form-data; name="files['+str(index)+']"; filename="'+name+'"\r\nContent-Type: '+mime+'\r\n\r\n').encode()+raw+b'\r\n')
    return b''.join(parts)+('--'+boundary+'--\r\n').encode(), 'multipart/form-data; boundary='+boundary


def rendered_installation_image(url, token, repository):
    # Only GitHub API receives the repository-scoped token; image CDN requests are anonymous.
    if not token or not github_media_url(url) or urlsplit(url).hostname not in ('github.com', 'private-user-images.githubusercontent.com'):
        return None
    opener = build_opener(NoMediaRedirect())
    body = json.dumps({'text': '<img src="' + html.escape(url, quote=True) + '">',
                       'mode': 'gfm', 'context': repository}).encode()
    request = Request('https://api.github.com/markdown', data=body, method='POST',
                      headers={'User-Agent': 'Threadle-Media/1.0', 'Accept': 'text/html',
                               'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token,
                               'X-GitHub-Api-Version': '2022-11-28'})
    try:
        with opener.open(request, timeout=5) as response:
            raw = response.read(256 * 1024 + 1)
        if len(raw) > 256 * 1024:
            return None
        parser = Images()
        parser.feed(raw.decode('utf-8'))
        for source in parser.urls:
            if github_media_url(source) and urlsplit(source).hostname != 'github.com':
                image = public_image(source)
                if image:
                    return image
    except HTTPError as error:
        logging.getLogger('notification-media').warning('Private image render failed HTTP=%s; check repository Contents read permission', error.code)
        error.close()
        return None
    except (URLError, OSError, ValueError):
        return None
    return None
