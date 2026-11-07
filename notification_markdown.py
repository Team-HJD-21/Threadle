"""Keep Discord-compatible Markdown and adapt GitHub-only presentation."""
import re
import uuid

CODE_PATTERN=(r'^ {0,3}(?P<ticks>`{3,})[^\n]*\n.*?(?:^ {0,3}(?P=ticks)`*[ \t]*\r?$|\Z)'
              r'|^ {0,3}(?P<tildes>~{3,})[^\n]*\n.*?(?:^ {0,3}(?P=tildes)~*[ \t]*\r?$|\Z)|`+[^`\n]+`+')


def strip_comments(text):
    # Match literal code and comments in source order. A fence inside a hidden
    # comment must not hide its closing --> from the comment remover.
    return re.sub(CODE_PATTERN+r'|<!--.*?(?:-->|\Z)',
                  lambda m:'' if m.group(0).startswith('<!--') else m.group(0),
                  text,flags=re.M|re.S)


def protect_code(text):
    text=strip_comments(text)
    blocks=[]
    prefix="\uE000THREADLE"+uuid.uuid4().hex+":"
    def save(match):
        blocks.append(match.group(0))
        return prefix+str(len(blocks)-1)+'\uE001'
    # Fences and inline code are literal: do not interpret their image or HTML examples.
    masked=re.sub(CODE_PATTERN,save,text,flags=re.M|re.S)
    def restore(value):
        return re.sub(re.escape(prefix)+r'(\d+)\uE001',lambda m:blocks[int(m.group(1))],value)
    return masked,restore


def adapt(text):
    from notification_html import to_markdown
    text,restore=protect_code(text)
    text=to_markdown(text)
    text,restore_html=protect_code(text)
    text=re.sub(r'(?mi)^(\s*)[-*+] \[([ xX])\] ',lambda m:m.group(1)+('☑ ' if m.group(2).lower()=='x' else '☐ '),text)
    lines=text.splitlines();result=[];index=0
    def cells(line):return [cell.strip() for cell in line.strip().strip('|').split('|')]
    while index<len(lines):
        if index+1<len(lines) and '|' in lines[index] and re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*',lines[index+1]):
            headers=cells(lines[index]);index+=2
            while index<len(lines) and '|' in lines[index] and lines[index].strip():
                values=cells(lines[index]);result.append('• '+' · '.join('**'+(headers[n] if n<len(headers) else str(n+1))+'**: '+value for n,value in enumerate(values)));index+=1
            continue
        result.append(lines[index]);index+=1
    return restore(restore_html('\n'.join(result)))
