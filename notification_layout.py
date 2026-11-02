import copy
import json
from discord_threads import clip, iso_timestamp, Topic

CREATION = {'issues', 'pull_request', 'pull_request_target', 'discussion'}


def issue_object(payload):
    return payload.get('pull_request') or payload.get('issue') or payload.get('discussion') or {}


def display(value):
    if value is None:
        return ''
    if isinstance(value, list):
        return ', '.join(filter(None, (display(item) for item in value)))
    if isinstance(value, dict):
        for key in ('single_select_option', 'multi_select_options', 'name', 'issue_field_name', 'label', 'login', 'title', 'value', 'text', 'number', 'date', 'single_select_option', 'option'):
            if key in value:
                return display(value[key])
        return ''
    return str(value)


def field_details(payload, english=False):
    if payload.get('_resolved_fields'):
        return [str(name)+': '+str(value) for name,value in payload['_resolved_fields']]
    action = payload.get('action')
    if action in ('typed', 'untyped'):
        value = payload.get('issue_type') or issue_object(payload).get('type') or payload.get('changes', {}).get('type', {}).get('from')
        return [('Type: ' if english else '유형: ') + (display(value) or ('Not provided by GitHub' if english else 'GitHub에서 정보 미제공'))]
    if action not in ('field_added', 'field_removed'):
        return []
    field = payload.get('issue_field') or payload.get('field') or {}
    entry = payload.get('issue_field_value') or {}
    if not field and isinstance(entry, dict):
        field = entry.get('field') or entry.get('issue_field') or {}
    name = (field.get('name') or field.get('issue_field_name') if isinstance(field,dict) else display(field)) or (entry.get('issue_field_name') if isinstance(entry,dict) else None) or ('Field' if english else '필드')
    missing = object()
    value = payload.get('field_value', missing)
    if value is missing:
        value = (entry.get('single_select_option') or entry.get('multi_select_options') or entry.get('value', missing)) if isinstance(entry, dict) else entry
    if value is missing and isinstance(field, dict):
        value = field.get('value', missing)
    if action == 'field_removed':
        text = 'Cleared' if english else '해제됨'
    else:
        text = display(value) if value is not missing else ''
        if not text:
            text = 'Value not provided by GitHub' if english else 'GitHub에서 값 미제공'
    return [name + ': ' + text]


def snapshot_fields(obj):
    raw = obj.get('issue_fields', obj.get('issue_field_values', obj.get('field_values', obj.get('fields', None))))
    if raw is None:
        return None
    if isinstance(raw, dict):
        return {str(name): display(value) for name, value in raw.items()}
    values = {}
    for entry in raw if isinstance(raw,list) else []:
        if isinstance(entry,dict):
            field = entry.get('field') or entry.get('issue_field') or entry
            name = entry.get('issue_field_name') or (field.get('name') if isinstance(field,dict) else display(field))
            if name:
                values[name] = display(entry.get('single_select_option') or entry.get('multi_select_options') or entry.get('value', entry.get('field_value')))
    return values


