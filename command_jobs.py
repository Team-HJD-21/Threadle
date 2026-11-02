import json
import time
import threading
import logging
from runtime_logging import log_exception

from connect_auth import manages_guild
from team_notifications import validate_options


class CommandJobs:
    def __init__(self, service, sessions):
        self.service, self.sessions = service, sessions
        with sessions.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS command_pages(job TEXT NOT NULL,page INTEGER NOT NULL,message TEXT,PRIMARY KEY(job,page))')
            db.execute('CREATE TABLE IF NOT EXISTS command_jobs(id TEXT PRIMARY KEY, expires REAL NOT NULL, payload BLOB, done INTEGER NOT NULL DEFAULT 0)')
            if 'result' not in {r[1] for r in db.execute('PRAGMA table_info(command_jobs)')}:
                db.execute('ALTER TABLE command_jobs ADD COLUMN result BLOB')

    def enqueue(self, payload):
        if not manages_guild(payload.get('member', {})):
            return 'Manage Server permission is required.'
        token = payload.get('token', '')
        if not isinstance(token, str) or not token or len(token) > 512:
            return 'This command could not be verified. Please run it again.'
        with self.sessions.db() as db:
            if db.execute('SELECT count(*) FROM command_jobs WHERE done=0').fetchone()[0] >= 100:
                return 'Commands are busy. Please try again shortly.'
            db.execute('INSERT OR IGNORE INTO command_jobs(id,expires,payload) VALUES(?,?,?)',
                       (payload['id'], time.time() + 840, self.sessions.encode(payload)))
        return None

    def worker(self, stop):
        while not stop.is_set():
            try:
                self.process()
            except Exception as error:
                log_exception(logging.getLogger('commands'),'Command retry error=%s', type(error).__name__)
                stop.wait(3)
            stop.wait(.25)

    def start(self, stop):
        thread = threading.Thread(target=self.worker, args=(stop,), daemon=True)
        thread.start()
        return thread

    def process(self):
        with self.sessions.db() as db:
            db.execute('UPDATE command_jobs SET payload=NULL,result=NULL,done=1 WHERE expires<?', (time.time(),))
            db.execute('DELETE FROM command_jobs WHERE expires<?', (time.time() - 86400,))
            db.execute('DELETE FROM command_pages WHERE job NOT IN (SELECT id FROM command_jobs)')
            row = db.execute('SELECT id,payload,result FROM command_jobs WHERE done=0 ORDER BY rowid LIMIT 1').fetchone()
        if not row:
            return
        payload = json.loads(self.sessions.cipher.decrypt(row[1]))
        if row[2]:
            stored = self.sessions.cipher.decrypt(row[2]).decode()
            try:
                decoded=json.loads(stored)
                message=decoded['pages'] if isinstance(decoded,dict) and decoded.get('format')=='threadle-pages' else stored
            except (ValueError,KeyError):
                message=stored
        else:
            try:
                message = self.execute(payload)
            except ValueError as error:
                message = str(error)
            except Exception:
                log_exception(logging.getLogger('commands'),'Command execution failed')
                message = 'The request could not be completed. Check repository access and try again.'
            with self.sessions.db() as db:
                db.execute('UPDATE command_jobs SET result=? WHERE id=?', (self.sessions.cipher.encrypt((json.dumps({'format':'threadle-pages','pages':message}) if isinstance(message,list) else message).encode()), row[0]))
        pages=message if isinstance(message,list) else [message]
        endpoint='/webhooks/'+str(payload['application_id'])+'/'+payload['token']
        for index,content in enumerate(pages):
            with self.sessions.db() as db:
                saved=db.execute('SELECT message FROM command_pages WHERE job=? AND page=?',(row[0],index)).fetchone()
            if saved and saved[0]:
                continue
            data={'content':content[:1900],'allowed_mentions':{'parse':[]}}
            if payload['data']['name']=='summary':
                heading,_,body=content.partition('\n')
                data={'embeds':[{'title':heading.strip('*'),'description':body.strip(),'color':0x2DA44E}],'allowed_mentions':{'parse':[]}}
            if index==0:
                result=self.service.discord().call('PATCH',endpoint+'/messages/@original',data)
            else:
                result=self.service.discord().call('POST',endpoint,dict(data,flags=64))
            with self.sessions.db() as db:
                db.execute('INSERT OR REPLACE INTO command_pages VALUES(?,?,?)',(row[0],index,str((result or {}).get('id','original' if index==0 else 'sent'))))
        with self.sessions.db() as db:
            db.execute('UPDATE command_jobs SET payload=NULL,result=NULL,done=1 WHERE id=?', (row[0],))
            db.execute('DELETE FROM command_pages WHERE job=?',(row[0],))

    def execute(self, payload):
        guild, user = str(payload['guild_id']), str(payload['member']['user']['id'])
        if not manages_guild(payload['member']):
            raise ValueError('Manage Server permission is required.')
        api = self.service.discord()
        member = api.call('GET', '/guilds/' + guild + '/members/' + user)
        roles = api.call('GET', '/guilds/' + guild + '/roles')
        info = api.call('GET', '/guilds/' + guild)
        permissions = 0
        for role in roles:
            if str(role['id']) == guild or str(role['id']) in member.get('roles', []):
                permissions |= int(role.get('permissions', 0))
        if str(info.get('owner_id')) != user and not permissions & ((1 << 3) | (1 << 5)):
            raise ValueError('Manage Server permission is no longer available.')
        entries = payload.get('data', {}).get('options', [])
        options = {v['name']: v['value'] for v in entries}
        if len(options) != len(entries):
            raise ValueError('Duplicate command options are not allowed.')
        candidates = [b for b in self.service.registry.active() if b.auth == 'app' and b.config['guild_id'] == guild]
        if payload['data']['name'] == 'summary':
            channel = api.call('GET', '/channels/' + str(payload['channel_id']))
            if str(channel.get('guild_id')) != guild:
                raise ValueError('The command channel does not belong to this server.')
            from repository_summary import all_repository_summaries
            return all_repository_summaries(self.service,candidates)
        if options.get('repository'):
            candidates = [b for b in candidates if b.id == options['repository'] or b.repo.casefold() == options['repository'].casefold()]
        else:
            channel = str(payload['channel_id'])
            matched = [b for b in candidates if channel in list(b.config['channels'].values()) + [b.config.get('alert_channel'), b.config.get('ci_channel')] + [g['channel'] for g in b.config.get('routing_groups', [])]]
            candidates = matched or candidates
        if len(candidates) != 1:
            raise ValueError('Specify repository by selecting a connected repository from the suggestions.')
        binding = candidates[0]
        channel = api.call('GET', '/channels/' + str(payload['channel_id']))
        if str(channel.get('guild_id')) != guild:
            raise ValueError('The command channel does not belong to this server.')
        if payload['data']['name'] == 'summary':
            return self.summary(binding, options.get('type', 'pr'), options.get('page', 1))
        updates = {k: v for k, v in options.items() if k != 'repository'}
        current = dict({'review_hours': 0, 'batch_seconds': 0, 'digest': 'off', 'digest_hour': 9, 'digest_weekday': 0, 'utc_offset': 9, 'quiet': False, 'quiet_start': 22, 'quiet_end': 8}, **binding.config.get('team_notifications', {}))
        if not updates:
            return self.describe(binding.repo, current, binding.config.get('language','ko')) + '\nPass an option to change it.'
        allowed = {'language', 'review_hours', 'batch_seconds', 'digest', 'digest_hour', 'digest_weekday', 'utc_offset', 'quiet', 'quiet_start', 'quiet_end'}
        if set(updates) - allowed:
            raise ValueError('Unknown notification setting.')
        from notification_language import normalize_language
        language = normalize_language(updates.pop('language',binding.config.get('language','ko')))
        current.update(updates)
        try:
            validate_options(current)
        except ValueError:
            raise ValueError('Invalid settings. Quiet hours must have different start and end times; use the listed option ranges.') from None
        if updates.get('review_hours') and not binding.config.get('team_notifications', {}).get('review_hours'):
            current['review_since'] = time.time()
        if current.get('digest', 'off') != 'off' and (not current.get('enabled_at') or set(updates) & {'digest','digest_hour','digest_weekday','utc_offset'}):
            current['enabled_at'] = time.time()
        config = dict(binding.config, team_notifications=current, language=language)
        self.service.registry.configure(binding.id, config, user, 'discord-command')
        return self.describe(binding.repo, current, language) + '\nNotification settings updated.'

    @staticmethod
    def describe(repo, options, language='ko'):
        hours = options.get('review_hours', 0)
        batch = options.get('batch_seconds', 0)
        weekdays = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return (f'**{repo}**\nNotification language: '+('English (EN)' if language=='en' else 'Korean (KR)')+'\nOpen PR reminder: ' + (f'{hours} hours' if hours else 'off')
                + '\nNotification batching: ' + (f'{batch} seconds' if batch else 'instant')
                + f"\nPR and issue digest: {options.get('digest', 'off')} · {options.get('digest_hour', 9):02}:00 · {weekdays[options.get('digest_weekday', 0)]}"
                + f"\nTime zone: UTC{options.get('utc_offset', 9):+d}"
                + '\nQuiet hours: ' + (f"{options.get('quiet_start', 22):02}:00–{options.get('quiet_end', 8):02}:00" if options.get('quiet') else 'off'))

    def summary(self, binding, kind, page=1):
        if kind not in ('pr', 'issue', 'discussion'):
            raise ValueError('Choose PR, Issue, or Discussion.')
        from repository_summary import open_items, summary_page
        return summary_page(binding.repo, kind, open_items(self.service.github(binding), binding.repo, kind), page)


