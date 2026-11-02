def normalize_language(value):
    result={'ko':'ko','kr':'ko','en':'en'}.get(str(value).strip().lower())
    if result is None:
        raise ValueError('Choose Korean (KR) or English (EN).')
    return result


def selected_language(session,key,existing=None,default='ko'):
    if existing is not None:
        return normalize_language(existing.config.get('language','ko'))
    choice=session.data.get('notification_language',{})
    if choice.get('key')==key:
        return normalize_language(choice['language'])
    return normalize_language((existing.config if existing else {}).get('language',default))