def creation_embed(topic, payload, language='ko'):
    en = language == 'en'
    obj = topic.metadata
    none = 'None' if en else '없음'
    unknown = 'Not provided by GitHub' if en else 'GitHub에서 정보 미제공'
    fields = []
    def add(ko, english, value, inline=True):
        fields.append({'name': english, 'value': clip(value or none, 600), 'inline': inline})
    if topic.kind == 'pr':
        head,base = obj.get('head',{}).get('ref'),obj.get('base',{}).get('ref')
        branch = '`'+str(head).replace('`','')+'` → `'+str(base).replace('`','')+'`' if head and base else unknown
        add('브랜치','Branches',branch,False)
    actor = display(obj.get('user')) or display(payload.get('sender')) or 'GitHub'
    if topic.kind == 'discussion':
        add('카테고리','Category',display(obj.get('category')) or unknown)
        add('라벨','Labels',display(obj.get('labels')) if 'labels' in obj else unknown,False)
        answer = obj.get('answer_html_url') or (obj.get('answer') or {}).get('html_url')
        add('채택된 답변','Accepted answer',('[View answer](' if en else '[답변 보기](')+answer+')' if answer else none,False)
    else:
        if topic.kind == 'issue':
            add('유형','Type',display(obj.get('type')) if 'type' in obj else unknown)
        add('담당자','Assignees',display(obj.get('assignees')) if 'assignees' in obj else unknown)
        add('라벨','Labels',display(obj.get('labels')) if 'labels' in obj else unknown)
        add('마일스톤','Milestone',display(obj.get('milestone')) if 'milestone' in obj else unknown)
        if topic.kind == 'pr':
            people = display(obj.get('requested_reviewers'))
            teams = display(obj.get('requested_teams'))
            add('리뷰 요청','Reviewers',', '.join(filter(None,[people,teams])) if 'requested_reviewers' in obj else unknown)
            add('상태','Status',('Draft' if obj.get('draft') else 'Ready for review') if 'draft' in obj else unknown)
        else:
            values=snapshot_fields(obj)
            if not values:
                add('추가 필드','Issue fields',unknown if values is None else none,False)
            else:
                chunk=[];size=0;part=1
                for name,value in values.items():
                    line=clip(name+': '+(value or none),500)
                    if size+len(line)>550 and chunk:
                        add('추가 필드'+(' ('+str(part)+')' if part>1 else ''),'Issue fields'+(' ('+str(part)+')' if part>1 else ''),'\n'.join(chunk),False)
                        part+=1;chunk=[];size=0
                    chunk.append(line);size+=len(line)+1
                if chunk:add('추가 필드'+(' ('+str(part)+')' if part>1 else ''),'Issue fields'+(' ('+str(part)+')' if part>1 else ''),'\n'.join(chunk),False)
    for entry in fields:
        entry['inline'] = False
    compact_names = {'유형','Type','담당자','Assignees','마일스톤','Milestone','리뷰 요청','Reviewers','상태','Status','카테고리','Category'}
    compact = [entry for entry in fields if entry['name'] in compact_names]
    if compact:
        lines = ['**'+entry['name']+'** · '+entry['value'] for entry in compact]
        fields = [entry for entry in fields if entry not in compact]
        position = 1 if topic.kind == 'pr' else 0
        fields.insert(position, {'name':'Details', 'value':clip('\n'.join(lines),1000),'inline':False})
    body = str(obj.get('body') or '').strip()
    result = {'title': clip(f'{topic.kind.upper()} #{topic.number} · {topic.title}',230), 'url':topic.url,
              'description':clip(body,3000) or ('No description provided.' if en else '설명 없음'),
              'author':{'name':clip(actor,100)},
              'fields':fields,'color':{'issue':0x2DA44E,'pr':0x8957E5,'discussion':0x0969DA}[topic.kind]}
    posted=iso_timestamp(obj.get('created_at'))
    if posted:
        result['timestamp']=posted
    return result


def merge_snapshot(topic, entries):
    obj = copy.deepcopy(topic.metadata)
    for event,payload in entries:
        if event in CREATION:
            obj.update(copy.deepcopy(issue_object(payload)))
        action=payload.get('action')
        if action in ('field_added','field_removed'):
            existing=snapshot_fields(obj) or {}
            field=payload.get('issue_field') or payload.get('field') or {}
            if display(field):
                if action=='field_removed':existing.pop(display(field),None)
                else:existing[display(field)]=field_details(payload,True)[0].split(': ',1)[-1]
                obj['issue_fields']=existing
    return Topic(topic.repo,topic.kind,topic.number,obj.get('title',topic.title),topic.url,obj)


