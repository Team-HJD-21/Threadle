from discord_threads import clip, marker, nonce


def alert_channel_config(api, config):
    parent = api.call('GET', '/channels/' + config['channels']['issue']).get('parent_id')
    channels = api.call('GET', '/guilds/' + config['guild_id'] + '/channels')
    candidates = [item for item in channels if item.get('type') == 0 and item.get('name', '').endswith('github-alert')]
    matches = [item for item in candidates if item.get('parent_id') == parent]
    if not matches and len(candidates) == 1:
        matches = candidates
    if len(matches) != 1:
        raise ValueError('Could not uniquely identify the existing github-alert text channel; no channel was created.')
    updated = dict(config, alert_channel=matches[0]['id'])
    ci = [item for item in channels if item.get('type') == 0
          and item.get('parent_id') == parent and item.get('name', '').endswith('github-ci')]
    if len(ci) == 1:
        updated['ci_channel'] = ci[0]['id']
    return updated


def activity(event, payload):
    action = payload.get('action', 'updated')
    if event == 'threadle_bundle':
        return '새 항목 등록' if payload.get('_created') else f"변경 {len(payload.get('_events', []))}건"
    if event in ('issue_comment', 'discussion_comment', 'pull_request_review_comment'):
        what = '코드 댓글' if event == 'pull_request_review_comment' else '댓글'
        return what + {'created': ' 등록', 'edited': ' 수정', 'deleted': ' 삭제'}.get(action, ' 변경')
    if event == 'pull_request_review':
        if action == 'dismissed':
            return '리뷰 취소'
        return {'approved': '리뷰 승인', 'changes_requested': '변경 요청',
                'commented': '리뷰 댓글'}.get(payload.get('review', {}).get('state', '').lower(), '리뷰 변경')
    if event == 'workflow_run':
        return 'CI 실패'
    if action == 'closed' and payload.get('pull_request', {}).get('merged'):
        return '병합'
    return {'opened': '새 항목 등록', 'created': '새 항목 등록', 'edited': '내용 수정',
            'deleted': '삭제', 'closed': '종료', 'reopened': '다시 열림', 'synchronize': '새 커밋 반영',
            'review_requested': '리뷰 요청', 'review_request_removed': '리뷰 요청 취소',
            'ready_for_review': '리뷰 준비 완료', 'converted_to_draft': '초안 전환',
            'assigned': '담당자 지정', 'unassigned': '담당자 해제', 'labeled': '라벨 추가',
            'unlabeled': '라벨 제거', 'answered': '답변 채택', 'unanswered': '답변 채택 해제',
            'category_changed': '카테고리 변경', 'locked': '잠금', 'unlocked': '잠금 해제',
            'milestoned': '마일스톤 지정', 'demilestoned': '마일스톤 해제',
            'pinned': '고정', 'unpinned': '고정 해제', 'typed': '유형 지정', 'untyped': '유형 해제', 'field_added': '필드 설정', 'field_removed': '필드 해제'}.get(action, '항목 변경 · ' + str(action))


def english_activity(event, payload):
    if event == 'threadle_bundle':
        return 'Opened' if payload.get('_created') else f"{len(payload.get('_events', []))} updates"
    if event in ('issue_comment', 'discussion_comment', 'pull_request_review_comment'):
        return {'created': 'New comment', 'edited': 'Comment edited', 'deleted': 'Comment deleted'}.get(payload.get('action'), 'Comment updated')
    if event == 'pull_request_review':
        if payload.get('action') == 'dismissed':
            return 'Review dismissed'
        return {'approved': 'Approved', 'changes_requested': 'Changes requested', 'commented': 'Review comment'}.get(payload.get('review', {}).get('state', '').lower(), 'Review updated')
    if event == 'workflow_run':
        return 'CI failed'
    if payload.get('action') == 'closed' and payload.get('pull_request', {}).get('merged'):
        return 'Merged'
    return {'opened': 'Opened', 'created': 'Created', 'edited': 'Updated', 'closed': 'Closed', 'reopened': 'Reopened',
            'synchronize': 'New commits', 'review_requested': 'Review requested', 'review_request_removed': 'Review request removed',
            'ready_for_review': 'Ready for review', 'converted_to_draft': 'Converted to draft', 'labeled': 'Label added',
            'unlabeled': 'Label removed', 'assigned': 'Assignee added', 'unassigned': 'Assignee removed',
            'deleted': 'Deleted', 'answered': 'Answer accepted', 'unanswered': 'Answer unmarked',
            'category_changed': 'Category changed', 'locked': 'Locked', 'unlocked': 'Unlocked',
            'milestoned': 'Milestone added', 'demilestoned': 'Milestone removed',
            'pinned': 'Pinned', 'unpinned': 'Unpinned', 'typed': 'Type set', 'untyped': 'Type removed', 'field_added': 'Field set', 'field_removed': 'Field cleared'}.get(payload.get('action'), 'Updated · ' + str(payload.get('action','unknown')))


