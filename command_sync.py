import json
import logging
from runtime_logging import log_exception
import threading
import time

from command_jobs import command_definitions, register_extra_commands
from discord_connect import command_definition, register_command


LOG = logging.getLogger('command-sync')


def matches(expected, actual):
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(matches(value,actual.get(key, [] if key=='options' and value==[] else None)) for key,value in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(expected)==len(actual) and all(matches(e,a) for e,a in zip(expected,actual))
    return expected == actual


class CommandSync:
    def __init__(self, service, settings):
        self.service, self.settings = service, settings

    def state(self, status):
        target = self.service.root/'command-sync.json'
        temporary = target.with_suffix('.new')
        temporary.write_text(json.dumps({'state':status,'checked_at':time.time(),'application':self.settings.discord_id}),encoding='utf-8')
        temporary.replace(target)

    def check(self):
        path = '/applications/'+self.settings.discord_id+'/commands'
        desired = [command_definition()]+command_definitions()
        def verified(rows):
            return isinstance(rows,list) and all(any(matches(item,row) for row in rows) for item in desired)
        if not verified(self.service.discord().call('GET',path)):
            register_command(self.service,self.settings)
            register_extra_commands(self.service,self.settings)
            if not verified(self.service.discord().call('GET',path)):
                raise ValueError('Registered commands did not match the expected definitions')
        self.state('registered')

    def run(self, stop):
        while not stop.is_set():
            delay = 300
            try:
                self.check()
            except Exception as error:
                delay = 60
                try:
                    self.state('retrying')
                except OSError:
                    pass
                log_exception(LOG,'Discord command registration retry error=%s',type(error).__name__, level=logging.WARNING)
            stop.wait(delay)

    def start(self, stop):
        worker = threading.Thread(target=self.run,args=(stop,),daemon=True,name='discord-command-sync')
        worker.start()
        return worker
