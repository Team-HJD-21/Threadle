# Code and file review conversations

Subscribe the GitHub App to `pull_request_review_thread`, alongside the existing
review and review-comment events. Existing Pull requests read permission is enough;
users do not need to invite the Discord bot again.

The overall submitted review remains its own Commented/Approved/Changes requested
message and displays its Review ID. Each code/file conversation has one mutable
card identifying that same parent review with a GitHub link, file/line, and
Unresolved (red) or Resolved (green). Replies append author, local Discord timestamp
and original Markdown to that card. Edits and deletions refresh the conversation.
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
card has been delivered. No global historical backfill runs on startup.

Verification includes fake Discord delivery/edit/delete/retry tests and a read-only
query against PR #616 (four conversations, seven comments). This does not substitute
for post-deployment delivery verification using the production installation token.
