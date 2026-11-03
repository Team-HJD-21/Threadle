import datetime as dt
import logging
from runtime_logging import log_exception
import re
import threading
import time
from urllib.parse import urlsplit

LOG = logging.getLogger('webhook-recovery')


class WebhookRecovery:
    def __init__(self, service, clock=time.time):
        self.service, self.clock = service, clock
        with service.registry.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS webhook_recovery(guid TEXT PRIMARY KEY, requested REAL NOT NULL, attempts INTEGER NOT NULL)')

    def check(self):
        if not self.service.tokens:
            return 0
        active = {(b.installation,b.repo_id) for b in self.service.registry.active(include_paused=True) if b.auth == 'app'}
        if not active:
            return 0
        api = self.service.tokens.app_api()
        path = '/app/hook/deliveries?per_page=100'
        candidates = {}
        cutoff = self.clock() - 3 * 86400
        for _ in range(30):
            rows = api.call('GET', path)
            for row in rows:
                if 200 <= int(row.get('status_code') or 0) < 400:
                    continue
                try:
                    when = dt.datetime.fromisoformat(row['delivered_at'].replace('Z','+00:00')).timestamp()
                except (KeyError,ValueError,TypeError):
                    continue
                if when < cutoff or (row.get('installation_id'),row.get('repository_id')) not in active:
                    continue
                if row.get('event') not in ('issues','pull_request','issue_comment','discussion','discussion_comment','pull_request_review','pull_request_review_comment','workflow_run','push'):
                    continue
                guid = row.get('guid','')
                if re.fullmatch(r'[a-fA-F0-9-]{16,64}',guid) and str(row.get('id','')).isdigit():
                    candidates.setdefault(guid,(when,row))
            link = getattr(api, 'last_link', '')
            match = re.search(r'<([^>]+)>;\s*rel="next"',link) if isinstance(link,str) else None
            if not match:
                break
            parsed = urlsplit(match.group(1))
            if parsed.scheme != 'https' or parsed.netloc != 'api.github.com' or parsed.path != '/app/hook/deliveries':
                raise ValueError('Unexpected delivery pagination target')
            path = parsed.path + '?' + parsed.query
        count = 0
        for guid, (_,row) in sorted(candidates.items(),key=lambda entry:entry[1][0]):
            with self.service.registry.db() as db:
                if db.execute('SELECT 1 FROM inbox WHERE id=?',('app:'+guid,)).fetchone():
                    continue
                saved = db.execute('SELECT requested,attempts FROM webhook_recovery WHERE guid=?',(guid,)).fetchone()
                if saved and self.clock() < saved[0] + min(3600,300 * 2 ** min(saved[1]-1,4)):
                    continue
            api.call('POST', '/app/hook/deliveries/' + str(row['id']) + '/attempts')
            with self.service.registry.db() as db:
                db.execute('INSERT INTO webhook_recovery VALUES(?,?,1) ON CONFLICT(guid) DO UPDATE SET requested=excluded.requested,attempts=attempts+1',(guid,self.clock()))
            count += 1
            if count >= 100:
                break
        with self.service.registry.db() as db:
            db.execute('DELETE FROM webhook_recovery WHERE requested<?',(cutoff,))
        if count:
            LOG.info('Requested redelivery for %s missed GitHub events',count)
        return count

    def run(self, stop):
        if stop.wait(10):
            return
        while not stop.is_set():
            try:
                self.check()
            except Exception as error:
                log_exception(LOG,'Webhook recovery will retry error=%s',type(error).__name__, level=logging.WARNING)
            stop.wait(60)

    def start(self, stop):
        worker = threading.Thread(target=self.run,args=(stop,),daemon=True,name='webhook-recovery')
        worker.start()
        return worker
