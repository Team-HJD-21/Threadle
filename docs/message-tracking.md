# Hidden notification receipts

The queue-backed receiver removes generated tracking footer lines before sending a Discord message. Receipts, source/page revisions, and root identities are stored in the connection's queue database. Internal API reads reconstruct virtual footers so existing edit, deletion, ownership, and duplicate checks keep working. Older messages with visible receipts remain readable; their receipts disappear when the message is next updated. No bulk rewrite of historical messages is performed.

A durable pre-send record supports interrupted delivery recovery. Matching requires the bot author, normalized content and media, and a matching nonce when Discord returns one. Already-mapped messages cannot be reassigned by a different pending POST. Explicit PATCH recovery targets the existing message ID. Ambiguous candidates are never adopted.

When Discord omits a nonce, only recent messages with a source link can be recovered by content. An image-only continuation page without a nonce is deliberately not guessed. If a process crashes after posting such a page, a later retry outside Discord's nonce-deduplication window can produce a duplicate page. This preserves source ownership rather than risking an edit or deletion of a different comment.

The existing state backup includes the queue database and these tables. Loss of that database and all backups loses hidden receipt mappings. The legacy standalone CLI without a queue database retains its original visible-receipt behavior; the hosted receiver uses queue-backed tracking.
