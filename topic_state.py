"""Current topic-card state is independent from the color of a historical event."""
GREEN=0x2DA44E
GRAY=0x8B949E
BLUE=0x0969DA
PURPLE=0x8957E5
RED=0xD1242F
ORANGE=0xD97706


def state_label(kind,obj):
    if kind=='pr' and (obj.get('merged') or obj.get('merged_at')):return 'Merged'
    if kind=='discussion':
        answered=bool(obj['isAnswered']) if 'isAnswered' in obj else bool(obj['answer_chosen_at']) if 'answer_chosen_at' in obj else bool(obj.get('answer'))
        if answered:return 'Answered'
    closed=bool(obj['closed']) if 'closed' in obj else obj.get('state')=='closed'
    if closed:return 'Closed'
    if kind=='pr' and obj.get('draft'):return 'Draft'
    return 'Open'


def card_color(kind,obj):
    state=state_label(kind,obj)
    if state=='Closed':return PURPLE if kind in ('issue','discussion') else RED
    return {'Open':GREEN,'Draft':GRAY,'Merged':PURPLE,'Answered':BLUE}[state]


def status_field(kind,obj):
    return {'name':'Current Status','value':'`'+state_label(kind,obj)+'`','inline':False}


def event_color(kind,event,payload):
    action=payload.get('action')
    if event in ('issue_comment','discussion_comment','pull_request_review_comment'):return GRAY
    if event=='pull_request_review':
        if action=='dismissed':return GRAY
        return {'approved':GREEN,'changes_requested':ORANGE}.get(str(payload.get('review',{}).get('state','')).lower(),GRAY)
    if kind=='discussion':return {'answered':BLUE,'unanswered':RED,'closed':PURPLE,'reopened':GREEN}.get(action,BLUE)
    if kind=='issue':return RED if action in ('closed','deleted') else GREEN
    if payload.get('pull_request',{}).get('merged') and action=='closed' or action=='merged':return PURPLE
    if action=='closed':return RED
    if action in ('opened','reopened','ready_for_review'):return GREEN
    if action=='converted_to_draft':return GRAY
    return BLUE