def bundle_embed(topic, payload, language):
    from discord_threads import make_embed
    from alert_feed import activity,english_activity
    entries=[(item['event'],item['payload']) for item in payload['_events']]
    creations=[(event,value) for event,value in entries if event in CREATION and value.get('action') in ('opened','created')]
    embeds=[]
    if creations:
        event,first=creations[0]
        base=Topic(topic.repo,topic.kind,topic.number,topic.title,topic.url,topic.metadata)
        merged=merge_snapshot(base,entries)
        embeds.append(creation_embed(merged,first,language))
    changes=[]
    actors={display(value.get('sender')) or 'GitHub' for event,value in entries if event in CREATION and value.get('action') not in ('opened','created')}
    for event,value in entries:
        if event in CREATION and value.get('action') in ('opened','created'):
            continue
        if event in CREATION:
            if creations and value.get('action') in ('assigned','unassigned','labeled','unlabeled','milestoned','demilestoned','review_requested','review_request_removed','typed','untyped','field_added','field_removed','edited'):
                continue
            details=make_embed(topic,event,value,language)
            label=(english_activity if language=='en' else activity)(event,value)
            actor=display(value.get('sender')) or 'GitHub'
            prefix='' if len(actors)==1 else '**'+actor+'**\n'
            changes.append(prefix+'**'+label+'**'+('\n'+details['description'] if details['description'] else ''))
        else:
            embeds.append(make_embed(topic,event,value,language))
    if changes:
        heading = 'Updates'
        state=next((value for event,value in reversed(entries) if event in CREATION and value.get('action') in ('closed','deleted','reopened','opened','created','answered','unanswered')),None)
        if state:
            action=state.get('action')
            if topic.kind=='pr' and action=='closed' and state.get('pull_request',{}).get('merged'):
                action='merged'
            heading={'closed':'Closed','deleted':'Deleted','reopened':'Reopened','merged':'Merged','answered':'Answered','unanswered':'Unanswered'}.get(action,heading)
        text='\n\n'.join(changes)
        change_embeds=[]
        while text:
            part=clip(text,3300)
            text=text[len(part):]
            change_embeds.append({'title':clip(f'{topic.kind.upper()} #{topic.number} · '+heading,230),
                             'description':part,'url':topic.url,
                             **({'author':{'name':clip(next(iter(actors)),100)}} if len(actors)==1 else {}),
                             'color':{'issue':0x2DA44E,'pr':0x8957E5,'discussion':0x0969DA}[topic.kind]})
        embeds=([embeds[0]]+change_embeds+embeds[1:]) if creations else change_embeds+embeds
    if not embeds:
        embeds=[creation_embed(topic,payload,language)]
    last=entries[-1][1]
    obj=last.get('comment') or last.get('review') or issue_object(last)
    posted=iso_timestamp(obj.get('submitted_at') or obj.get('updated_at') or obj.get('created_at'))
    if posted:embeds[-1]['timestamp']=posted
    return {'_embeds':embeds}


def message_pages(embed):
    from notification_media import media_embeds
    source=[item for original in (embed.get('_embeds') or [embed]) for item in media_embeds(original)]
    units=lambda value:len(str(value).encode('utf-16-le'))//2
    embeds=[]
    for original in source:
        current=dict(original,fields=[])
        if not current.get('description'):
            current.pop('description',None)
        size=units(current.get('title',''))+units(current.get('description',''))+units(current.get('author',{}).get('name',''))
        for field in original.get('fields',[]):
            extra=units(field.get('name',''))+units(field.get('value',''))
            if size+extra>5400 or len(current['fields'])>=25:
                embeds.append(current)
                current={key:value for key,value in original.items() if key not in ('description','fields','timestamp')}
                current['fields']=[]
                size=units(current.get('title',''))
            current['fields'].append(field);size+=extra
        embeds.append(current)
    pages=[];current=[];used=0
    for item in embeds:
        count=units(item.get('title',''))+units(item.get('description',''))+units(item.get('author',{}).get('name',''))+sum(units(f.get('name',''))+units(f.get('value','')) for f in item.get('fields',[]))
        if current and (item.get('image') or any(e.get('image') for e in current) or len(current)>=10 or used+count>5700):
            pages.append(current);current=[];used=0
        current.append(item);used+=count
    if current:pages.append(current)
    return pages


def topic_identity(event,payload):
    if event not in CREATION | {'issue_comment','pull_request_review','pull_request_review_comment','discussion_comment'}:
        return None
    obj=issue_object(payload)
    kind='pr' if 'pull_request' in payload or 'pull_request' in obj else 'discussion' if 'discussion' in payload else 'issue'
    number=obj.get('number')
    return str(payload.get('repository',{}).get('id'))+':'+kind+':'+str(number) if number else None


