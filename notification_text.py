"""Lossless Discord description pagination with balanced fenced code blocks."""
import copy
import re
from discord_threads import clip


def units(value):return len(str(value).encode('utf-16-le'))//2


def fence_after(text,state,final):
    lines=text.splitlines(keepends=True)
    for index,line in enumerate(lines):
        if index==len(lines)-1 and not final and not line.endswith(('\n','\r')):continue
        match=re.match(r'^ {0,3}(`{3,32}|~{3,32})([^`~].*)?$',line.rstrip('\r\n'))
        if not match:continue
        marker,info=match[1],match[2] or ''
        if state:
            if marker[0]==state[0][0] and len(marker)>=len(state[0]) and not info.strip():state=None
        elif units(line)<200:state=(marker,line.rstrip('\r\n'))
    return state


def split_description(text,limit=3500):
    text=str(text or '')
    if units(text)<=limit:return [text]
    parts=[];state=None
    while text:
        prefix=(state[1]+'\n') if state else ''
        available=limit-units(prefix)
        for _ in range(5):
            part=clip(text,available)
            if len(part)<len(text):
                boundary=part.rfind('\n')
                if boundary>=len(part)//2:part=part[:boundary+1]
            final=len(part)==len(text)
            after=fence_after(part,state,final)
            suffix=('\n'+after[0]) if after and not final else ''
            excess=units(prefix+part+suffix)-limit
            if excess<=0:break
            available-=excess
        if not part:raise ValueError('Could not paginate description')
        parts.append(prefix+part+suffix)
        text=text[len(part):];state=after
    return parts


def description_embeds(original):
    parts=split_description(original.get('description',''))
    if len(parts)==1:return [original]
    result=[]
    for index,part in enumerate(parts):
        if index==0:
            embed=copy.deepcopy(original)
        else:
            embed={key:copy.deepcopy(value) for key,value in original.items() if key in ('title','url','author','color')}
            if embed.get('title'):embed['title']=clip(embed['title'],210)+f' ({index+1}/{len(parts)})'
        embed.pop('fields',None)
        embed['description']=part
        embed.pop('image',None);embed.pop('timestamp',None)
        if index==len(parts)-1:
            for key in ('image','timestamp','fields'):
                if key in original:embed[key]=copy.deepcopy(original[key])
        result.append(embed)
    return result
