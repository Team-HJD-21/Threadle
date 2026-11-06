import time
from discord_threads import clip


def summary_text(value,limit=160):
    import re
    text=' '.join(str(value or '').split()).replace('@','@\u200b')
    return re.sub(r'([\\`*_\[\]<>~|#])',r'\\\1',clip(text,limit))


def pr_counts(count,review,drafts,english=False):
    return (f'**Open PRs** `{count}` · **Awaiting review** `{review}` · **Drafts** `{drafts}`' if english
            else f'**열린 PR** `{count}` · **리뷰 대기** `{review}` · **Draft** `{drafts}`')


def summary_item(repo,kind,item,english=False):
    number=int(item['number'])
    path='pull' if kind=='pr' else 'issues'
    label='PR' if kind=='pr' else 'Issue'
    heading=f'**[{label} #{number}](https://github.com/{repo}/{path}/{number})**'
    if kind=='pr':
        review=item.get('review') or item.get('requested_reviewers') or item.get('requested_teams')
        state='Draft' if item.get('draft') else ('Awaiting review' if english else '리뷰 대기') if review else 'Open'
        heading+=' · `'+state+'`'
    return heading+'\n'+summary_text(item.get('title',''))


def summary_pages(entries,header='',limit=2700):
    """Keep each number/title pair intact when Discord needs another page."""
    from notification_text import units
    pages=[];current=header
    for entry in entries:
        candidate=(current+'\n\n' if current else '')+entry
        if current and units(candidate)>limit:
            pages.append(current);current=(header+'\n\n' if header else '')+entry
        else:current=candidate
    if current:pages.append(current)
    return pages or [header]


def open_items(github, repo, kind):
    field = {'pr': 'pullRequests', 'issue': 'issues', 'discussion': 'discussions'}[kind]
    owner, name = repo.split('/')
    args = 'first:100,after:$after,orderBy:{field:CREATED_AT,direction:ASC}' + (',states:OPEN' if kind != 'discussion' else '')
    fields = 'number title url createdAt' + (' closed' if kind == 'discussion' else '')
    query = ('query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){'
             + field + '(' + args + '){totalCount nodes{' + fields + '} pageInfo{hasNextPage endCursor}}}}')
    items, cursor, deadline = [], None, time.monotonic() + 120
    for _ in range(100):
        if time.monotonic() > deadline:
            raise ValueError('The summary timed out before the complete list was read. Please try again.')
        response = github.call('POST', '/graphql', {'query': query, 'variables': {'owner': owner, 'name': name, 'after': cursor}})
        if response.get('errors') or not response.get('data', {}).get('repository'):
            raise ValueError('GitHub could not read the requested summary. Check app permissions.')
        page = response['data']['repository'][field]
        items.extend(item for item in page['nodes'] if not item.get('closed', False))
        if not page['pageInfo']['hasNextPage']:
            return items
        after = page['pageInfo']['endCursor']
        if not after or after == cursor:
            raise ValueError('The summary list is incomplete. Please try again.')
        cursor = after
    raise ValueError('The list is too large to read completely in one request.')