def group_payload(entries):
    from discord_threads import silent_review
    entries=[(event,payload) for event,payload in entries if not silent_review(event,payload)]
    if not entries:
        return {'_events':[], '_silent':True}
    unique=[];previous_milestone=None
    for event,value in entries:
        if value.get('action') in ('milestoned','demilestoned'):
            obj=issue_object(value)
            fingerprint=json.dumps([obj.get('number'),value.get('action'),value.get('milestone',{}).get('id'),obj.get('updated_at')])
            if fingerprint == previous_milestone:
                continue
            previous_milestone=fingerprint
        else:
            previous_milestone=None
        unique.append((event,value))
    entries=unique
    first_creation=next((p for e,p in entries if e in CREATION and p.get('action') in ('opened','created')),None)
    base=copy.deepcopy(first_creation or entries[0][1])
    base['_events']=[{'event':event,'payload':payload} for event,payload in entries]
    base['_created']=bool(first_creation)
    obj=issue_object(base)
    kind='pr' if 'pull_request' in base or 'pull_request' in obj else 'discussion' if 'discussion' in base else 'issue'
    key={'pr':'pull_request','discussion':'discussion','issue':'issue'}[kind]
    topic=Topic(base['repository']['full_name'],kind,int(obj['number']),obj.get('title',''),' ',obj)
    base[key]=merge_snapshot(topic,entries).metadata
    base.pop('comment',None);base.pop('review',None)
    return base


def hydrate_issue_fields(topic, payload, github):
    if topic.kind != 'issue':
        return topic
    created=payload.get('_created') or (not payload.get('_events') and not (payload.get('comment') or payload.get('review')) and payload.get('action') in ('opened','created'))
    if not created or snapshot_fields(topic.metadata) is not None:
        return topic
    from discord_threads import ApiError
    try:
        fields=list(github.pages(f'/repos/{topic.repo}/issues/{topic.number}/issue-field-values'))
    except ApiError as error:
        if error.status in (403,404,410,422):
            return topic
        raise
    if not all(isinstance(item,dict) and isinstance(item.get('issue_field_name'),str) for item in fields):
        return topic
    obj=dict(topic.metadata,issue_fields=fields)
    return Topic(topic.repo,topic.kind,topic.number,topic.title,topic.url,obj)


def resolve_changed_fields(topic,payload,github):
    if topic.kind!='issue':
        return
    values=None
    entries=[item['payload'] for item in payload.get('_events',[]) if item['event']=='issues'] or [payload]
    for child in entries:
        if child.get('action')!='field_added' or child.get('_resolved_fields'):
            continue
        lines=field_details(child,True)
        if lines and 'not provided' not in lines[0].lower() and not lines[0].startswith('Field:'):
            continue
        if values is None:
            from discord_threads import ApiError
            try:
                values=list(github.pages(f'/repos/{topic.repo}/issues/{topic.number}/issue-field-values'))
            except ApiError as error:
                if error.status in (403,404,410,422):return
                raise
        field=child.get('issue_field') or child.get('field') or {}
        entry=child.get('issue_field_value') or {}
        identity=(field.get('id') if isinstance(field,dict) else None) or (entry.get('issue_field_id') if isinstance(entry,dict) else None) or child.get('issue_field_id') or child.get('field_id')
        matches=[value for value in values if isinstance(value,dict) and str(value.get('issue_field_id'))==str(identity)] if identity is not None else []
        if matches:
            child['_resolved_fields']=[(value['issue_field_name'],display(value.get('single_select_option') or value.get('multi_select_options') or value.get('value'))) for value in matches if value.get('issue_field_name')]
        elif values:
            child['_resolved_fields']=[(str(value['issue_field_name'])+' (current)',display(value.get('single_select_option') or value.get('multi_select_options') or value.get('value'))) for value in values if isinstance(value,dict) and value.get('issue_field_name')]
