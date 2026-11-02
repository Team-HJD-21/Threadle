# Threadle

GitHub notifications, organized into Discord threads.

This repository contains the existing Windows notebook service and the scoped media, preview and deployment changes for 2026.11.02.5. Production data and credentials stay on the notebook.

- [Deployment setup (Korean)](deploy/README.ko.md)
- [Exact changed files and baseline checksums](deploy/changes.json)
- [CI and deployment workflow](.github/workflows/threadle.yml)

PRs run regression checks on a GitHub-hosted Windows runner. After main is updated, deployment runs on the notebook only when the repository variable `THREADLE_CD_ENABLED=true` and the `threadle-production` runner is connected. Start with a manual preflight-only run.

The deployment checks installed file identities, backs up only listed files, applies changes, and verifies `/health` and `/connect`. Failed application or health checks restore the backed-up files. A passing health check does not prove live Discord notification delivery.

## Local checks

Use Python 3.12. Install `requirements-app.txt` and Pillow, then run `python -B deploy/ci.py`.
