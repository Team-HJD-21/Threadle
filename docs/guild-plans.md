# Server plans and effective settings

`guild_subscriptions` in the connection registry owns subscription metadata by Discord guild ID. Repository names, connection IDs and connection config never grant a plan. Existing servers have no subscription row: they display Free / no subscription with free beta access. No billing provider, checkout, payment claim, plan-edit button or paid restriction is enabled by this change.

`guild_plans.subscription` resolves an active/trialing Free or Pro plan, falling back to Free when expired, canceled or past due. Future billing integration must validate provider events and write this table idempotently. `guild_plans.allows` is the shared entitlement boundary. Register future feature requirements centrally before using it in both web and command/worker entry points. Unknown feature policies raise rather than silently grant access. Current notifications remain available on Free; no existing functionality is restricted.

The administrator sees the server plan on each connection card. Expand Current /settings values to see allowlisted effective language, time zone, PR reminders, batching, independent daily/weekly summaries and Do Not Disturb hours. Legacy digest mode and weekly-hour fallback are resolved without changing stored settings. Raw connection configuration and secrets are never rendered.

Manual web ZIP update UI and upload/apply/cancel/status routes have been removed. Old routes return 404 after administrator authentication. Existing command registration status remains visible. GitHub Actions automatic deployment is unchanged. The standalone update recovery library is retained for existing recovery data and offline operator recovery; the website cannot stage or launch it.

Site and administration release notes live at `/connect/admin/changelog` behind the same operator authentication. The public `/connect/changelog` contains bot functionality only, including rewritten mixed historical entries. Site-only versions appear only in the administrator history.
