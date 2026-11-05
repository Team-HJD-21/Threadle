"""Select activity worth a secondary alert without changing thread delivery."""

METADATA_ACTIONS = {
    'assigned', 'unassigned', 'labeled', 'unlabeled', 'typed', 'untyped',
    'field_added', 'field_removed', 'milestoned', 'demilestoned',
}
COMMENT_EVENTS = {'issue_comment', 'discussion_comment', 'pull_request_review_comment'}


def should_alert(event, payload):
    if event == 'pull_request_review_comment' and payload.get('_bridge_review_echo'):
        return False
    if event == 'threadle_bundle':
        return any(should_alert(row.get('event'), row.get('payload') or {})
                   for row in payload.get('_events', []) if isinstance(row, dict))
    action = payload.get('action')
    if event=='pull_request_review_thread':return False
    if action in METADATA_ACTIONS or action == 'edited':
        return False
    if event in COMMENT_EVENTS:
        return action not in ('deleted', 'edited')
    if event == 'pull_request_review':
        if action not in (None, 'submitted'):
            return False
        review = payload.get('review') or {}
        state = str(review.get('state', '')).lower()
        return state in ('approved', 'changes_requested') or (
            state == 'commented' and bool(str(review.get('body') or '').strip()))
    # Existing lifecycle and commit notifications retain their current policy.
    # CI failures, conflicts, reminders and digests use their own sending paths.
    return True


def filter_alert(event, payload):
    """Return the visible event/payload, or None when no alert is needed.

    A bundle's title and activity count use only the entries that remain visible.
    The original payload stays intact for thread updates and review scheduling.
    """
    if event != 'threadle_bundle':
        return (event, payload) if should_alert(event, payload) else None
    entries = [row for row in payload.get('_events', []) if isinstance(row, dict)
               and should_alert(row.get('event'), row.get('payload') or {})]
    if not entries:
        return None
    if len(entries) == 1 and entries[0].get('event'):
        row = entries[0]
        return row['event'], row.get('payload') or {}
    created = any(row.get('event') in ('issues', 'pull_request', 'pull_request_target', 'discussion')
                  and (row.get('payload') or {}).get('action') in ('opened', 'created') for row in entries)
    return event, dict(payload, _events=entries, _created=created)
