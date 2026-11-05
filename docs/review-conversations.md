# Code and file review conversations

Subscribe the GitHub App to `pull_request_review_thread`, alongside the existing
review and review-comment events. Existing Pull requests read permission is enough;
users do not need to invite the Discord bot again.

The overall submitted review remains its own Commented/Approved/Changes requested
message and displays its Review ID. Each code/file conversation has one mutable
card identifying that same parent review with a GitHub link, file/line, and
Unresolved (red) or Resolved (green). Replies append author, local Discord timestamp
and original Markdown to that card. Edits and deletions refresh the conversation.
The overall review is delivered before its code/file cards for Commented,
Approved, and Changes requested. If a child webhook arrives first, the current
GraphQL snapshot supplies the submitted parent and its full body; its normal
webhook later reuses the same review source rather than posting another message.
Failed parent delivery retries before publishing the child. Existing historical
cards are not deleted/reposted to reorder Discord history.

New conversations from the same submitted review are sent in root-comment
creation order (numeric comment ID breaks ties), even when webhooks arrive in
reverse order. The existing paginated snapshot includes the parent review's
comment count; an incomplete batch retries before sending its first card. No
fixed buffering delay or recurring scan is introduced.

Dates beside comment authors use Discord's short date-and-time timestamp (`s`),
which displays an actual date and time in each reader's local time zone and locale,
instead of long month names or a relative Yesterday/Today label.
Long bodies and media use the existing lossless mutable pagination, so unusually
long conversations can span continuation messages with the same status color.

Only relevant incoming events trigger a read of the affected PR's current review
threads. Both thread and comment connections are paginated. GraphQL failures and
incomplete snapshots retry without substituting partial content; PENDING review
comments are excluded. Current GitHub state wins over delayed webhook payloads.
Resolution events update the existing card without an extra github-alert message.
Normal new-comment alert policy remains unchanged.

The per-connection queue stores only thread/comment identity mappings for this
feature. Bodies are not cached in a new table. Existing encrypted queue storage,
message ownership checks, hidden source markers, retry receipts, media permission
checks and repository-scoped credentials are reused. Old root comment cards retain
their source identity; mapped old reply messages are cleaned after the aggregate
card has been delivered. Authenticated deletion tombstones prevent stale reads or
late create events from resurrecting removed comments. Deleting a reply removes
only that reply; deleting a code/file conversation's first comment removes its
entire card and continuation pages, while preserving other reviews and the PR
thread. Deleted forwarded replies no longer wait for an impossible snapshot that
still contains them.

The next deployment performs a one-off reconciliation of already tracked review
cards, one PR at a time during idle worker turns. It updates stale text and removes
previously missed deletions, confirming missing comments through the GitHub API.
Read errors preserve cards. Progress and retry state survive restarts; completed
topics are not rescanned on later restarts. Only identity/progress metadata is
stored, using the existing encrypted per-connection queue.

Members who enable comment forwarding through `/account connect` can use Discord
Reply on a review card (including continuation pages) to reply in that GitHub
review conversation using their own account. This needs Pull requests write
permission in addition to the read-only notification setup above. The webhook
refreshes the existing conversation card first; only then can the bot remove the
unchanged Discord input, with Manage Messages permission. Delayed snapshots,
failed updates, changed inputs, and disconnected accounts preserve the input.
An uncertain GitHub response is reconciled using the signed receipt, never by
blindly repeating the comment write. No recurring PR scan is added.

Verification includes fake Discord delivery/edit/delete/retry tests and a read-only
query against PR #616 (four conversations, seven comments). This does not substitute
for post-deployment delivery verification using the production installation token.
