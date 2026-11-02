# Thread starter status and deletion

The existing bot-owned parent-channel starter embed is edited in place. Its title shows PR Open/Draft/Closed/Merged, ISSUE Open/Closed, or DISCUSSION Open/Answered/Closed, with state color. Closed takes precedence over draft/answered; merged takes precedence over closed. Reopening and marking a PR ready for review resolve from current GitHub state. Existing author, timestamp, URL, marker and non-status metadata are preserved; thread names are not renamed.

Starter creation uses payload metadata, then event delivery refreshes against the authoritative GitHub resource. The current-state read replaces the previous archive-state lookup. There is no periodic polling or bulk history update; legacy starters refresh on their next processed event. Unchanged state does not produce another PATCH. Deleted starter messages are not recreated just to show state.

Only authenticated Issue or Discussion deletion events (or an existing deletion tombstone) permit removal after the resource is no longer available. Comment deletion never authorizes topic deletion. Discord ownership, exact topic marker and thread parent are checked; the source message and public thread share an ID. Thread deletion precedes source deletion and supports lost-response and already-missing retries. Closed topics and all PR states retain the thread. Stale deletion events against a currently existing resource do not delete it.

A durable per-binding deletion tombstone prevents older queued activity from recreating deleted topics. Deferred alert/DND rows and detail-source mappings are cleaned for the removed thread, and frozen DND batches omit tombstoned topics. Once cleanup succeeds, later old events do not rescan Discord history. Permission errors abort rather than masquerading as confirmed deletion. Existing settings, other topics, channels and other servers are untouched.

References: https://docs.discord.com/developers/topics/threads and https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions
