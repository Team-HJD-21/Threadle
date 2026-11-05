"""Small, anchored excerpts from the review's original GitHub diff hunk."""
import re
import textwrap


LANGUAGES={'cs':'csharp','py':'python','js':'javascript','jsx':'jsx','ts':'typescript',
           'tsx':'tsx','json':'json','yml':'yaml','yaml':'yaml','c':'c','h':'c',
           'cpp':'cpp','hpp':'cpp','cc':'cpp','java':'java','rs':'rust','go':'go',
           'sql':'sql','sh':'bash','ps1':'powershell','css':'css','html':'html',
           'xml':'xml','csproj':'xml','shader':'hlsl','hlsl':'hlsl','md':'markdown'}


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
        if match:
            # A hunk is its own context; never borrow lines across a gap.
            old,new=int(match[1]),int(match[2]);rows.append(None);continue
        if old is None or not line or line[0] not in ' +-':continue
        sign=line[0];left=old if sign!='+' else None;right=new if sign!='-' else None
        rows.append((sign,left,right,line[1:]))
        if left is not None:old+=1
        if right is not None:new+=1
    column=1 if side=='LEFT' else 2
    # Show the code on the reviewed side, rather than mixing old/new line numbers.
    rows=[row for row in rows if row is None or row[column] is not None]
    selected=[i for i,row in enumerate(rows) if row and start<=row[column]<=target]
    if not selected:return ''  # Never show unrelated code when the anchor cannot be matched.
    beginning=selected[0]
    for index in range(selected[0]-1,max(-1,selected[0]-4),-1):
        if rows[index] is None:break
        beginning=index
    excerpt=rows[beginning:selected[-1]+1]
    # Remove only indentation shared by the excerpt, retaining relative indentation.
    code=textwrap.dedent('\n'.join(row[3] if row else '' for row in excerpt)).split('\n')
    numbered=list(zip(excerpt,code))
    if len(numbered)>12:numbered=numbered[:5]+[(None,'…')]+numbered[-6:]
    width=max(len(str(row[column])) for row,line in numbered if row)
    lines=[]
    for row,line in numbered:
        if row is None:
            lines.append(' '*(width+1)+'…')
            continue
        # Bound the optional preview, never the review comments below it.
        if len(line)>200:line=line[:199]+'…'
        # A backtick run in source must not close the enclosing Discord code block.
        prefix=str(row[column]).rjust(width)
        lines.append(prefix+(' '+line.replace('```','``\u200b`') if line else ''))
    path=thread.get('path') or ''
    label=path.rsplit('/',1)[-1].replace('`','ˋ')
    label+=':'+str(start)+'–'+str(target) if start!=target else ':'+str(target)
    when=' · **Outdated code**' if thread.get('isOutdated') else ''
    language=LANGUAGES.get(path.rsplit('.',1)[-1].lower(),'')
    return '`'+label+'`'+when+'\n```'+language+'\n'+'\n'.join(lines)+'\n```'
