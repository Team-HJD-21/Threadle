import copy
import time
import uuid
from urllib.parse import urlencode

from connect_auth import ConnectError
from pr_routing import directory_tree, group_channel_name, validate_groups, classify
from team_notifications import validate_options


class TeamWeb:
    def __init__(self, web):
        self.web = web

    def scope(self, session, guild, query):
        web = self.web
        identity = query.get('binding')
        if identity:
            binding = web.service.registry.get(identity)
            if binding is None or binding.auth != 'app' or str(binding.config['guild_id']) != str(guild['id']):
                raise ConnectError('이 연결을 설정할 권한이 없습니다.', 403)
            session.data['repository'] = f'{binding.installation}:{binding.repo_id}'
            session.data['team_binding'] = identity
        installation, repo_id = web.selection(session)
        key = str(guild['id']) + '|' + session.data['repository']
        draft = session.data.get('team_draft')
        if not draft or draft['key'] != key or identity and draft.get('binding') != identity:
            existing = next((b for b in web.service.registry.active()
                             if b.repo_id == repo_id and str(b.config['guild_id']) == str(guild['id'])), None)
            config = existing.config if existing else {}
            from notification_language import selected_language
            from web_locale import LANGUAGE
            draft = {'language':selected_language(session,key,existing,LANGUAGE.get()), 'key': key, 'groups': copy.deepcopy(config.get('routing_groups', [])),
                     'split': bool(config.get('routing_groups')), 'selected': [],
                     'options': copy.deepcopy(config.get('team_notifications', {})) if existing else {'digest_weekday':4,'weekly_digest_hour':9},
                     'binding': existing.id if existing else None, 'original': copy.deepcopy(config)}
            session.data['team_draft'] = draft
        return installation, repo_id, draft

    def repository(self, session, installation, repo_id):
        self.web.require(session, 'github')
        repository = next((r for _, r in self.web.repositories(session, installation) if int(r['id']) == repo_id), None)
        if repository is None:
            raise ConnectError('이 저장소를 연결할 권한이 없습니다.', 403)
        return repository

    def get(self, request, session, guild, query):
        from connect_web import esc, form, page, select as select_control
        from web_locale import text, LANGUAGE
        def select(name, label, choices, selected=None):
            return select_control(name, label, [(key, text(value)) for key, value in choices], selected)
        web = self.web
        installation, repo_id, draft = self.scope(session, guild, query)
        web.sessions.save(session)
        if not session.data.get('github_token') or session.data.get('github_expires', 0) <= time.time():
            session.data['return_to_team'] = True
            web.sessions.save(session)
            return web.send(request, 200, page('팀 알림 설정', '<div class="card"><p>저장소 관리 권한을 확인하려면 GitHub 계정을 연결해 주세요.</p>'
                            + form('/connect/github/start', session, '<button>GitHub 계정 연결하기</button>') + '</div>'))
        repo = self.repository(session, installation, repo_id)
        groups, options = draft['groups'], draft['options']
        area_error = draft.pop('area_error', None)
        web.sessions.save(session)
        body = '<p><strong>' + esc(repo['full_name']) + '</strong></p>'
        body += '<div class="card" id="team-mode"><h2>PR 스레드 분류</h2>'
        mode = ('<label><input class="choice" type="radio" name="mode" value="off"' + (' checked' if not draft['split'] else '')
                + '>나누지 않기</label><label><input class="choice" type="radio" name="mode" value="on"'
                + (' checked' if draft['split'] else '') + '>디렉터리별로 나누기</label><button>분류 방식 선택</button>')
        body += form('/connect/team/mode', session, mode) + '</div>'
        if draft['split']:
            body += '<div class="card" id="work-areas"><h2>작업 영역</h2>'
            body += '<p>단일 영역 PR은 해당 채널에, 여러 영역 또는 미분류 파일이 포함된 PR은 공통 채널에 스레드를 만듭니다. 생성된 스레드는 이동하지 않습니다.</p>'
            for group in groups:
                body += ('<div class="area"><strong>' + esc(group['name']) + '</strong><p class="muted">#'
                         + esc(group_channel_name(group)) + ' · ' + esc(', '.join(group['paths'])) + '</p>'
                         + form('/connect/team/remove', session, '<input type="hidden" name="group" value="' + esc(group['id'])
                                + '"><button class="secondary">영역 제거</button>') + '</div>')
            folder = query.get('folder', '')
            directory_fields = ''
            try:
                children = directory_tree(web.users, session.data['github_token'], repo['full_name'], draft, folder)
                web.sessions.save(session)
                directory_fields += '<div class="picker-heading"><h3>디렉터리 선택</h3></div><div class="directory-browser"><nav class="directory-breadcrumbs" aria-label="현재 디렉터리">'
                directory_fields += '<button type="submit" formaction="/connect/team/browse" name="folder" value="" formnovalidate>/</button>'
                if folder:
                    current = ''
                    for part in folder.rstrip('/').split('/'):
                        current += part + '/'
                        directory_fields += '<span aria-hidden="true">/</span><button type="submit" formaction="/connect/team/browse" name="folder" value="' + esc(current) + '" formnovalidate>' + esc(part) + '</button>'
                directory_fields += '</nav><div class="directory-list">'
                entries = ([folder] if folder else ['@root']) + children
                fields = ''
                for index, path in enumerate(entries[:60]):
                    label = text('현재 폴더 전체') if path == folder else text('루트의 파일만') if path == '@root' else path.rstrip('/').rsplit('/', 1)[-1]
                    icon = ('<svg class="directory-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 7V5h6l2 2h10v13H3Z"/></svg>'
                            if path != '@root' else '<svg class="directory-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M6 3h8l4 4v14H6Z"/></svg>')
                    fields += '<div class="directory-row"><label><input class="choice" type="checkbox" name="path' + str(index) + '" value="' + esc(path) + '"' + (' checked' if path in draft['selected'] else '') + '>' + icon + '<span class="directory-name" translate="no">' + esc(label) + '</span></label>'
                    if path not in ('@root', folder):
                        fields += '<button class="directory-open" type="submit" formaction="/connect/team/browse" name="folder" value="' + esc(path) + '" formnovalidate aria-label="' + esc(text('하위 디렉터리 보기') + ' · ' + path) + '"><span aria-hidden="true">›</span></button>'
                    fields += '</div>'
                directory_fields += fields + '</div></div><p class="picker-note">폴더를 체크하면 하위 폴더도 포함됩니다. 화살표를 눌러 더 안쪽으로 이동할 수 있습니다.</p>'
                if len(entries) > 60:
                    directory_fields += '<p>항목이 많아 앞의 60개를 표시합니다. 아래 경로 입력으로 목록에 있는 디렉터리를 선택할 수 있습니다.</p>'
                    directory_fields += '<label for="path">디렉터리 경로</label><input id="path" name="path" maxlength="1000">'
            except (ConnectError, ValueError) as error:
                if isinstance(error, ConnectError) and error.status not in (403, 404):
                    raise
                body += '<p class="error">디렉터리를 읽을 수 없습니다. GitHub App의 Contents 읽기 권한과 저장소 접근 승인을 확인해 주세요.</p>'
                body += '<p class="muted">이미 설치한 계정이나 조직은 추가 권한 업데이트를 승인해야 합니다. 승인 후 GitHub 로그인을 새로 해 주세요.</p>'
                body += '<a class="button secondary" target="_blank" rel="noopener noreferrer" href="https://github.com/apps/' + esc(web.settings.github_slug) + '/installations/new">GitHub 앱 권한 확인 ↗</a>'
            selected = '<div class="selection-summary"><span>선택한 디렉터리</span><span class="selection-count">' + str(len(draft['selected'])) + '</span></div><div class="selection-chips">'
            for path in draft['selected']:
                selected += '<div class="selection-chip"><span translate="no">' + esc(text('루트의 파일만') if path == '@root' else path) + '</span><button type="submit" formaction="/connect/team/unselect" name="remove_path" value="' + esc(path) + '" formnovalidate aria-label="' + esc(text('선택 해제') + ' · ' + path) + '">×</button></div>'
            selected += '</div>'
            if not draft['selected']:
                selected = ''
            group_fields = '<input type="hidden" name="current_folder" value="' + esc(folder) + '"><label for="name">영역 이름</label><input id="name" name="name" maxlength="32" value="' + esc(draft.get('pending_name', '')) + '" required>'
            group_fields += directory_fields + selected
            if area_error:
                group_fields += '<div class="team-feedback error" role="alert">' + esc(area_error) + '</div>'
            group_fields += '<div class="area-actions"><button>영역 추가</button></div>'
            body += '<div class="area-editor" id="area-editor">' + form('/connect/team/group', session, group_fields) + '</div>'
            body += '<div class="picker-clear">' + form('/connect/team/clear', session, '<button class="secondary">디렉터리 선택 비우기</button>') + '</div></div>'
            body += '<div class="card" id="pr-preview"><h2>PR로 분류 미리보기</h2><p class="preview-intro">PR 번호로 보낼 채널과 분류 이유를 확인하세요.</p>'
            preview_fields = '<div class="preview-input"><label for="number">PR 번호</label><input id="number" name="number" inputmode="numeric" pattern="[0-9]+" placeholder="예: 123" value="' + esc(str(draft.get('preview', {}).get('number', ''))) + '" required></div><button>분류 확인</button>'
            body += form('/connect/team/preview', session, preview_fields).replace('<form ', '<form class="preview-form" ', 1)
            if draft.get('preview'):
                body += self.preview_result(draft['preview'])
            body += '</div>'
        body += '<div class="card" id="team-options"><h2>추가 알림</h2><p>선택한 기능만 켭니다. 상세 스레드의 활동은 바로 기록됩니다.</p>'
        body += '<p class="muted">사이트 언어와 별개로 이 저장소의 알림 언어를 설정합니다. 제목과 댓글 원문은 번역하지 않습니다.</p>'
        fields = select('notification_language','알림 언어',[('ko','한국어 (KR)'),('en','English (EN)')],draft.get('language','ko'))
        fields += select('review_hours', '열린 PR 반복 알림', [('0', '사용 안 함'), ('4', '4시간마다'), ('12', '12시간마다'), ('24', '24시간마다'), ('48', '48시간마다')], str(options.get('review_hours', 0)))
        fields += '<p class="muted">재알림을 켜거나 간격을 바꾼 시각부터, 초안을 포함한 열린 PR 전체를 같은 주기로 모아 알립니다. 방해금지 시간에는 보내지 않습니다.</p>'
        fields += select('batch_seconds', '일반 알림 묶기', [('0', '즉시 전달'), ('60', '1분마다'), ('300', '5분마다')], str(options.get('batch_seconds', 0)))
        fields += '<p class="muted">종료·병합·CI 실패·변경 요청은 묶지 않습니다. 방해금지 시간에는 새 항목의 시작 메시지만 무음으로 보내고, 스레드와 상세 알림은 종료 후 순차적으로 전달합니다. 기존 스레드는 무음으로 기록하며 CI/CD 상태와 실패 경고는 즉시 전달합니다.</p>'
        from digest_settings import enabled as digest_enabled
        fields += '<h3>일일 요약</h3>'
        fields += select('daily_digest','일일 요약 사용',[('0','사용 안 함'),('1','사용')], '1' if digest_enabled(options,'daily') else '0')
        fields += select('digest_hour', '일일 요약 시각', [(str(n), f'{n:02d}:00') for n in range(24)], str(options.get('digest_hour', 9)))
        fields += '<h3>주간 요약</h3>'
        fields += select('weekly_digest','주간 요약 사용',[('0','사용 안 함'),('1','사용')], '1' if digest_enabled(options,'weekly') else '0')
        fields += select('digest_weekday', '주간 요약 요일', [(str(n), label) for n, label in enumerate(['월요일', '화요일', '수요일', '목요일', '금요일', '토요일', '일요일'])], str(options.get('digest_weekday', 0)))
        fields += select('weekly_digest_hour', '주간 요약 시각', [(str(n), f'{n:02d}:00') for n in range(24)], str(options.get('weekly_digest_hour', options.get('digest_hour', 9))))
        fields += '<p class="muted">요약은 발송 시점의 열린 PR · Issue 현황입니다.</p>'
        fields += '<h3>시간대와 방해금지</h3>'
        fields += select('utc_offset', '시간대 (UTC 기준)', [(str(n), f'UTC{n:+d}') for n in range(-12, 15)], str(options.get('utc_offset', 9)))
        fields += '<p class="muted">고정 UTC 시차입니다. 서머타임은 자동 변경하지 않습니다.</p>'
        fields += '<label><input class="choice" type="checkbox" name="quiet" value="1"' + (' checked' if options.get('quiet') else '') + '>방해금지 시간 사용</label>'
        for key, title, default in [('quiet_start', '방해금지 시간 시작', 22), ('quiet_end', '방해금지 시간 종료', 8)]:
            fields += select(key, title, [(str(n), f'{n:02d}:00') for n in range(24)], str(options.get(key, default)))
        fields += '<h3>설정 적용</h3><p>선택한 옵션은 아래 버튼을 눌러야 실제 연결에 반영됩니다. 기존 PR 스레드와 대기 중인 알림은 유지됩니다.</p>'
        if query.get('saved') == '1':
            fields += '<div class="team-feedback" role="status">설정이 적용됐어요.</div>'
        import legal_consent
        body += form('/connect/team/options', session, fields + legal_consent.fields(session) + '<button name="apply" value="1">채널 생성 및 설정 적용</button>') + '</div><a href="/connect/channels">기본 연결 설정으로 돌아가기</a>'
        body = '<div id="team-settings" data-folder="' + esc(query.get('folder', '')) + '">' + body + '</div><script src="/connect/assets/team-settings.js?v=1" defer></script>'
        return web.send(request, 200, page('팀 알림 설정', body))

    def post(self, request, path, session, guild, fields):
        web = self.web
        installation, repo_id, draft = self.scope(session, guild, {})
        repo = self.repository(session, installation, repo_id)
        if path == '/connect/team/start':
            from notification_language import normalize_language
            language = normalize_language(fields.get('notification_language',draft.get('language','ko')))
            draft['language'] = language
            session.data['notification_language'] = {'key':draft['key'],'language':language}
        elif path == '/connect/team/mode':
            if fields.get('mode') not in ('off', 'on'):
                raise ConnectError('분류 방식을 선택해 주세요.')
            draft['split'] = fields['mode'] == 'on'
        elif path == '/connect/team/select':
            draft['selected'] = self.selected_paths(draft, fields)
            if 'name' in fields:
                draft['pending_name'] = fields['name'][:32]
        elif path in ('/connect/team/browse', '/connect/team/unselect'):
            folder = fields.get('folder', '') if path.endswith('/browse') else fields.get('current_folder', '')
            if folder not in draft.get('directory_trees', {}):
                raise ConnectError('목록에서 디렉터리를 선택해 주세요.')
            draft['selected'] = self.selected_paths(draft, fields)
            if path.endswith('/unselect'):
                draft['selected'] = [p for p in draft['selected'] if p != fields.get('remove_path')]
            draft['pending_name'] = fields.get('name', draft.get('pending_name', ''))[:32]
            draft.pop('preview', None)
            web.sessions.save(session)
            target = '/connect/team' + ('?' + urlencode({'folder': folder}) if folder else '')
            return web.redirect(request, target + '#area-editor')
        elif path == '/connect/team/clear':
            draft['selected'] = []
        elif path == '/connect/team/group':
            draft['pending_name'] = fields.get('name', '').strip()[:32]
            paths = self.selected_paths(draft, fields)
            group = {'id': uuid.uuid4().hex[:16], 'name': fields.get('name', '').strip(), 'paths': paths}
            error = None
            if not paths:
                error = '영역 이름만으로는 분류할 수 없습니다. 이 영역에 넣을 실제 폴더를 하나 이상 체크해 주세요.'
            else:
                try:
                    validate_groups(draft['groups'] + [group], provisioned=False)
                except ValueError as invalid:
                    error = str(invalid)
                if group_channel_name(group) in [group_channel_name(g) for g in draft['groups']]:
                    error = '채널 이름이 중복됩니다. 다른 영역 이름을 입력해 주세요.'
            if error:
                draft['selected'], draft['area_error'] = paths, error
                web.sessions.save(session)
                return web.redirect(request, '/connect/team#work-areas')
            draft['groups'].append(group)
            draft['selected'] = []
            draft.pop('pending_name', None)
        elif path == '/connect/team/remove':
            draft['groups'] = [g for g in draft['groups'] if g['id'] != fields.get('group')]
        elif path == '/connect/team/options':
            from notification_language import normalize_language
            draft['language'] = normalize_language(fields.get('notification_language',draft.get('language','ko')))
            options = {key: int(fields.get(key, draft['options'].get(key,default))) for key, default in [('review_hours', 0), ('batch_seconds', 0),
                       ('utc_offset', 9), ('digest_hour', 9), ('digest_weekday', draft['options'].get('digest_weekday',0)), ('weekly_digest_hour', draft['options'].get('weekly_digest_hour',draft['options'].get('digest_hour',9))), ('quiet_start', 22), ('quiet_end', 8)]}
            options.update({'quiet': fields.get('quiet') == '1'})
            for key in ('daily_digest','weekly_digest'):
                if key in fields:
                    if fields[key] not in ('0','1'):raise ConnectError('요약 사용 여부를 확인해 주세요.')
                    options[key]=fields[key]=='1'
            if 'digest' in fields:options['digest']=fields['digest']
            from digest_settings import apply
            options=apply(draft['options'],options,time.time())
            validate_options(options)
            options['review_since'] = (time.time() if options['review_hours']!=draft['options'].get('review_hours',0)
                                       else draft['options'].get('review_since',0))
            draft['options'] = options
            if fields.get('apply') == '1':
                return self.post(request, '/connect/team/apply', session, guild, fields)
        elif path == '/connect/team/preview':
            from registry import Binding
            number = fields.get('number', '')
            if not number.isdigit() or not 0 < int(number) < 2**31:
                raise ConnectError('PR 번호를 확인해 주세요.')
            pending = Binding('preview', 'preview', repo_id, repo['full_name'], installation, 'app', {}, False)
            files = web.service.file_index.files(pending, int(number), web.service.github(pending))
            matches, unmatched = classify(files or [], draft['groups'])
            names = [g['name'] for g in draft['groups'] if g['id'] in matches]
            destination = names[0] if len(names) == 1 and not unmatched and files is not None else '공통 PR 채널'
            matched_paths = {p for paths in matches.values() for p in paths}
            unmatched_count = sum(p not in matched_paths for p in files or [])
            reason = ('영역 미설정' if not draft['groups'] else '전체 경로 확인 제한' if files is None else
                      '변경 경로 없음' if not files else '일치 영역 없음' if not matches else
                      '미분류 경로 포함' if unmatched else '여러 작업 영역 일치' if len(matches) > 1 else '단일 작업 영역 일치')
            draft['preview'] = {'number': int(number), 'destination': destination, 'reason': reason,
                                'total': len(files) if files is not None else None, 'unmatched': unmatched_count,
                                'groups': [{'name': g['name'], 'count': len(matches[g['id']])} for g in draft['groups'] if g['id'] in matches],
                                'paths': (files or [])[:100]}
        elif path == '/connect/team/apply':
            if draft['split'] and not draft['groups']:
                raise ConnectError('작업 영역을 하나 이상 추가해 주세요.')
            web.sessions.save(session)
            return web.post(request, '/connect/save', session, {'team_apply': '1', 'csrf': fields['csrf'], **{k: v for k, v in fields.items() if k.startswith('legal_')}})
        else:
            raise ConnectError('요청을 찾을 수 없습니다.', 404)
        draft.pop('preview', None) if path != '/connect/team/preview' else None
        web.sessions.save(session)
        return web.redirect(request, '/connect/team')

    @staticmethod
    def selected_paths(draft, fields):
        values = [v for k, v in fields.items() if v and (k == 'path' or k.startswith('path') and k[4:].isdigit())]
        known = set(draft.get('directory_trees', {})) - {''}
        if any(p not in known | {'@root'} for p in values):
            raise ConnectError('목록에서 디렉터리를 선택해 주세요.')
        chosen = sorted(set(draft['selected'] + values))
        if len(chosen) > 30:
            raise ConnectError('각 작업 영역에 디렉터리를 1~30개 선택해 주세요.')
        return chosen

    @staticmethod
    def preview_result(preview):
        from connect_web import esc
        from web_locale import text
        destination = preview['destination']
        label = text(destination) if destination == '공통 PR 채널' else destination
        reasons = {
            '영역 미설정': '작업 영역을 추가하면 디렉터리 기준으로 분류할 수 있습니다.',
            '전체 경로 확인 제한': '변경 파일이 많아 전체 경로를 확인할 수 없습니다. 공통 채널을 사용합니다.',
            '변경 경로 없음': '분류할 변경 경로가 없어 공통 채널을 사용합니다.',
            '일치 영역 없음': '설정한 작업 영역에 해당하는 경로가 없어 공통 채널을 사용합니다.',
            '미분류 경로 포함': '어느 작업 영역에도 속하지 않은 경로가 포함되어 공통 채널을 사용합니다.',
            '여러 작업 영역 일치': '두 개 이상의 작업 영역을 함께 수정한 PR이므로 공통 채널을 사용합니다.',
            '단일 작업 영역 일치': '모든 변경 경로가 하나의 작업 영역에 속합니다.',
        }
        result = '<div class="preview-result"><p class="preview-kicker">예상 스레드 위치</p><div class="preview-destination" translate="no">' + esc(label) + '</div>'
        result += '<p class="preview-reason">' + esc(reasons.get(preview.get('reason'), '분류 결과를 다시 확인해 주세요.')) + '</p><div class="preview-stats">'
        if preview.get('total') is not None:
            result += '<span class="preview-stat"><span>검사한 경로</span> <b>' + str(preview['total']) + '</b></span>'
        for group in preview.get('groups', []):
            result += '<span class="preview-stat"><span translate="no">' + esc(group['name']) + '</span> <b>' + str(group['count']) + '</b></span>'
        if preview.get('unmatched'):
            result += '<span class="preview-stat"><span>미분류</span> <b>' + str(preview['unmatched']) + '</b></span>'
        result += '</div></div>'
        if preview.get('paths'):
            result += '<details class="preview-files"><summary><span>분류에 사용한 경로 보기</span></summary><ul class="path-list">'
            for path in preview['paths']:
                parent, _, filename = path.rpartition('/')
                result += '<li translate="no"><span class="path-file">' + esc(filename) + '</span><code class="path-parent">' + esc(parent + '/' if parent else '/') + '</code></li>'
            result += '</ul>'
            if preview.get('total', 0) > len(preview['paths']):
                result += '<p class="preview-limit">경로가 많아 앞의 100개만 표시합니다.</p>'
            result += '</details>'
        return result
