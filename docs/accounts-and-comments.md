# Personal accounts and bidirectional comments

Members run `/account connect` in a connected Discord server, follow its private
five-minute link, verify the same Discord account, choose features, then authorize
GitHub. `/account status` is private. `/account disconnect` removes the personal
connection in **all** servers and cancels pending comments.

Opt-in is per Discord server. Mentions are selected by default; forwarding is
**off** until the member explicitly selects it. The OAuth flow reuses the existing
Discord and GitHub `/connect/{provider}/callback` URLs. It uses browser-bound,
single-use state and GitHub PKCE; it never accepts an identity supplied in a URL.
Repository setup remains restricted to server administrators.

## Operator activation

1. In the Threadle **GitHub App registration**, set repository permissions:
   - Issues: **Read and write**
   - Pull requests: **Read and write**
   - Discussions: **Read and write**
   Keep the existing Metadata/Contents/Actions read permissions and existing
   webhook subscriptions. Do not add Contents write or Organization administration.
2. Installation owners/organization administrators must approve the additional
   repository permissions for existing installations. Updating the registration
   alone does not grant them to every installation.
3. Every participating member authorizes their **own** account. The bot owner
   cannot authorize impersonation of other members. Effective GitHub access is
   the intersection of that user's access and the App installation's access.
4. In Discord Developer Portal → Application → Bot → Privileged Gateway Intents,
   enable **Message Content Intent**. Restart the receiver after changing it.
   The gateway checks the application's approved/limited content-intent flags
   before requesting it. Without the intent, ordinary notifications and account
   linking still work, but ordinary Discord messages are not forwarded. Verified
   applications may need Discord's intent approval.
5. Let each member run `/account connect` and select forwarding. Bot reinvitation
   is unnecessary if the existing bot and `applications.commands` scopes and
   channel permissions remain present. Command synchronization publishes
   `/account` on startup automatically. The bot needs View Channel, Read Message
   History, Send Messages, Send Messages in Threads, and Add Reactions for receipts.
   Give its existing role **Manage Messages** to remove a review-reply input after
   the review card reflects it. Without this permission the input remains and a
   quiet warning explains why; notification delivery still works. Reinviting the
   bot is not required to change its existing role permissions.

GitHub `github_app_authorization` revocation webhooks are signature-verified and
cancel personal credentials; no new periodic repository scan is introduced.

## What is forwarded

- Human **new** messages in a uniquely mapped, active, Threadle-managed public
  PR, Issue, or Discussion thread, from an opted-in member of that server.
- Ordinary PR messages are conversation comments, not new code-line reviews.
- Discord **Reply** to a Threadle notification or an already-forwarded comment
  quotes its GitHub original. Discord-only messages are not copied to GitHub.
- Reply to a code/file review card instead posts in that exact GitHub review
  conversation. Replies to continuation cards use the same conversation root.
  The webhook updates the existing card before the unchanged Discord input is
  deleted. Failed or delayed updates preserve the input; edited inputs are kept.
- Repository, thread parent, message ownership, current Discord membership and
  channel access are checked again before a write. GitHub checks the member's
  current authority with their user token; no installation-token fallback exists.
- Bots, webhooks, DMs, unrelated channels, unknown threads, locked threads, and
  unlinked members are excluded. Existing Discord history is not imported.
- Image/GIF uploads use inline image Markdown; other attachments remain links.
  The current Discord message refreshes signed CDN URLs for the original file
  paths, without importing newly added attachments. CDN links can still expire;
  Threadle does not upload these files to GitHub or promise permanent hosting.
- GIF-picker Tenor/Giphy links use the actual GIF from Discord's preview metadata
  or a bounded, unauthenticated read of the public provider page. The GIF remains
  in its original text position. Missing/blocked previews retain the source link;
  MP4/PNG addresses are never guessed into GIF addresses. Code spans are unchanged.
  Provider redirects are limited to the provider allowlist. GitHub credentials
  are never sent to Discord or GIF providers. Later Discord edits are not
  synchronized to GitHub. Deletion of a forwarded original is handled below.
- A ✅ reaction confirms a GitHub write. A ⚠️ reaction and quiet reply explain a
  failure; after an ambiguous result, check GitHub before manually reposting.

GitHub `@login` mentions resolve only for linked, opted-in people who currently
belong to the destination server and can view that channel. Missing members and
unlinked users keep their original GitHub text. Code, URLs, emails, teams, HTML
comments, and escaped mentions are left unchanged. Linked names appear only
inside the notification card: Threadle does not append an outside mention line
or request a real mention notification. This applies to new messages, edits and
Do Not Disturb alike. Explicit `allowed_mentions` disables users, roles,
everyone and reply pings. Ordinary channel notification preferences still apply.

