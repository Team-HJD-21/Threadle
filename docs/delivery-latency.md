# Immediate delivery and editable metadata updates (beta.12)

Normal delivery has no internal coalescing delay (formerly eight seconds). The 0.2-second ingress dispatch interval and event-woken idle workers, GitHub delivery, queued work, enrichment/media downloads, Discord requests and throttling still contribute. This is not an end-to-end latency benchmark or a zero-latency promise. User-configured one/five-minute alert batching and DND schedules remain intact.

The first metadata notification sends immediately. Since beta.15, labels, assignees, milestones, review requests and issue types share one editable state card per topic with no family/time split. New events reuse the latest mapped legacy metadata card when available; existing historical cards are retained rather than deleted. Snapshot fields omitted by a later event retain their last known values, while explicit empty/null values clear them. The type remains visible when a later milestone update arrives. Separate comments, commits and Closed/Reopened/Merged history remain independent. Full issue-field deltas without a snapshot remain separate.

Per-event source selection persists before posting. Restart, failed/lost responses and older revisions preserve the same message/source and do not duplicate or overwrite newer state. DND metadata replays update the same current-state card; stale revisions cannot overwrite newer state. Legacy retries preserve their old source/receipt to avoid uncertain-send duplication. Old frozen delivery groups remain replayable. New metadata sources do not scan thread history on their first attempt; retries still recover source-tagged pages safely.

Queue.next(window_seconds) can skip an unready optional/legacy batch instead of stopping the worker on it. Default Queue.next() and the service now have no coalescing wait. Already executing HTTP requests are not preempted; retries inside such calls can still occupy the per-binding worker. This patch does not add concurrent edits to the same thread or bypass API limits.

Recommended follow-up, separate from this patch: measure webhook receipt / queue start / first Discord post / completion, yield long network retries back to the durable queue, share authorized GitHub lookups, coordinate Discord rate-limit buckets, and rehearse SQLite backup/restoration. Deployment file backups are not a replacement for operational database recovery. No live latency benchmark or production message sending was performed here.
# Idle worker cost

Binding enumeration reads all active connections in one database connection,
preserving disabled installation and paused connection filtering. Previously it
opened the registry once per binding as well as once for the list.

Idle delivery workers wait on a local event instead of reopening the encrypted
queue five times each second. Committing a new queue job signals the event, and a
persisted retry deadline shortens the wait. The worker clears the signal before
checking for work, so commits between the check and wait are remembered. A bounded
five-second recovery timeout covers writes from another process. Configuration
changes and shutdown explicitly wake affected workers. Completed one-off review
repairs stop reading their progress table on every idle turn. Encryption and
durable queue ordering remain unchanged; no message batching delay is added.

Local Windows comparison using a temporary encrypted database with ten bindings
and twenty active-list reads: 0.520s before versus 0.054s after (process CPU time
0.469s versus 0.047s). This measures that query path, not overall production CPU;
the operator's notebook utilization must be compared after deployment.
