"""Allowlisted effective /settings values; never render raw connection config."""
from html import escape
from digest_settings import enabled
from web_locale import LANGUAGE


def details(config):
    en=LANGUAGE.get()=='en';t=lambda ko,en_:en_ if en else ko
    options=config.get('team_notifications',{})
    hour=options.get('digest_hour',9)
    weekdays=['월요일','화요일','수요일','목요일','금요일','토요일','일요일'] if not en else ['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday']
    switch=lambda value:t('사용','On') if value else t('사용 안 함','Off')
    groups=[(t('일반','General'),[(t('알림 언어','Notification language'),'English (EN)' if config.get('language','ko')=='en' else '한국어 (KR)'),(t('시간대','Time zone'),f"UTC{options.get('utc_offset',9):+d}")]),
            (t('PR 재알림·묶음','PR reminders & batching'),[(t('열린 PR 재알림','Open PR reminder'),str(options.get('review_hours',0))+t('시간',' hours') if options.get('review_hours',0) else switch(False)),(t('일반 알림 묶기','Notification batching'),str(options.get('batch_seconds',0))+t('초',' seconds') if options.get('batch_seconds',0) else t('즉시','Instant'))]),
            (t('요약','Digests'),[(t('일일 요약','Daily digest'),switch(enabled(options,'daily'))+f' · {hour:02}:00'),(t('주간 요약','Weekly digest'),switch(enabled(options,'weekly'))+' · '+weekdays[options.get('digest_weekday',0)]+f" · {options.get('weekly_digest_hour',hour):02}:00")]),
            (t('방해금지 시간','Do Not Disturb'),[(t('사용 여부','Enabled'),switch(options.get('quiet',False))),(t('시간','Hours'),f"{options.get('quiet_start',22):02}:00–{options.get('quiet_end',8):02}:00")])]
    body='<details class="admin-current-settings"><summary>'+t('현재 /settings 설정','Current /settings values')+'</summary>'
    for heading,rows in groups:
        body+='<section class="admin-setting-group"><h3>'+escape(heading)+'</h3><dl>'
        for name,value in rows:body+='<div class="admin-setting-row"><dt>'+escape(name)+'</dt><dd>'+escape(str(value))+'</dd></div>'
        body+='</dl></section>'
    return body+'</details>'
