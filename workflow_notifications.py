import datetime as dt

from discord_threads import clip, marker, nonce


def timestamp(value):
    try:
        return int(dt.datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def update_run(feed, repo, payload):
    run = payload['workflow_run']
    channel = str(feed.config.get('ci_channel') or '')
    if not channel or channel == str(feed.channel):
        return
    conclusion = run.get('conclusion')
    phase = 3 if conclusion or payload.get('action') == 'completed' else 2 if payload.get('action') == 'in_progress' or run.get('status') == 'in_progress' else 1
    attempt = int(run.get('run_attempt') or 1)
    key = f'{repo}:{run["id"]}:{attempt}:{channel}'
    receipt = 'threadle-ci:' + nonce(key)
    with feed.queue.connect() as db:
        db.execute('CREATE TABLE IF NOT EXISTS ci_runs (identity TEXT PRIMARY KEY, message TEXT, phase INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT OR IGNORE INTO ci_runs(identity) VALUES(?)', (key,))
        message, saved_phase = db.execute('SELECT message,phase FROM ci_runs WHERE identity=?', (key,)).fetchone()
    if saved_phase >= phase:
        return
    english = feed.config.get('language') == 'en'
    if phase < 3:
        label = ('⏳ Queued' if english else '⏳ 실행 대기 중') if phase == 1 else ('🔄 Running' if english else '🔄 실행 중')
        color = 0xDBAB09
    elif conclusion == 'success':
        label, color = ('✅ Succeeded' if english else '✅ 성공'), 0x2DA44E
    elif conclusion in ('failure', 'timed_out', 'startup_failure', 'action_required'):
        label, color = ('❌ Failed' if english else '❌ 실패'), 0xD1242F
    else:
        label, color = '⏹ ' + clip(conclusion or 'completed', 40), 0x8B949E
    started = timestamp(run.get('run_started_at'))
    ended = timestamp(run.get('updated_at')) if phase == 3 else None
    description = ('Repository: ' if english else '저장소: ') + repo
    description += ('\nBranch: ' if english else '\n브랜치: ') + '`' + clip(run.get('head_branch') or 'unknown', 100) + '`'
    description += ('\nTrigger: ' if english else '\n실행 유형: ') + '`' + clip(run.get('event', 'unknown'), 60) + '`'
    description += ('\nAttempt: ' if english else '\n실행 회차: ') + str(attempt)
    if started:
        if ended:
            seconds = max(0, ended - started)
            description += ('\nDuration: ' if english else '\n소요 시간: ') + f'{seconds // 60}m {seconds % 60}s'
        elif phase < 3:
            description += ('\nStarted: ' if english else '\n시작: ') + f'<t:{started}:R>'
    embed = {'title': clip(run.get('name') or 'CI/CD', 180) + ' · ' + label,
             'description': description, 'url': run['html_url'], 'color': color,
             'footer': {'text': receipt}}
    data = {'content': 'CI/CD · ' + label, 'embeds': [embed], 'allowed_mentions': {'parse': []}}
    target = type(feed)(feed.api, dict(feed.config, alert_channel=channel), feed.queue)
    target.check()
    if not message:
        for existing in target.history():
            if receipt in marker(existing):
                message = existing['id']
                break
    if message:
        feed.api.call('PATCH', f'/channels/{channel}/messages/{message}', data)
    else:
        result = feed.api.call('POST', f'/channels/{channel}/messages', dict(data, nonce=nonce(receipt), enforce_nonce=True))
        message = result['id']
    with feed.queue.connect() as db:
        db.execute('UPDATE ci_runs SET message=?,phase=? WHERE identity=?', (message, phase, key))
