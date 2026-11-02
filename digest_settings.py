"""Independent digest switches with read compatibility for legacy frequency."""

def enabled(options,kind):
    return options.get(kind+'_digest',options.get('digest','off')==kind)


def apply(current,updates,now):
    result=dict(current)
    # Freeze legacy weekly time before daily time is edited.
    result.setdefault('weekly_digest_hour',current.get('digest_hour',9))
    for kind in ('daily','weekly'):result[kind+'_digest']=enabled(current,kind)
    result.update(updates)
    if 'digest' in updates:
        for kind in ('daily','weekly'):
            if kind+'_digest' not in updates:result[kind+'_digest']=updates['digest']==kind
    for kind in ('daily','weekly'):
        switch=kind+'_digest';stamp=kind+'_enabled_at'
        def schedule(options):
            hour=options.get('digest_hour',9) if kind=='daily' else options.get('weekly_digest_hour',options.get('digest_hour',9))
            return (bool(enabled(options,kind)),hour,options.get('digest_weekday',0) if kind=='weekly' else None,options.get('utc_offset',9))
        changed=schedule(current)!=schedule(result)
        if enabled(result,kind) and (changed or not result.get(stamp,result.get('enabled_at'))):
            result[stamp]=now
    return result
