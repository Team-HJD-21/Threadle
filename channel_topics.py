import logging
from pathlib import Path
import threading

from channel_layouts import ChannelLayouts
from runtime_logging import log_exception


class ChannelTopics:
    def __init__(self,service):
        self.service=service

    def run(self,stop):
        while not stop.is_set():
            try:
                path=Path(self.service.root)/'channel-layouts.sqlite3'
                if path.is_file():
                    layouts=ChannelLayouts(path,'')
                    layouts.clean_pending_topics(self.service.discord())
            except Exception as error:
                log_exception(logging.getLogger('channel-topics'),'Topic cleanup deferred error=%s',type(error).__name__,level=logging.WARNING)
            stop.wait(60)

    def start(self,stop):
        worker=threading.Thread(target=self.run,args=(stop,),daemon=True,name='channel-topic-cleanup')
        worker.start()
        return worker
