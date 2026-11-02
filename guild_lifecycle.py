import logging
import threading
import time

from discord_threads import ApiError


LOG = logging.getLogger('guild-lifecycle')


class GuildLifecycle:
    def __init__(self, service):
        self.service = service

    def removed(self, guild):
        if getattr(guild, 'unavailable', False):
            return
        count = self.service.registry.remove_guild(str(guild.id))
        LOG.info('Discord server removed guild=%s disconnected=%s', guild.id, count)

    def check(self):
        api = self.service.discord()
        membership = None
        for guild, identities in self.service.registry.guild_connections().items():
            try:
                result = api.call('GET', '/guilds/' + guild)
            except ApiError as error:
                gone = error.status == 404 and error.code == 10004
                if error.status == 403 and error.code == 50001:
                    try:
                        membership = membership if membership is not None else self.membership(api)
                        gone = guild not in membership
                    except (ApiError, ValueError):
                        pass
                if gone:
                    count = self.service.registry.remove_guild(guild, expected=identities)
                    LOG.info('Discord membership ended guild=%s disconnected=%s', guild, count)
                else:
                    LOG.warning('Discord membership check deferred guild=%s status=%s', guild, error.status)
                continue
            if str(result.get('id')) == guild:
                self.service.registry.save_guild_name(guild, result.get('name'))
        self.profiles()

    @staticmethod
    def membership(api):
        guilds, cursor = set(), 0
        for _ in range(1000):
            rows = api.call('GET', f'/users/@me/guilds?limit=200&after={cursor}')
            if not isinstance(rows,list) or any(not str(row.get('id','')).isdigit() for row in rows):
                raise ValueError('Invalid Discord membership response')
            guilds.update(str(row['id']) for row in rows)
            if len(rows) < 200:
                return guilds
            after = max(int(row['id']) for row in rows)
            if after <= cursor:
                raise ValueError('Discord membership pagination did not advance')
            cursor = after
        raise ValueError('Discord membership list is incomplete')

    def profiles(self):
        with self.service.registry.db() as db:
            users = [row[0] for row in db.execute('SELECT DISTINCT a.github_user FROM connection_audit a '
                     'LEFT JOIN github_profiles p ON p.user=a.github_user WHERE a.action=\'connected\' '
                     'AND a.github_user<>\'\' AND (p.checked IS NULL OR p.checked<?) LIMIT 5', (time.time()-86400,))]
        api = self.service.api_factory('GitHub', 'https://api.github.com', '', '')
        for user in users:
            if not user.isdigit():
                continue
            try:
                result = api.call('GET', '/user/' + user)
                if str(result.get('id')) == user:
                    self.service.registry.save_github_login(user, result.get('login'), time.time())
            except ApiError:
                return

    def run(self, stop):
        while not stop.is_set():
            try:
                self.check()
            except Exception as error:
                LOG.warning('Discord membership check retry error=%s', type(error).__name__)
            stop.wait(300)

    def start(self, stop):
        worker = threading.Thread(target=self.run, args=(stop,), daemon=True, name='discord-membership')
        worker.start()
        return worker
