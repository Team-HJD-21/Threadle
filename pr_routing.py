import hashlib
import json
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from urllib.parse import quote


def validate_groups(groups, provisioned=True):
    if not isinstance(groups, list) or len(groups) > 8:
        raise ValueError('작업 영역은 최대 8개까지 만들 수 있습니다.')
    seen, names = set(), set()
    for group in groups:
        if not isinstance(group, dict) or not re.fullmatch('[a-f0-9]{16}', group.get('id', '')):
            raise ValueError('작업 영역 설정을 다시 확인해 주세요.')
        name = group.get('name', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 32 or any(ord(c) < 32 for c in name):
            raise ValueError('작업 영역 이름은 1~32자로 입력해 주세요.')
        if group['id'] in seen or name.casefold() in names:
            raise ValueError('작업 영역 이름이 중복됩니다.')
        seen.add(group['id'])
        names.add(name.casefold())
        paths = group.get('paths')
        if not isinstance(paths, list) or not 1 <= len(paths) <= 30:
            raise ValueError('각 작업 영역에 디렉터리를 1~30개 선택해 주세요.')
        for path in paths:
            if not isinstance(path, str) or (path != '@root' and (
                not path.endswith('/') or path.startswith('/') or len(path) > 1000
                or any(p in ('', '.', '..') for p in path[:-1].split('/'))
                or any(ord(c) < 32 for c in path) or '\\' in path)):
                raise ValueError('디렉터리 경로가 올바르지 않습니다.')
        if provisioned and not re.fullmatch('[1-9][0-9]*', str(group.get('channel', ''))):
            raise ValueError('작업 영역 채널을 확인해 주세요.')
    return groups


def group_channel_name(group):
    name = re.sub(r'[^\w\-]+', '-', group['name'].lower(), flags=re.UNICODE).strip('-_')
    return (name or 'area')[:70] + '-pr'


def classify(files, groups):
    matches, unmatched = {}, False
    for filename in files:
        found = False
        for group in groups:
            if any(('/' not in filename if path == '@root' else filename.startswith(path)) for path in group['paths']):
                matches.setdefault(group['id'], []).append(filename)
                found = True
        unmatched |= not found
    return matches, unmatched


class FileIndex:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = path, clock
        self.locks = [threading.Lock() for _ in range(64)]
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pr_files(cache_key TEXT PRIMARY KEY, files TEXT NOT NULL, created REAL NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def files(self, binding, number, github):
        prefix = f'/repos/{binding.repo}/pulls/{number}'
        before = github.call('GET', prefix)
        head, base = before['head']['sha'], before['base']['sha']
        count = before.get('changed_files')
        if not isinstance(count, int) or count < 0:
            raise RuntimeError('Missing changed-file count')
        if count > 3000:
            return None
        key = f'{binding.installation}:{binding.repo_id}:{number}:{base}:{head}'
        with self.locks[int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % len(self.locks)]:
            with self.connect() as db:
                saved = db.execute('SELECT files FROM pr_files WHERE cache_key=?', (key,)).fetchone()
            if saved:
                return json.loads(saved[0])
            files = []
            for page in range(1, 31):
                rows = github.call('GET', prefix + f'/files?per_page=100&page={page}')
                if not isinstance(rows, list):
                    raise RuntimeError('Invalid changed-file response')
                files.extend(rows)
                if len(rows) < 100:
                    break
            after = github.call('GET', prefix)
            if (after['head']['sha'], after['base']['sha'], after.get('changed_files')) != (head, base, count) or len(files) != count:
                raise RuntimeError('PR changed while reading files; retry required')
            paths = sorted({row['filename'] for row in files} | {row['previous_filename'] for row in files if row.get('previous_filename')})
            with self.connect() as db:
                db.execute('DELETE FROM pr_files WHERE created<?', (self.clock() - 7 * 86400,))
                db.execute('INSERT OR REPLACE INTO pr_files VALUES(?,?,?)', (key, json.dumps(paths), self.clock()))
            return paths


class PrRouter:
    def __init__(self, queue, index):
        self.queue, self.index = queue, index
        with queue.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS pr_routes(number INTEGER PRIMARY KEY, channel TEXT NOT NULL, groups_json TEXT NOT NULL, reason TEXT NOT NULL)')

    def resolve(self, binding, topic, event, payload, github):
        groups = validate_groups(binding.config.get('routing_groups', []))
        with self.queue.connect() as db:
            saved = db.execute('SELECT channel,groups_json,reason FROM pr_routes WHERE number=?', (topic.number,)).fetchone()
            link = db.execute('SELECT channel FROM links WHERE topic=?', (topic.key,)).fetchone()
        refresh = event == 'pull_request' and payload.get('action') in ('opened', 'reopened', 'synchronize', 'edited')
        if saved and not refresh:
            return saved[0], json.loads(saved[1]), saved[2]
        files = self.index.files(binding, topic.number, github) if groups else []
        matches, unmatched = classify(files or [], groups)
        selected = next((g['channel'] for g in groups if g['id'] in matches), binding.config['channels']['pr'])
        if len(matches) != 1 or unmatched or files is None:
            selected = binding.config['channels']['pr']
        reason = ('file-limit' if files is None else 'mixed-or-unmatched' if len(matches) != 1 or unmatched else 'directory-match')
        retained = {g['id']: g for g in json.loads(saved[1])} if saved else {}
        retained.update({g['id']: g for g in groups if g['id'] in matches})
        targets = list(retained.values())
        selected = link[0] if link else saved[0] if saved else selected
        with self.queue.connect() as db:
            db.execute('INSERT INTO pr_routes VALUES(?,?,?,?) ON CONFLICT(number) DO UPDATE SET groups_json=excluded.groups_json,reason=excluded.reason',
                       (topic.number, selected, json.dumps(targets), reason))
        return selected, targets, reason


def directory_tree(users, token, repository, session_data, folder=''):
    trees = session_data.setdefault('directory_trees', {})
    if not folder:
        ref = trees.get('')['sha'] if '' in trees else 'HEAD'
    elif folder in trees:
        ref = trees[folder]['sha']
    else:
        raise ValueError('목록에서 디렉터리를 선택해 주세요.')
    if folder in trees and 'children' in trees[folder]:
        return trees[folder]['children']
    result = users.github(token, f'/repos/{repository}/git/trees/' + quote(ref, safe=''))
    if result.get('truncated'):
        raise ValueError('디렉터리 목록이 너무 큽니다. 더 작은 저장소 범위로 연결해 주세요.')
    children = []
    for row in result['tree']:
        if row.get('type') != 'tree':
            continue
        if len(trees) >= 2000:
            raise ValueError('디렉터리 목록이 너무 큽니다. 더 작은 저장소 범위로 연결해 주세요.')
        path = folder + row['path'] + '/'
        trees[path] = {'sha': row['sha']}
        children.append(path)
    trees[folder] = {'sha': result['sha'], 'children': sorted(children)}
    return sorted(children)
