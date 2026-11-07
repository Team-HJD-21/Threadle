import re

from discord_threads import clip
from topic_state import GRAY


NOTICE_PREFIX = 'reviews-dismissed:'
DECISIONS = {'approved', 'changes_requested'}


def dismissal_reason(review, topic, english):
    message = ' '.join((review.get('dismissal_message') or '').split())
    if message:
        return clip(message, 480)
    commit = str(review.get('dismissal_commit_id') or '')
    if re.fullmatch(r'[a-fA-F0-9]{40}', commit):
        label = 'Dismissal commit' if english else '무효화에 연결된 커밋'
        return f'{label} [`{commit[:7]}`](https://github.com/{topic.repo}/commit/{commit})'
    return 'GitHub provided no reason.' if english else 'GitHub에서 사유를 제공하지 않았어요.'


def summary(github, topic, event, payload, language):
    entries = payload['_events'] if event == 'threadle_bundle' else [{'event': event, 'payload': payload}]
    trigger_ids = {
        str((entry['payload'].get('review') or {}).get('id'))
        for entry in entries
        if entry['event'] == 'pull_request_review' and entry['payload'].get('action') == 'dismissed'
    }
    if not trigger_ids:
        return None

    repo = '/repos/' + topic.repo
    active, dismissed = set(), set()
    for review in github.pages(f'{repo}/pulls/{topic.number}/reviews'):
        state = review['state'].lower()
        if state in DECISIONS:
            active.add(str(review['id']))
        if state == 'dismissed':
            dismissed.add(str(review['id']))
    if trigger_ids & active:
        raise RuntimeError('Review dismissal is not yet visible in GitHub reviews; retrying')
    if active or not trigger_ids & dismissed:
        return None

    decisions = {}
    for entry in github.pages(f'{repo}/issues/{topic.number}/timeline'):
        if entry.get('event') != 'review_dismissed':
            continue
        review = entry.get('dismissed_review') or {}
        review_id = str(review.get('review_id'))
        if review_id in dismissed:
            decisions[review_id] = review
    # A bulk dismissal can be visible in the review list before every event
    # reaches the timeline. Wait for all rows so retries use the same summary ID.
    if dismissed - decisions.keys():
        raise RuntimeError('Review dismissals are not yet complete in the GitHub timeline; retrying')
    relevant = {review_id: review for review_id, review in decisions.items()
                if str(review.get('state')).lower() in DECISIONS}
    if not trigger_ids.intersection(relevant):
        return None

    english = language == 'en'
    title = f'PR #{topic.number} · ' + ('All reviews dismissed' if english else '모든 승인·변경 요청 무효화')
    reasons = dict.fromkeys(dismissal_reason(review, topic, english) for review in reversed(list(relevant.values())))
    lines = ['• ' + reason for reason in list(reasons)[:5]]
    if len(reasons) > 5:
        lines.append('See GitHub for more reasons.' if english else '나머지 사유는 GitHub에서 확인할 수 있어요.')
    description = ('**Dismissal reasons**' if english else '**무효화 사유**') + '\n' + '\n'.join(lines)
    # Review IDs are unique, and a fresh approval/change request gets a new ID.
    return NOTICE_PREFIX + max(relevant, key=int), title, description


def notify(threads, topic, github, event, payload, language, closed, feed, notices):
    result = summary(github, topic, event, payload, language)
    if result is None:
        return
    key, title, description = result
    thread = threads.deliver(topic, {'title': title, 'description': description, 'url': topic.url, 'color': GRAY}, key, closed)
    link = f'https://discord.com/channels/{threads.guild}/{thread}'
    description += f'\n\n[{"Open thread" if language == "en" else "스레드 확인하기"}]({link})'
    if notices and feed:
        notices.schedule(topic.key, title, description, link, key, [feed.channel], critical=True, color=GRAY)
    elif feed:
        feed.send(topic.key, key, '', title, description, link, GRAY)
