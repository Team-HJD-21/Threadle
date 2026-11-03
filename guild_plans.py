"""Server-scoped subscription metadata and one extensible entitlement boundary.

No checkout or paid features are enabled by this module. A future verified billing
integration must write subscriptions; repository settings never grant a plan.
"""
import time

PLAN_RANK={'free':0,'pro':1}
FEATURE_MIN_PLAN={'notifications':'free'}


def subscription(registry,guild,now=None):
    with registry.db() as db:
        row=db.execute('SELECT plan,status,expires_at FROM guild_subscriptions WHERE guild=?',(str(guild),)).fetchone()
    if row is None:return {'plan':'free','subscribed_plan':'free','status':'none','expires_at':None}
    plan,status,expiry=row
    valid=plan in PLAN_RANK and status in ('active','trialing') and (expiry is None or expiry>(time.time() if now is None else now))
    return {'plan':plan if valid else 'free','subscribed_plan':plan,'status':status if expiry is None or expiry>(time.time() if now is None else now) else 'expired','expires_at':expiry}


def allows(registry,guild,feature,now=None):
    if feature not in FEATURE_MIN_PLAN:raise ValueError('Unregistered feature policy')
    return PLAN_RANK[subscription(registry,guild,now)['plan']]>=PLAN_RANK[FEATURE_MIN_PLAN[feature]]
