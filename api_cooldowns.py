"""Share server-requested cooldowns without holding webhook workers asleep."""
import hashlib
import math
import re
import threading
import time

COOLDOWNS={}
LOCK=threading.Lock()


def number(value):
    try:value=float(value)
    except (TypeError,ValueError):return None
    return value if math.isfinite(value) and value>=0 else None


def scopes(api,path):
    identity=(api.service,api.base,hashlib.sha256(str(api.token or '').encode()).hexdigest())
    channel=re.match(r'/channels/(\d+)',path)
    return identity+('global',),identity+(channel[1] if channel else path.split('?')[0],)


def remaining(api,path):
    now=time.monotonic()
    with LOCK:
        for k in list(COOLDOWNS):
            if COOLDOWNS[k]<=now:del COOLDOWNS[k]
        return max(0,*(COOLDOWNS.get(k,0)-now for k in scopes(api,path)))


def remember(api,path,delay,global_limit):
    key=scopes(api,path)[0 if global_limit else 1]
    with LOCK:COOLDOWNS[key]=max(COOLDOWNS.get(key,0),time.monotonic()+delay)


def yield_delivery():
    from delivery_metrics import ACTIVE
    return ACTIVE.get() is not None


def delay(headers,detail,attempt):
    explicit=number(detail.get('retry_after',headers.get('Retry-After')))
    reset=number(headers.get('X-RateLimit-Reset')) if headers.get('X-RateLimit-Remaining')=='0' else None
    if reset is not None:explicit=max(explicit or 0,reset-time.time(),.1)
    return explicit,explicit if explicit is not None else 2**attempt
