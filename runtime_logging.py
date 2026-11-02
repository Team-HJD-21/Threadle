import faulthandler
import datetime as dt
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pathlib import Path
import re
import sqlite3
import sys
import threading
import traceback

FAULT_STREAM = None


class SafeFormatter(logging.Formatter):
    def __init__(self, *args, secrets=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.secrets = sorted({value for value in secrets if isinstance(value,str) and len(value)>=8},key=len,reverse=True)

    def redact(self, text):
        for value in self.secrets:
            text=text.replace(value,'[REDACTED]')
        text=re.sub(r'(?i)(Bearer|Bot) +[^\s,;]+',r'\1 [REDACTED]',text)
        text=re.sub(r'gh[pousr]_[A-Za-z0-9_]{16,}','[REDACTED]',text)
        text=re.sub(r'(/webhooks/\d+/)[^/\s?]+',r'\1[REDACTED]',text)
        text=re.sub(r'(https?://[^\s?]+)\?[^\s]+',r'\1?[REDACTED]',text)
        return text

    def formatException(self, info):
        # No frame locals or source lines: command payloads and credentials must not enter logs.
        output=[];seen=set();error=info[1]
        while error is not None and id(error) not in seen:
            seen.add(id(error))
            output.append('Traceback (most recent call last):')
            for frame in traceback.extract_tb(error.__traceback__,limit=40):
                output.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
            name=type(error).__name__
            if hasattr(error,'status'):
                output.append(f'{name}: status={error.status} code={getattr(error,"code",None)}')
            elif isinstance(error,(sqlite3.Error,OSError)):
                output.append(name+': '+str(error)[:500])
            else:
                output.append(name+' (exception message omitted to protect payloads)')
            error=error.__cause__ or (None if error.__suppress_context__ else error.__context__)
        return self.redact('\n'.join(output))

    def format(self, record):
        return self.redact(super().format(record))


def log_exception(logger, message, *args, level=logging.ERROR):
    error=sys.exc_info()[1]
    secrets=[value for key,value in os.environ.items() if re.search(r'TOKEN|SECRET|PASSWORD|COOKIE|PRIVATE_KEY$',key,re.I)]
    formatter=SafeFormatter(secrets=secrets)
    detail=formatter.formatException(sys.exc_info()) if error is not None else 'Exception context unavailable'
    # Serialize a safe trace here as well, so a caller's custom log handler cannot reveal locals/source.
    logger.log(level,message+'\n%s',*args,detail)


def configure(root, env=None):
    global FAULT_STREAM
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    env=os.environ if env is None else env
    secrets=[value for key,value in env.items() if re.search(r'TOKEN|SECRET|PASSWORD|COOKIE|PRIVATE_KEY$',key,re.I)]
    handler=TimedRotatingFileHandler(root/'receiver.log',when='midnight',interval=1,backupCount=30,encoding='utf-8',utc=True)
    handler.setFormatter(SafeFormatter('%(asctime)s %(levelname)s %(name)s pid=%(process)d thread=%(threadName)s %(message)s',datefmt='%Y-%m-%dT%H:%M:%S%z',secrets=secrets))
    logging.basicConfig(level=logging.INFO,handlers=[handler],force=True)
    try:
        fault=root/'receiver-fault.log'
        if fault.exists() and fault.stat().st_size>2*1024*1024:
            older=root/'receiver-fault.log.1'
            older.replace(root/'receiver-fault.log.2') if older.exists() else None
            fault.replace(older)
        FAULT_STREAM=fault.open('a',encoding='utf-8')
        FAULT_STREAM.write('\n'+dt.datetime.now(dt.timezone.utc).isoformat()+' Receiver native-fault capture opened pid='+str(os.getpid())+'\n');FAULT_STREAM.flush()
        faulthandler.enable(file=FAULT_STREAM,all_threads=True)
    except (OSError,RuntimeError):
        log_exception(logging.getLogger('runtime'),'Native fault capture unavailable; Python error logging remains enabled')
    def unhandled(kind,error,tb):
        logging.getLogger('runtime').critical('Unhandled main-thread exception',exc_info=(kind,error,tb))
    def thread_error(args):
        if args.exc_type is not SystemExit:
            logging.getLogger('runtime').critical('Unhandled worker-thread exception thread=%s',args.thread.name if args.thread else 'unknown',exc_info=(args.exc_type,args.exc_value,args.exc_traceback))
    sys.excepthook=unhandled
    threading.excepthook=thread_error
    logging.getLogger('runtime').info('Receiver starting python=%s',sys.version.split()[0])
