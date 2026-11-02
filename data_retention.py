import json
import logging
import re
import sqlite3
import threading
import time


LOG = logging.getLogger('retention')
PERIOD = 30 * 86400


class Retention:
    def __init__(self, service, clock=time.time):
        self.service, self.clock = service, clock

    def queue_files(self, identity, legacy):
        if not re.fullmatch('[a-f0-9]{32}',identity):
            raise ValueError('Invalid expired connection identity')
        root = self.service.root.resolve()
        folder = root if legacy else root/'connections'/identity
        target = folder/'queue.sqlite3'
        if not target.resolve().is_relative_to(root):
            raise ValueError('Expired queue path is outside the service folder')
        for suffix in ('','-wal','-shm','-journal'):
            path = folder/('queue.sqlite3'+suffix)
            if not path.resolve().is_relative_to(root):
                raise ValueError('Expired queue file is outside the service folder')
            path.unlink(missing_ok=True)

    def purge(self):
        cutoff = self.clock()-PERIOD
        expired = []
        expired_installations = set()
        with self.service.registry.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('PRAGMA secure_delete=ON')
            rows = db.execute('SELECT b.id,b.guild,b.repo_id,b.legacy_state FROM bindings b JOIN connection_retention t ON t.id=b.id WHERE b.enabled=0 AND t.disconnected_at<=?',(cutoff,)).fetchall()
            retired = db.execute('SELECT r.id,r.data FROM retired_bindings r JOIN connection_retention t ON t.id=r.id WHERE t.disconnected_at<=?',(cutoff,)).fetchall()
            candidates = [(identity,guild,repo,legacy) for identity,guild,repo,legacy in rows]
            for identity,raw in retired:
                old = json.loads(raw)
                candidates.append((identity,old['guild'],old['repo_id'],old['legacy_state']))
            for identity,guild,repo,legacy in candidates:
                if db.execute('SELECT 1 FROM bindings WHERE id=? AND enabled=1',(identity,)).fetchone():
                    continue
                try:
                    self.queue_files(identity,legacy)
                except OSError:
                    LOG.warning('Expired connection cleanup deferred binding=%s',identity)
                    continue
                current = db.execute('SELECT installation FROM bindings WHERE id=?',(identity,)).fetchone()
                if current:
                    expired_installations.add(current[0])
                archived = db.execute('SELECT data FROM retired_bindings WHERE id=?',(identity,)).fetchone()
                if archived:
                    expired_installations.add(json.loads(archived[0])['installation'])
                db.execute('DELETE FROM bindings WHERE id=? AND enabled=0',(identity,))
                db.execute('DELETE FROM retired_bindings WHERE id=?',(identity,))
                db.execute('DELETE FROM connection_audit WHERE binding=?',(identity,))
                db.execute('DELETE FROM legal_acceptances WHERE binding=?',(identity,))
                db.execute('DELETE FROM connection_welcomes WHERE binding=?',(identity,))
                db.execute('DELETE FROM admin_audit WHERE binding=?',(identity,))
                db.execute('DELETE FROM admin_paused WHERE id=?',(identity,))
                db.execute('DELETE FROM connection_retention WHERE id=?',(identity,))
                db.execute('INSERT OR IGNORE INTO retention_scopes VALUES(?)',(str(guild)+':'+str(repo),))
                db.execute('INSERT OR IGNORE INTO retention_scopes VALUES(?)',(str(guild),))
                expired.append((identity,guild,repo))
            identities = {row[0] for row in expired}
            if identities:
                for identity,raw in db.execute('SELECT id,recipients FROM inbox').fetchall():
                    recipients = json.loads(raw)
                    remaining = [recipient for recipient in recipients if recipient not in identities]
                    if not remaining:
                        db.execute('DELETE FROM inbox WHERE id=?',(identity,))
                    elif remaining != recipients:
                        db.execute('UPDATE inbox SET recipients=? WHERE id=?',(json.dumps(remaining),identity))
            keep_guilds = {str(row[0]) for row in db.execute('SELECT DISTINCT guild FROM bindings')}
            keep_scopes = {str(row[0])+':'+str(row[1]) for row in db.execute('SELECT guild,repo_id FROM bindings')}
            keep_installations = {row[0] for row in db.execute('SELECT DISTINCT installation FROM bindings')}
            for (raw,) in db.execute('SELECT data FROM retired_bindings'):
                row = json.loads(raw)
                keep_guilds.add(str(row['guild']))
                keep_scopes.add(str(row['guild'])+':'+str(row['repo_id']))
                keep_installations.add(row['installation'])
            for (guild,) in db.execute('SELECT guild FROM guild_profiles').fetchall():
                if guild not in keep_guilds:
                    db.execute('DELETE FROM guild_profiles WHERE guild=?',(guild,))
            db.execute("DELETE FROM github_profiles WHERE user NOT IN (SELECT github_user FROM connection_audit WHERE github_user<>'')")
            for (installation,) in db.execute('SELECT id FROM installations').fetchall():
                if installation in expired_installations and installation not in keep_installations:
                    db.execute('DELETE FROM installations WHERE id=?',(installation,))
        for identity,_,_ in expired:
            self.service.queues.pop(identity,None)
        layout_path = self.service.root/'channel-layouts.sqlite3'
        if layout_path.is_file():
            from contextlib import closing
            with self.service.registry.db() as registry:
                registry.execute('BEGIN IMMEDIATE')
                scopes = {str(guild)+':'+str(repo) for guild,repo in registry.execute('SELECT guild,repo_id FROM bindings')}
                guilds = {str(guild) for (guild,) in registry.execute('SELECT DISTINCT guild FROM bindings')}
                for (raw,) in registry.execute('SELECT data FROM retired_bindings'):
                    row = json.loads(raw)
                    scopes.add(str(row['guild'])+':'+str(row['repo_id']))
                    guilds.add(str(row['guild']))
                with closing(sqlite3.connect(layout_path,timeout=10)) as db:
                    with db:
                        db.execute('PRAGMA secure_delete=ON')
                        for (scope,) in registry.execute('SELECT scope FROM retention_scopes').fetchall():
                            if scope not in scopes and scope not in guilds:
                                db.execute('DELETE FROM layouts WHERE guild=?',(scope,))
                            registry.execute('DELETE FROM retention_scopes WHERE scope=?',(scope,))
                        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='topic_cleanup'").fetchone():
                            db.execute('DELETE FROM topic_cleanup WHERE scope NOT IN (SELECT guild FROM layouts)')
        with self.service.file_index.connect() as db:
            db.execute('PRAGMA secure_delete=ON')
            db.execute('DELETE FROM pr_files WHERE created<?',(self.clock()-7*86400,))
        from operational_retention import purge_operations
        purge_operations(self.service.root, self.clock())
        return len(expired)

    def run(self, stop):
        while not stop.is_set():
            try:
                count = self.purge()
                if count:
                    LOG.info('Expired connections removed count=%s',count)
            except Exception as error:
                LOG.warning('Connection cleanup retry error=%s',type(error).__name__)
            stop.wait(86400)

    def start(self, stop):
        worker = threading.Thread(target=self.run,args=(stop,),daemon=True,name='connection-retention')
        worker.start()
        return worker
