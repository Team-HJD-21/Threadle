# New-connection operator email

Authenticated administrators can opt in to email notifications for repository connections created after they subscribe. The recipient is the email from the verified Cloudflare Access identity; the form cannot select another recipient. Subscription does not send a test message or backfill historical connections.

The administrator page can accept a subscription before SMTP is configured. It shows that delivery configuration is pending, and new events remain in the durable outbox. Existing GitHub/Discord notification delivery does not wait for SMTP.

## Trusted server configuration

Configure an approved SMTP provider on the receiver host, using these environment variables:

| Variable | Meaning |
| --- | --- |
| `THREADLE_SMTP_HOST` | Provider's SMTP hostname |
| `THREADLE_SMTP_PORT` | `465` for implicit TLS, or `587` for STARTTLS (default) |
| `THREADLE_SMTP_FROM` | Provider-authorized sender address, optionally with display name |
| `THREADLE_SMTP_USER` | SMTP username, if required |
| `THREADLE_SMTP_PASSWORD` | SMTP credential; required together with the username |

TLS certificates are verified. The admin page does not accept SMTP hosts or credentials. Keep credentials in the receiver host's protected credential configuration and expose them only to the service process. The feature does not select, register, or purchase an email provider automatically.

Messages contain the repository name/link and the connected Discord server name/ID. They do not contain repository source code, comments, or credentials.

## Google Workspace Gmail

Use `smtp.gmail.com` on port `465` (implicit TLS). The SMTP username and sender should be the authorized Workspace mailbox, such as `operator@example.com`. Authentication requires an app password rather than the account's ordinary sign-in password. App passwords require 2-Step Verification and may be restricted by the Workspace administrator or account protection settings. See [Google's app password guide](https://support.google.com/mail/answer/185833) and [Workspace application SMTP guidance](https://support.google.com/a/answer/176600).

The deployment helper stores the SMTP values using the receiver's existing machine-protected credential format:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\deploy\configure-admin-mail.ps1 -Sender 'operator@example.com'
```

Run this on the receiver host from the checkout containing the helper. It defaults to `C:\ProgramData\TeamHJD\GitHubDiscord`; use `-Root` if the receiver is elsewhere. Enter the app password only at the hidden prompt. The helper does not send a test email. After deployment/restarting the receiver, use the authenticated administrator page to opt in.

## Resend SMTP alternative

Resend requires a verified sending domain and an API key. Its SMTP hostname is `smtp.resend.com`, username is `resend`, and the API key is the SMTP password. Use port `465` for implicit TLS or `587` for STARTTLS with this implementation. Set the sender to an address on the verified domain. See [Resend's SMTP documentation](https://resend.com/docs/send-with-smtp).

The bundled helper above configures Google Workspace specifically; do not use it unchanged for Resend. Configure the same `THREADLE_SMTP_*` values through the receiver's protected server configuration instead. No provider is activated until its settings and credential are supplied.

## Delivery and retry behavior

New connections enqueue one message per enabled subscriber in the same registry transaction. Duplicate enqueue attempts for the same connection/subscriber do not create duplicate jobs. A separate worker sends messages, retries failures with a bounded delay, and stops after six attempts. A crashed in-flight job can be retried after its five-minute lease expires.

Unsubscribing cancels unsent jobs. A message already handed to SMTP may still finish sending. Re-subscribing does not reactivate canceled historical jobs.

Delivery rechecks that operator access is enabled and the subscribed recipient is still permitted by the current administrator email allowlist. Disabled access or a removed recipient cancels the pending job.

SMTP has no transaction shared with SQLite: if SMTP accepts a message and the process crashes before recording success, a retry may send it again. Retries retain the same deterministic `Message-ID`, but receiving mail systems are not guaranteed to deduplicate it. Delivery is at least once rather than exactly once.

Logs record only failure categories and attempt counts. SMTP responses, recipient addresses, and credentials are not written to logs. The administrator panel shows pending and terminal-failure counts for the authenticated recipient.
