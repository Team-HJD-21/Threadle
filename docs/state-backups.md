# Verified state backups

Threadle creates a local SQLite snapshot before applying a deployment and once per day during normal operation. The background worker first waits five minutes, then checks hourly. It uses SQLite online backup rather than copying live WAL files. Each database is consistent independently; the set is not a single cross-database transaction. Registry is copied before delivery queues.

Snapshots live under `database-backups` in the configured runtime directory. Every published snapshot passes SHA-256 and SQLite quick-check validation and a restoration into a temporary directory. Failed or interrupted attempts are not published. Backup failure before deployment leaves application files unchanged and restarts the previous receiver. The administrator page shows the latest successful backup/restore-check time.

## Scope and retention

Included: connection registry, channel layouts, repository-file cache, legacy queue, and per-connection delivery queues, thread/message links and pending work. OAuth/login sessions, credentials, keys and configuration files are excluded. Treat backups as private repository data and keep the runtime directory restricted to its existing operators.

Keep at most 14 snapshots, removing snapshots older than seven days when a new verified snapshot is created. After a disconnected connection reaches the existing deletion deadline, remove all managed snapshots containing that connection. Cleanup requests persist across errors and retry hourly. Manual folders and external copies are not managed by this feature.

Backups use the same local disk: they help recover state corruption or an unsuccessful change, but do not protect against loss of the machine or disk. Operators still need a separately protected external backup and the original configuration/encryption keys for disaster recovery.

## Read-only verification and isolated restore check

Run using the configured service Python executable and the deployed `state_backups.py`:

```powershell
python state_backups.py verify --snapshot 'C:\ProgramData\TeamHJD\GitHubDiscord\database-backups\<snapshot>'
python state_backups.py restore-check --snapshot 'C:\ProgramData\TeamHJD\GitHubDiscord\database-backups\<snapshot>' --target 'D:\Threadle-Recovery-Check-NEW'
```

The restore target must not exist. This command does not replace operating databases or change the running service. It validates the source before creating the target, copies only allowlisted database paths and validates restored files. Do not point it at the live runtime directory.

Actual recovery is a separate operator action: stop intake and delivery, retain the damaged state for investigation, select a verified snapshot compatible with the deployed application schema, review replay/duplicate implications, then replace the complete intended database set with the service stopped. Preserve required configuration/keys; users may need to sign in again because login sessions are excluded. No automatic live database rollback is performed.

## Validation

Tests cover live WAL state and pending-job/thread-link restoration, excluded sessions/secrets, corruption and path rejection, low disk/interruption, daily scheduling, retention cleanup and durable retries. Symlink rejection is tested where Windows permits creating symlinks. Deployment CI also imports the new runtime module in the isolated candidate.
