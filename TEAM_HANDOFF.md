# Team handoff

## What belongs in Git

Commit the application, tests, migrations, Docker files, documentation, `.env.example`, and the
two `.gitkeep` files. Do not commit `.env`, database files, virtual environments, raw MIME,
acceptance outputs, caches, credentials, or model-provider tokens. The supplied `.gitignore` and
`.dockerignore` exclude those files.

The GitHub repository should use the contents of `snoc-langgraph` as its root. Do not copy this
directory's generated `outputs/` or `var/` data into release archives.

## First setup

Requirements are Python 3.12, Docker Engine, and Docker Compose v2.

```bash
cp .env.example .env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -c constraints-langchain.txt -e ".[dev,dashboard]"
python -m ruff check src tests scripts/docker_mail_journey.py
python -m mypy --no-incremental src/snoc_agent
python -m pytest -ra
docker compose config --quiet
docker compose up --build -d postgres migrate api worker
```

The Compose stack defaults to production mode, LangGraph, live execution, and one IMAP-worker
replica; a PostgreSQL advisory lock rejects accidental duplicate pollers. It refuses SQLite,
dry-run/test flags, test-scoped IMAP filters, or missing live credentials in production. Set
`SNOC_COMPOSE_PROJECT_NAME`, `SNOC_DASHBOARD_PORT`, and `SNOC_POSTGRES_VOLUME` to unique values
when multiple checkouts run on the same Docker host.
The API binds to `127.0.0.1` by default; set `SNOC_API_BIND_ADDRESS` only for an approved ingress.

## Secrets and environment values to provide

Share secrets through the team's approved secret manager, not Git, email attachments, pull request
comments, or issue text. Each teammate should create their own untracked `.env`. For CI/CD, map the
same names from GitHub Actions environments or repository secrets.

For offline tests, no credentials are required. For the real six-journey mailbox acceptance, the
operator needs:

- Agent mailbox: `IMAP_HOST`, `IMAP_USERNAME`, `IMAP_PASSWORD`, `SMTP_HOST`, `SMTP_USERNAME`,
  `SMTP_PASSWORD`, and `SMTP_FROM_ADDRESS`, plus the matching TLS/port settings.
- Test sender mailbox: `SENDER_USERNAME`, `SENDER_PASSWORD`, and `SENDER_IMAP_MAILBOX`.
- Routing: `AUTHORIZED_SENDERS` containing only the test sender and a controlled
  `ESCALATION_RECIPIENT`.
- vLLM: `VLLM_API_KEY`, both `VLLM_*_BASE_URL` values, the exact served model IDs, and analyzer /
  verifier deployment selectors.
- Safety: `DRY_RUN=true`, `DRY_RUN_SEND_EMAILS=true` for the real-mail acceptance only, and a
  test-scoped `IMAP_SEARCH_CRITERION` containing `X-SNOC-Test-Run`.

Use provider-specific application passwords if normal mailbox passwords are not accepted. Keep
`BUSINESS_API_BASE_URL` and `BUSINESS_API_TOKEN` empty for this validation; real telecom execution
is out of scope.

To keep the secret file outside the checkout:

```bash
export SNOC_ENV_FILE=/absolute/path/to/snoc-langgraph.env
docker compose config --quiet
```

## Real-mail acceptance

The acceptance runner performs static checks and tests, validates migrations twice, probes both
vLLM deployments, runs all six journeys, writes evidence under `outputs/<timestamp>/`, and leaves
the isolated database volume for inspection:

```bash
scripts/run_real_acceptance.sh
```

If a separately checked-out legacy worker could consume the same mailbox, pass its checkout or
Compose file so the runner stops and restarts only that worker:

```bash
SNOC_LEGACY_REPO=/absolute/path/to/legacy-dsip scripts/run_real_acceptance.sh
# or:
SNOC_LEGACY_COMPOSE_FILE=/absolute/path/to/docker-compose.yml \
  scripts/run_real_acceptance.sh
```

## Before opening the pull request

From a fresh clone, repeat the offline checks above and run:

```bash
git status --short
git ls-files | grep -E '(^|/)(\.env|outputs/.+|var/.+|.*\.(db|sqlite3?))$' && exit 1 || true
docker compose build
```

Review the staged diff and repository visibility before pushing. The example configuration uses
placeholder hostnames and values; real infrastructure addresses should remain environment-managed
unless the team has explicitly approved documenting them.
