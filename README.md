# Threadle

GitHub notifications, organized into Discord threads.

This repository contains the existing Windows notebook service and the scoped media, preview and deployment changes for 2026.11.02.5. Production data and credentials stay on the notebook.

- [Deployment setup (Korean)](deploy/README.ko.md)
- [Exact changed files and baseline checksums](deploy/changes.json)
- [CI and deployment workflow](.github/workflows/threadle.yml)
- [Personal accounts, mentions, and comment forwarding](docs/accounts-and-comments.md)

PRs run regression checks on a GitHub-hosted Windows runner. After main is updated, deployment runs on the notebook only when the repository variable `THREADLE_CD_ENABLED=true` and the `threadle-production` runner is connected. Start with a manual preflight-only run.

The deployment checks installed file identities, backs up only listed files, applies changes, and verifies `/health` and `/connect`. Failed application or health checks restore the backed-up files. A passing health check does not prove live Discord notification delivery.

PR checks also compare the deployment manifest against the destination branch.
Every previously supported runtime checksum must remain accepted, including the
previous release's current checksum. Removing one fails CI before deployment.
When a file first enters the managed list, CI also reads its predecessor's Git
source: an existing application file cannot be treated as a brand-new absent file.
A captured beta.31 production manifest independently checks the upgrade path from
the last successful release before the beta.32 baseline-list fix, including its
previously unmanaged connection-authentication module. A separate checksum-only
production sample covers every managed Python entry, including absent new files.
Unknown server modifications still stop deployment before files are changed;
preflight reports all mismatches and installed hashes together.

## Local checks

Use Python 3.12. Install `requirements-app.txt` and Pillow, then run `python -B deploy/ci.py`.
To check an upgrade from a specific predecessor, also run
`python -B deploy/ci.py --checks-only --baseline-ref <commit-or-ref>`.
