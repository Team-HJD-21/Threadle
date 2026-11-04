# State database and backup encryption

This release adds full-file SQLCipher 4 encryption to the connection registry, delivery queues, channel layouts, repository-file cache, login/session database and legacy admin-update database. Connection IDs, settings, pending jobs, retry counts and Discord thread/message associations remain unchanged. The encrypted runtime is exercised by the existing delivery and administrator regression suites.

The Windows Python 3.12 x64 SQLCipher package is isolated in `vendor-sqlcipher-0.6.4`. Deployment downloads the exact PyPI wheel listed in `deploy/cipher.json`, verifies its length and SHA-256, and performs an encrypted write/read/authentication check before stopping the receiver. Changing a package or using another architecture aborts the preflight.

## One-time conversion and compatibility

CD stops intake and delivery, creates the existing verified state backup, installs the candidate, and prepares encrypted copies offline. Each copy passes SQLite integrity and a comparison of schema, values, table row order, application ID and user version before any original database is replaced. Previously published, valid managed snapshots are also converted and their manifests refreshed. Unknown/manual backup folders and external copies are retained for operator inspection, not silently edited.

A durable conversion journal and startup guard prevent the receiver from accidentally creating an empty database during an interrupted file replacement. Preparation failures restore the originals. The health check runs against the converted databases. If health fails, rollback converts the **current** encrypted databases back for the previous application, preserving notifications received after conversion instead of reverting to a stale snapshot. A subsequent deployment to an already encrypted runtime retains its key and encrypted format.

Only after health passes does finalization remove the protected temporary original files. File deletion does not guarantee erasure of prior SSD blocks, old WAL copies, volume snapshots or external backups. Protect the disk and its historical backups separately.

The access layer can still open a legacy plaintext runtime before its first CD conversion. Once `state-encryption.json` marks a runtime encrypted, missing/unreadable/wrong keys or unexpected plaintext databases cause an error; it does not generate a new key or fall back to plaintext. No repository reconnect, bot reinvite or settings migration by a user is needed.

## Key and backup boundaries

A random 256-bit master key is protected by Windows machine-bound DPAPI in `.state-keys/master.dpapi`. The key directory has an explicit protected DACL limited to SYSTEM, local administrators and the deploying operator. Credentials, the DPAPI blob and recovery keys are not placed in managed database snapshots. The public policy file contains only a format, readiness marker and non-secret key identifier. SQLCipher protects database pages and WAL content; temporary SQLite data stays in memory.

DPAPI is machine-bound, not an independent disaster-recovery copy. Losing both the machine's DPAPI protection and a separately exported recovery key makes the databases unrecoverable. An operator with access to the running service or authorized key material can still read data. Discord messages/attachments, application logs, runtime configuration JSON, unknown/manual backups and copies held by external providers are outside this database-encryption scope. This is encryption at rest, not end-to-end encryption.

## Separately protected recovery key

After the encrypted deployment, export a recovery key to a protected location **outside the runtime directory and its database backups**, then keep it separately from the database backup. The tool prompts twice for a passphrase of at least 16 characters. It never prints or accepts the passphrase as a command-line argument. The file uses Scrypt and authenticated AES-GCM encryption, and is not overwritten if it already exists.

Using the configured service Python:

```powershell
python state_encryption.py export-recovery --root 'C:\ProgramData\TeamHJD\GitHubDiscord' --recovery-file 'D:\Separate-Key-Backup\threadle-recovery.json'
```

On a replacement machine, first install the compatible application/SQLCipher dependency and restore encrypted database files into a new runtime root. Keep the receiver stopped. Import the separately stored recovery key:

```powershell
python state_encryption.py import-recovery --root 'C:\ProgramData\TeamHJD\GitHubDiscord' --recovery-file 'D:\Separate-Key-Backup\threadle-recovery.json' --offline
```

The tool verifies the passphrase and the restored registry before writing a new machine-protected key. An existing protected key is not overwritten. Restore configuration/credentials through their separate recovery process; managed database snapshots intentionally exclude them and login sessions. Never send recovery files, passphrases or key blobs through chat or issue reports.

## Interrupted deployment recovery

Normal CD handles errors automatically. After machine/process loss during conversion, keep the receiver stopped and run the matching release's `state_encryption.py rollback --root <runtime> --job <original CD job> --offline` before restoring old application files. The job is recorded in `state-conversion-active.json`. Do not delete the guard, journal, key or protected conversion directory to bypass an error. A completed deployment's originals have been removed; normal data restore remains a separate operator decision.

Reference: [SQLCipher design](https://www.zetetic.net/sqlcipher/design/) and [export/migration API](https://www.zetetic.net/sqlcipher/sqlcipher-api/).