class AlertFeed:
    def __init__(self, api, config, queue):
        self.api, self.config, self.queue = api, config, queue
        self.channel = config['alert_channel']
        with queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS alerts (receipt TEXT PRIMARY KEY, message TEXT)')

    def check(self):
        channel = self.api.call('GET', '/channels/' + self.channel)
        if channel.get('guild_id') != self.config['guild_id'] or channel.get('type') != 0:
            raise ValueError('The configured alert channel is not a text channel in the expected server.')
        self.api.call('GET', '/channels/' + self.channel + '/messages?limit=1')

    def history(self):
        before = None
        while True:
            path = '/channels/' + self.channel + '/messages?limit=100'
            if before:
                path += '&before=' + before
            rows = self.api.call('GET', path)
            yield from rows
            if len(rows) < 100:
                return
            cursor = rows[-1]['id']
            if cursor == before:
                raise RuntimeError('Alert history cursor did not advance')
            before = cursor

    def send(self, key, event_key, content, title, description, url, color, extra_embeds=None):
        if self.config.get('alert_scope'):
            key += ':channel:' + self.config['alert_scope']
        receipt = 'github-alert:' + nonce(key + ':' + event_key)
        with self.queue.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT message FROM alerts WHERE receipt=?', (receipt,)).fetchone()
            if row and row[0]:
                return
            if row is None:
                db.execute('INSERT INTO alerts(receipt) VALUES(?)', (receipt,))
        if row is not None:
            for message in self.history():
                if receipt in marker(message):
                    self.remember(receipt, message['id'])
                    return
        message = self.api.call('POST', '/channels/' + self.channel + '/messages', {
            'content': clip(content, 500), 'allowed_mentions': {'parse': []},
            'nonce': nonce(receipt), 'enforce_nonce': True,
            'embeds': [{'title': clip(title, 230), 'description': description, 'url': url,
                        'color': color, 'footer': {'text': receipt}}] + (extra_embeds or [])})
        self.remember(receipt, message['id'])

    def remember(self, receipt, message):
        with self.queue.connect() as db:
            db.execute('UPDATE alerts SET message=? WHERE receipt=?', (message, receipt))

    def topic(self, topic, event, payload, event_key, thread):
        label = {'issue': 'ISSUE', 'pr': 'PR', 'discussion': 'Discussion'}[topic.kind]
        actor = clip(payload.get('sender', {}).get('login', 'GitHub'), 80)
        english = self.config.get('language') == 'en'
        summary = f'{label} #{topic.number} · ' + (english_activity(event, payload) if english else activity(event, payload))
        link = f"https://discord.com/channels/{self.config['guild_id']}/{thread}"
        content = f'🧵 I brought you an update: {summary}.' if english else f'🧵 새 소식을 가져왔어요! {summary} — 스레드에서 확인해 주세요.'
        description = f'By {actor}\n[Open thread]({link}) · [View on GitHub]({topic.url})' if english else f'작성자: {actor}\n[스레드 확인하기]({link}) · [GitHub 원문]({topic.url})'
        self.send(topic.key, event_key, content,
                  topic.title or summary, description,
                  link, {'issue': 0x2DA44E, 'pr': 0x8957E5, 'discussion': 0x0969DA}[topic.kind])

    def ci(self, repo, payload, event_key):
        run = payload['workflow_run']
        if run.get('event') == 'pull_request' or run.get('conclusion') != 'failure':
            return
        link = run['html_url']
        channel = self.config.get('ci_channel')
        english = self.config.get('language') == 'en'
        details = ('[View run on GitHub](' if english else '[GitHub 실행 결과](') + link + ')'
        if channel:
            details = ('[Open CI channel](' if english else '[CI 채널 확인하기](') + f"https://discord.com/channels/{self.config['guild_id']}/{channel}) · " + details
        self.send(repo + ':ci:' + str(run['id']), event_key,
                  '🧵 I spotted a failed CI run. Take a look when you can.' if english else '🧵 CI 실패를 발견했어요. 실행 결과를 함께 확인해 주세요.',
                  clip(run.get('name', 'CI'), 100) + ' · ' + clip(run.get('head_branch', ''), 100), details, link, 0xD1242F)
