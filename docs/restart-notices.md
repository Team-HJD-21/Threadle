# Restart announcements

Normal Receiver startup creates one announcement per connected Discord guild,
regardless of how many repositories are connected. The target is the oldest
configured github-alert channel (lowest channel ID); a legacy connection without
an alert channel falls back to its managed issue channel. Its saved language is
used. If any connected repository is in Do Not Disturb, the guild notice is silent.
No mentions or private repository details are included.

The initial blue starting card becomes green when the coordinator has started its
workers and completed its first dispatch pass. This is an edit of the same message,
not another notification and not a promise that every remote integration is healthy.
Pending normal delivery and failed-webhook recovery continue independently.

An unexpected shutdown cannot be announced while the machine is off. This feature
runs after the process starts again. It also covers service restarts during updates;
it does not install Windows shutdown hooks or predict scheduled OS restarts.

Announcements run on a separate daemon and do not block HTTP startup or event
delivery. The existing encrypted registry stores one per-run/guild receipt with a
stable Discord nonce for bounded network retries (three minutes). Channel guild
and message author are checked before editing. Permission/deletion failures are
logged, other guilds continue, and records older than seven days are removed.
Check and smoke modes return before this worker starts, so deployment preflight
does not send announcements.