In the other direction, native Discord user mentions (`<@id>`) become the linked
person's current GitHub `@login`. Stable account IDs and current server membership
are checked; this works for members who opted into mentions or comment forwarding
in that server. Code snippets and plain typed nicknames are not guessed. Unlinked
mentions remain non-pinging text. Discord's native mention picker is unchanged.

## Encryption, retries, and echo prevention

Personal access/refresh tokens and pending comment bodies are Fernet-encrypted
using the existing protected `CONNECT_SESSION_KEY`, inside `connections.sqlite3`.
That database is already covered by SQLCipher and managed encrypted backups.
Do not rotate the session key without migrating these credentials. Recovery
requires both the database encryption key and the original session-key credential.
Token expiry is refreshed serially; a crash or failed refresh requires reconnect
rather than reusing a potentially consumed refresh token.

Each outgoing comment has a durable operation ID **before** the network request
and an authenticated HTML comment receipt, invisible in GitHub's rendered view.
An echo must match a recorded operation, GitHub author ID, repository ID, item
number/type, and the resulting comment ID. It is suppressed only in the originating
Discord connection; another connected server still receives it normally. No
visible tracking code is added to Discord. Other people's replies are forwarded.
Review replies are an exception to suppressing the webhook: their authenticated
receipt refreshes the aggregate card in the originating connection, without an
extra standalone reply. Only that connection's successful card update authorizes
input cleanup. Cleanup expires after 24 hours and stops after account disconnect.

Comment writes have no automatic HTTP POST retry. On a lost response or process
restart, the worker searches for its signed receipt. If the result stays unknown,
it asks for manual review rather than risk posting twice. Restored pre-send rows
are also reconciled before a write. This favors avoiding duplicates over blind
at-least-once delivery; it is not a claim of exactly-once external API execution.

Bodies are removed on completion/cancellation; pending work expires after 24h.
Receipts and terminal records expire after 30 days. Disconnecting removes active
tokens immediately; historical backup copies follow the existing retention period.

## Deletion and activity records

Deleting an original Discord input forwarded by Threadle requests deletion of
that exact GitHub comment, including ordinary PR/Issue comments, Discussion
comments, and review replies. A server moderator's deletion has the same effect.
This requires the retained receipt (30 days), the author's active account consent,
and current GitHub access. It never deletes an unrelated GitHub comment or the
review's first comment. Removing a bot notification card is not a deletion request
for its GitHub source. Discord edits still do not change GitHub comments.

Threadle records its own deletion intent **before** automatic input cleanup so
the resulting Discord event cannot delete the newly posted GitHub review reply.
The receipt and deletion queue survive receiver restarts. An uncertain write is
looked up before retrying; deletion never reposts a comment. Already-missing
comments are considered complete only after repository/topic access is verified.
Failures are recorded in the `comment-deletions` / `chat-events` runtime logs.

Gateway events, not a history polling loop, record message creation/edit/deletion,
bulk deletions, thread changes, and reactions in managed channels/threads only.
IDs, event type, source and outcome are stored in the encrypted registry database
for 30 days; message bodies, attachments, titles and credentials are excluded.
Corresponding metadata is also written to the existing rotating receiver log.
Discord events during a non-resumable offline gap cannot be reconstructed without
querying history; no new periodic history scan is introduced.

Discord message-deletion events do **not** identify the deleting actor. The author
is distinct from the actor and is never used as a substitute. With the bot's
existing **View Audit Log** permission, the moderation gateway intent also saves
audit entries' actor, target, channel and count. Without permission, normal
deletion synchronization still works. Audit message-deletion entries can aggregate
multiple messages and have no exact message ID: they remain separate evidence,
not a guessed attribution to one message. No new bot invitation is required.

References: [GitHub user tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app),
[issue/PR comments](https://docs.github.com/en/rest/issues/comments#create-an-issue-comment),
[review replies](https://docs.github.com/en/rest/pulls/comments#create-a-reply-for-a-review-comment),
[Discord message deletion](https://docs.discord.com/developers/resources/message#delete-message),
[Discussion mutations](https://docs.github.com/en/graphql/reference/discussions#adddiscussioncomment),
[Discord intents](https://docs.discord.com/developers/events/gateway#privileged-intents).
