# Do Not Disturb (beta.6)

The presentation name is 방해금지 시간 / Do Not Disturb. Existing quiet, quiet_start and quiet_end configuration keys and slash-command option names stay compatible. No binding settings are migrated.

Ordinary PR/Issue/Discussion root and detail POSTs include SUPPRESS_NOTIFICATIONS during DND. The mode is checked again immediately before each POST, so long media processing does not miss the boundary. A durable per-delivery mode keeps retries silent even if DND has ended by retry time. Edits update existing messages; replacement pages stay silent.

Ordinary alert heads-ups during DND are replaced by a durable roundup of unique changed topics, including thread links and activity counts. Small roundups use one message; large lists paginate. Recorded topics and frozen batches survive restarts and lost responses. A late in-flight delivery may produce a subsequent catch-up batch rather than be dropped. Existing deferred topic alerts whose due time is the configured DND end are converted once. Historical source titles unavailable in those rows remain status-based.

CI/CD requested/in-progress/completed state messages stay immediately event-driven and always silent in the CI/CD channel. Failure heads-ups and associated PR CI failure notices bypass DND and normal batching, with regular alert notifications. Existing deferred CI failures are released on the next scheduler tick. Review reminders and scheduled digests retain their existing DND behavior.

All data remains per binding queue; no cross-server settings, category, permissions or channels are changed. There is no live Discord sending in tests.