def summary_page(repo, kind, items, page=1):
    if type(page) is not int or page < 1:
        raise ValueError('Choose a page number of 1 or higher.')
    size, count = 15, len(items)
    pages = max(1, (count + size - 1) // size)
    if page > pages:
        raise ValueError(f'This summary has {pages} page(s).')
    label = {'pr': 'PRs', 'issue': 'issues', 'discussion': 'discussions'}[kind]
    lines = [f'**{repo}**', f'{count} open {label}.', '']
    for item in items[(page-1)*size:page*size]:
        title = clip(item['title'].replace('\n', ' '), 75)
        title = title.replace('`', "'").replace('@', '@\u200b')
        lines.append(f'• #{item["number"]} {title}')
    if pages > 1:
        lines += ['', f'Page {page}/{pages} · Use /summary page:{min(page+1,pages)} to see another page.']
    path = {'pr': 'pulls', 'issue': 'issues', 'discussion': 'discussions'}[kind]
    lines += ['', f'https://github.com/{repo}/{path}', 'Checked now. Scheduled summaries are unchanged.']
    return '\n'.join(lines)


def open_prs(github, repo):
    return list(github.pages(f'/repos/{repo}/pulls?state=open&sort=created&direction=asc'))


def issue_digest(github, repo):
    owner, name = repo.split('/')
    query = """query($owner:String!,$name:String!,$after:String){repository(owner:$owner,name:$name){
      oldest:issues(first:5,states:OPEN,orderBy:{field:CREATED_AT,direction:ASC}){
        totalCount nodes{number title url}}
      completed:issues(first:100,after:$after,states:CLOSED,orderBy:{field:UPDATED_AT,direction:DESC}){
        nodes{number title url closedAt updatedAt stateReason} pageInfo{hasNextPage endCursor}}
    }}"""
    cursor, completed, deadline = None, [], time.monotonic() + 120
    for _ in range(100):
        if time.monotonic() > deadline:
            raise ValueError('The issue digest timed out before completion.')
        result = github.call('POST', '/graphql', {'query':query, 'variables':{'owner':owner, 'name':name, 'after':cursor}})
        if result.get('errors') or not result.get('data', {}).get('repository'):
            raise ValueError('GitHub could not read the issue digest. Check issue permissions.')
        data = result['data']['repository']
        page = data['completed']
        completed.extend(item for item in page['nodes'] if item.get('stateReason') == 'COMPLETED' and item.get('closedAt'))
        completed.sort(key=lambda item:item['closedAt'], reverse=True)
        completed = completed[:5]
        if (not page['pageInfo']['hasNextPage'] or
                len(completed) == 5 and page['nodes'] and page['nodes'][-1]['updatedAt'] <= completed[-1]['closedAt']):
            return data['oldest']['totalCount'], data['oldest']['nodes'], completed
        after = page['pageInfo']['endCursor']
        if not after or after == cursor:
            raise ValueError('The completed issue list is incomplete.')
        cursor = after
    raise ValueError('The completed issue list is too large for this summary.')


def all_repository_summaries(service, bindings):
    import re
    pages=[]
    for binding in sorted(bindings,key=lambda item:item.repo.casefold()):
        en=binding.config.get('language')=='en'
        page='**'+binding.repo+'**'
        for kind,label,path in [('pr','PR','pull'),('issue','Issue','issues'),('discussion','Discussion','discussions')]:
            try:
                items=open_items(service.github(binding),binding.repo,kind)
                items.sort(key=lambda item:item.get('createdAt',''))
            except Exception:
                line='\n\n**'+label+'** · '+('Could not read this list. Check app access.' if en else '목록 조회 실패 · 앱 접근 권한을 확인하세요.')
                if len((page+line).encode('utf-16-le'))//2>1850:
                    pages.append(page);page='**'+binding.repo+'**'
                page+=line
                continue
            heading='\n\n**'+label+' · '+str(len(items))+(' open**' if en else '개 열림**')
            if len((page+heading).encode('utf-16-le'))//2>1850:
                pages.append(page);page='**'+binding.repo+'**'
            page+=heading
            visible=items if kind=='pr' else items[:10]
            for item in visible:
                title=str(item['title']).replace('\n',' ').replace('@','@\u200b')
                title=re.sub(r'([\\`*_{}\[\]<>~|])',r'\\\1',title)
                line='\n• ['+title+'](https://github.com/'+binding.repo+'/'+path+'/'+str(int(item['number']))+')'
                if len((page+line).encode('utf-16-le'))//2>1850:
                    pages.append(page);page='**'+binding.repo+' · '+label+'**'
                page+=line
            if not visible:
                page+='\n'+('None.' if en else '없음')
            remaining=len(items)-len(visible)
            if remaining:
                suffix='\n-# '+(str(remaining)+' remaining '+label.lower()+'s…' if en else '그 외 '+str(remaining)+'개 남음…')
                if len((page+suffix).encode('utf-16-le'))//2>1850:
                    pages.append(page);page='**'+binding.repo+' · '+label+'**'
                page+=suffix
        pages.append(page)
    return pages or ['No repositories are connected to this server.']


def open_issue_digest(github,repo):
    owner,name=repo.split('/')
    query='query($owner:String!,$name:String!){repository(owner:$owner,name:$name){oldest:issues(first:5,states:OPEN,orderBy:{field:CREATED_AT,direction:ASC}){totalCount nodes{number title url}}}}'
    response=github.call('POST','/graphql',{'query':query,'variables':{'owner':owner,'name':name}})
    if response.get('errors') or not response.get('data',{}).get('repository'):
        raise ValueError('GitHub could not read the open issue list.')
    result=response['data']['repository']['oldest']
    return result['totalCount'],result['nodes']
