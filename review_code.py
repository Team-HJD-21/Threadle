"""Small, anchored excerpts from the review's original GitHub diff hunk."""
import re


def preview(thread):
    root=thread['comments'][0]
    hunk=root.get('diffHunk') or ''
    target=root.get('originalLine') or thread.get('originalLine') or thread.get('line')
    if not hunk or not target:return ''  # File-level and binary reviews have no line excerpt.
    side=thread.get('diffSide','RIGHT')
    start=root.get('originalStartLine') or thread.get('originalStartLine') or target
    rows=[];old=new=None
    for line in hunk.splitlines():
        match=re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@',line)
        if match:old,new=int(match[1]),int(match[2]);continue
        if old is None or not line or line[0] not in ' +-':continue
        sign=line[0];left=old if sign!='+' else None;right=new if sign!='-' else None
        rows.append((sign,left,right,line[1:]))
        if left is not None:old+=1
        if right is not None:new+=1
    column=1 if side=='LEFT' else 2
    selected=[i for i,row in enumerate(rows) if row[column] is not None and start<=row[column]<=target]
    if not selected:return ''  # Never show unrelated code when the anchor cannot be matched.
    excerpt=rows[max(0,selected[0]-3):selected[-1]+1]
    if len(excerpt)>12:excerpt=excerpt[:5]+[None]+excerpt[-6:]
    lines=[]
    for row in excerpt:
        if row is None:lines.append('  …');continue
        sign,left,right,code=row
        number=left if side=='LEFT' else right
        if number is None:number=left if left is not None else right
        # Bound the optional preview, never the review comments below it.
        if len(code)>200:code=code[:199]+'…'
        lines.append(f'{sign} {number:>4} | {code}')
    path=thread.get('path') or ''
    label=path.rsplit('/',1)[-1].replace('`','ˋ')
    label+=':'+str(start)+'–'+str(target) if start!=target else ':'+str(target)
    when=' · **Outdated code**' if thread.get('isOutdated') else ''
    return '`'+label+'`'+when+'\n```diff\n'+'\n'.join(lines)+'\n```'
