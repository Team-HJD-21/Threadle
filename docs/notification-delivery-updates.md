# Notification delivery updates (beta.5)

Webhook fan-out already starts independent workers per binding. Root, detail and alert create-message nonces now include the destination channel/thread, avoiding Discord author-wide nonce collisions across servers. Historical receipt markers remain compatible. Different per-connection batching, quiet hours, backlogs and Discord rate limits may still affect delivery time; no exact simultaneous arrival guarantee is made. Production logs are needed to attribute a particular delay.

Mutable descriptions, conversation comments, inline comments, discussion comments and reviews retain source-to-message IDs in the binding queue database. Updates replace only messages mapped to that source and adjust its image pages. Stable receipt markers recover lost responses. Older revisions and superseded retries are ignored. Lifecycle notices remain separate. Legacy messages are adopted only when clearly attributable to a single source; ambiguous mixed messages/images are preserved and a tracked replacement is created. No bulk deletion or live messages are sent during validation.

PR synchronize events fetch their comparison once per delivery attempt and display up to 15 commit titles plus a comparison link. GitHub lookup failure keeps the comparison link without repeating PR body text. No periodic CI/CD polling is introduced.

Standard Markdown passes through. GitHub tasks become checkbox symbols, HTML details become visible text, and tables become labeled rows. Fenced and inline code remain literal, including image examples. Discord limits and client rendering apply.

Existing weekly schedules read the original digest_hour unless weekly_digest_hour is explicitly saved. New onboarding drafts default to Friday and 09:00. No existing binding is migrated. The weekly scheduler remains a current open-PR/Issue snapshot, not a historical activity report. /settings weekly_digest_hour changes the weekly time independently.

Admin repository totals count unique GitHub repository IDs; per-server binding records stay independent for permissions, channels, language and schedules. Applying team options routes to the confirmation screen for both initial and existing connections.
