import copy
import logging
import re

from discord_threads import ApiError,clip
from runtime_logging import log_exception


def enrich_commits(github,repo,event,payload):
    result=copy.deepcopy(payload)
    entries=result.get('_events') if event=='threadle_bundle' else [{'event':event,'payload':result}]
    for entry in entries or []:
        value=entry['payload']
        if entry['event'] not in ('pull_request','pull_request_target') or value.get('action')!='synchronize':
            continue
        value['_new_commits']=[];value['_commit_total']=None
        before,after=str(value.get('before','')),str(value.get('after',''))
        if not re.fullmatch(r'[a-fA-F0-9]{40,64}',before) or not re.fullmatch(r'[a-fA-F0-9]{40,64}',after):
            continue
        try:
            response=github.call('GET',f'/repos/{repo}/compare/{before}...{after}?per_page=100&page=1')
            value['_new_commits']=[{'sha':row['sha'],'title':str(row.get('commit',{}).get('message','')).split('\n',1)[0]} for row in response.get('commits',[]) if re.fullmatch(r'[a-fA-F0-9]{40,64}',str(row.get('sha','')))][:15]
            value['_commit_total']=int(response.get('total_commits',len(response.get('commits',[]))))
        except Exception as error:
            log_exception(logging.getLogger('commit-notifications'),'Commit comparison unavailable error=%s',type(error).__name__,level=logging.WARNING)
    return result


def render_commits(repo,payload,english):
    lines=[]
    for item in payload.get('_new_commits',[]):
        title=clip(item['title'].replace('@','@\u200b'),120)
        title=re.sub(r'([\\`*_{}\[\]()<>~|])',r'\\\1',title)
        sha=item['sha']
        lines.append('• [`'+sha[:7]+'`](https://github.com/'+repo+'/commit/'+sha+') '+title)
    total=payload.get('_commit_total')
    if total is not None and total>len(lines):
        lines.append('… '+str(total-len(lines))+(' more commits' if english else '개 커밋 더 있음'))
    if not lines:
        lines.append('Open the comparison to see the commit details.' if english else '커밋 상세는 비교 링크에서 확인해 주세요.')
    before,after=str(payload.get('before','')),str(payload.get('after',''))
    if re.fullmatch(r'[a-fA-F0-9]{40,64}',before) and re.fullmatch(r'[a-fA-F0-9]{40,64}',after):
        lines.append(('\n[View comparison](' if english else '\n[전체 변경 비교](')+f'https://github.com/{repo}/compare/{before}...{after})')
    return '\n'.join(lines)