def command_definitions():
    def choice(name, values):
        return {'name': name, 'value': values}
    repository = {'type':3, 'name':'repository', 'description':'Choose a connected repository.', 'autocomplete':True}
    integers = [('review_hours','Repeat open PR reminders every N hours; 0 disables reminders.',0,720),('batch_seconds','Batch interval: 0, 60, or 300 seconds.',0,300),('digest_hour','Digest hour in the configured time zone.',0,23),('digest_weekday','Weekly digest weekday: Monday 0 through Sunday 6.',0,6),('utc_offset','Time zone offset from UTC in whole hours.',-12,14),('quiet_start','Quiet hours start, in the configured time zone.',0,23),('quiet_end','Quiet hours end, in the configured time zone.',0,23)]
    options = [repository, {'type':3,'name':'language','description':'Notification language for this repository.','choices':[choice('Korean (KR)','ko'),choice('English (EN)','en')]}]
    for name, description, low, high in integers:
        item = {'type':4,'name':name,'description':description,'min_value':low,'max_value':high}
        if name == 'batch_seconds':
            item['choices']=[choice('Off',0),choice('Every minute',60),choice('Every five minutes',300)]
        options.append(item)
    options += [{'type':3,'name':'digest','description':'Scheduled PR and issue summary frequency.','choices':[choice('Off','off'),choice('Daily','daily'),choice('Weekly','weekly')]}, {'type':5,'name':'quiet','description':'Enable or disable quiet hours.'}]
    base = {'type':1,'default_member_permissions':'32','contexts':[0],'integration_types':[0],'name_localizations':None,'description_localizations':None}
    return [dict(base,name='summary',description='Show open PRs, issues, and discussions for every connected repository.',options=[]),dict(base,name='settings',description='View or change notification preferences for a connected repository.',options=options)]


def register_extra_commands(service, settings):
    api = service.discord()
    path = '/applications/' + settings.discord_id + '/commands'
    commands = api.call('GET',path)
    if not isinstance(commands,list):
        raise ValueError('Could not read existing application commands')
    for definition in command_definitions():
        existing = next((c for c in commands if c.get('name')==definition['name'] and c.get('type')==1),None)
        result = api.call('PATCH',path+'/'+str(existing['id']),{k:v for k,v in definition.items() if k!='type'}) if existing else api.call('POST',path,definition)
        if not isinstance(result,dict) or not str(result.get('id','')).isdigit():
            raise ValueError('Could not verify /'+definition['name'])
