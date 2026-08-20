# Project Split Manifest

## Ownership

This project owns its complete runtime under this directory:

- `src/snoc_agent`: application and five-agent graph
- `alembic`: independent schema history, including graph audit
- `scripts/docker_mail_journey.py`: real-mail acceptance and audit export
- `dashboard.py`: read-only domain and agent-trace audit
- `Dockerfile` and `compose.yaml`: isolated image, database, worker, dashboard, and journey
- `tests`: copied baseline plus graph-audit acceptance tests

The Docker build context is this directory. No root source directory is mounted or imported.

## Runtime configuration

The project reads a project-local, untracked `.env` by default. Set `SNOC_ENV_FILE` to an absolute
path to use an externally managed secret file instead. No source code or runtime data is shared
with another checkout. Compose overrides workflow engine, database URL, dry-run flags, IMAP
criterion, and raw-mail path.

## Optional legacy coordination

The standalone repository does not contain the legacy application. When the project remains inside
the original DSIP checkout, the acceptance runner auto-detects that checkout, verifies its
pre-migration snapshot, and temporarily stops/restarts its worker. In another layout, set
`SNOC_LEGACY_REPO` or `SNOC_LEGACY_COMPOSE_FILE` only when that coordination is required.
