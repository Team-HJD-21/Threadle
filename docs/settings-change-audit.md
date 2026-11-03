# Visible settings changes

An effective /settings mutation atomically records the interaction ID, binding/server/repository, Discord actor, time and allowlisted before/after settings in the registry transaction. Unchanged values and status reads do not mutate configuration or produce notices. Replaying the same interaction cannot overwrite a later settings change. Existing server/repository selection and Manage Server authorization are preserved.

A durable registry outbox sends the change to that binding's current github-alert. Actor mentions render without ping permissions. During Do Not Disturb, the notice is immediate and silent; normal API failures retry without undoing the configuration or blocking future commands indefinitely. AlertFeed receipts recover ambiguous responses and avoid duplicate notices. Paused connections wait; disconnected connections do not send a pending notice. Expiring connection data also deletes this audit/outbox data.

/settings-history (optional repository selection) shows the ten most recent settings-command changes for the selected binding, with separate ephemeral pages when needed. Legacy connection_audit rows already contain actor and UTC time and are included, explicitly marked as lacking before/after values. Earlier values cannot be reconstructed retroactively. This records successful settings changes, not every slash-command invocation. /settings without options remains the current-state view.

No credentials, interaction tokens, raw config, message bodies or repository content are copied to the audit. Website settings changes continue to use existing connection audit and are not announced by this command-specific feature.
