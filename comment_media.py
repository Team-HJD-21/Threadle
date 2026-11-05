"""Render Discord uploads and GIF-picker previews as GitHub Markdown."""
import copy
from html.parser import HTMLParser
import re
from urllib.error import HTTPError
from urllib.parse import urlsplit,urljoin
from urllib.request import Request,build_opener

from notification_media import NoMediaRedirect

CDN={'cdn.discordapp.com','media.discordapp.net'}
PAGES={'tenor.com','www.tenor.com','giphy.com','www.giphy.com'}
MEDIA={'media.tenor.com','media1.tenor.com','media.giphy.com',
       'i.giphy.com','media0.giphy.com','media1.giphy.com','media2.giphy.com','media3.giphy.com','media4.giphy.com'}
TOKENS=re.compile(r'```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`|!?\[[^\]]*\]\([^)]*\)|https://[^\s<>]+')


def allowed(url,hosts):
    if not isinstance(url,str) or len(url)>4096 or any(c in url for c in '\r\n<>\\'):return False
    try:
        value=urlsplit(url)
        return value.scheme=='https' and value.hostname in hosts and value.port in (None,443) and not value.username and not value.password
    except ValueError:return False


def image(file):
    content_type=(file.get('content_type') or '').split(';')[0].lower()
    extension=urlsplit(file['url']).path.rsplit('.',1)[-1].lower()
    return content_type in ('image/png','image/jpeg','image/gif','image/webp') or extension in ('png','jpg','jpeg','gif','webp')


def inline(label,url):
    label=re.sub(r'[\[\]\\\r\n]','',label) or 'Image'
    return '!['+label+'](<'+url.replace('>','%3E')+'>)'


class Preview(HTMLParser):
    def __init__(self):super().__init__();self.images=[]
    def handle_starttag(self,tag,attrs):
        values=dict(attrs)
        if tag=='meta' and values.get('property',values.get('name')) in ('og:image','og:image:url','twitter:image'):
            url=values.get('content','')
            if allowed(url,MEDIA) and urlsplit(url).path.lower().endswith('.gif'):self.images.append(url)


def picker_image(source):
    """Inspect a bounded public preview, never forwarding credentials."""
    for _ in range(3):
        if not allowed(source,PAGES):return None
        try:
            request=Request(source,headers={'User-Agent':'Threadle-Media/1.0','Accept':'text/html'})
            with build_opener(NoMediaRedirect()).open(request,timeout=5) as response:
                raw=response.read(131072)
            parser=Preview();parser.feed(raw.decode('utf-8','replace'))
            return next(iter(parser.images),None)
        except HTTPError as error:
            target=urljoin(source,error.headers.get('Location',''))
            redirect=error.code in (301,302,303,307,308) and target!=source
            error.close()
            if not redirect:return None
            source=target
        except (OSError,ValueError):return None
    return None


def prepare(saved,current,resolve=picker_image):
    result=copy.deepcopy(saved)
    # Refresh expiring signatures only for the same original attachment path.
    if isinstance(current.get('attachments'),list):
        fresh={(urlsplit(a.get('url','')).hostname,urlsplit(a.get('url','')).path):a for a in current['attachments'] if allowed(a.get('url'),CDN)}
        for file in result['attachments']:
            key=(urlsplit(file['url']).hostname,urlsplit(file['url']).path)
            if key in fresh:
                file['url']=fresh[key]['url']
                file['content_type']=fresh[key].get('content_type') or file.get('content_type')
    previews={}
    sources={m[0] for m in TOKENS.finditer(saved['content']) if allowed(m[0],PAGES)}
    embeds=current.get('embeds',saved.get('embeds',[]))
    if not isinstance(embeds,list):embeds=[]
    for embed in embeds[:10]:
        if not isinstance(embed,dict) or embed.get('type') not in ('gifv','image','video'):continue
        source=embed.get('url','')
        if not allowed(source,PAGES):continue
        candidates=[(embed.get(key) or {}).get('url','') for key in ('image','thumbnail')]
        media=next((url for url in candidates if allowed(url,MEDIA) and urlsplit(url).path.lower().endswith('.gif')),None)
        # Never guess a GIF URL by rewriting an MP4 or PNG URL.
        if source in sources:
            if media and allowed(media,MEDIA):previews[source]=media
    for source in sources-previews.keys():
        media=resolve(source)
        if media and allowed(media,MEDIA):previews[source]=media
    result['picker_images']=previews
    return result


def render(content,payload):
    previews=payload.get('picker_images',{})
    content=TOKENS.sub(lambda m:inline('GIF',previews[m[0]]) if m[0] in previews else m[0],content)
    for file in payload['attachments']:
        if image(file):content+='\n\n'+inline(file.get('filename','Image'),file['url'])
        else:
            label=re.sub(r'[\[\]\\\r\n]','',file.get('filename','attachment')) or 'attachment'
            content+='\n\n['+label+']('+file['url']+')'
    return content
