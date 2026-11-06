"""Reuse Discord metadata reads only within one active webhook delivery."""
import copy
import hashlib
import re

from delivery_metrics import ACTIVE

PATH = re.compile(r'/channels/\d+(?:/messages/\d+)?|/guilds/\d+/(?:roles|members/\d+)')


def cache_key(api,method,path,data):
    context=ACTIVE.get()
    if context is None or api.service!='Discord' or method!='GET' or data is not None or not PATH.fullmatch(path):
        return None
    # Separate credentials and destinations; this dict dies at delivery_metrics.end.
    return api.base,api.scheme,hashlib.sha256(str(api.token or '').encode()).digest(),path


def read(key):
    context=ACTIVE.get()
    if key is None or context is None:return False,None
    cache=context.get('discord_reads',{})
    if key not in cache:return False,None
    context['discord_reads_reused']=context.get('discord_reads_reused',0)+1
    return True,copy.deepcopy(cache[key])


def remember(key,result):
    context=ACTIVE.get()
    if key is not None and context is not None:
        context.setdefault('discord_reads',{})[key]=copy.deepcopy(result)


def invalidate(method,path):
    context=ACTIVE.get()
    # Message POSTs cannot change parent/owner/lock metadata. Writes to channels,
    # edits and deletions can: force fresh reads after them, including on retries.
    if context is not None and method not in ('GET','HEAD') and not (
            method=='POST' and re.fullmatch(r'/channels/\d+/messages',path)):
        context.pop('discord_reads',None)
