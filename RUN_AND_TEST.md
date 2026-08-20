# Run and test the merged SNOC email agent

## 1. Prerequisites

- Docker Engine with Docker Compose v2
- At least 8 GB free RAM for the local containers, excluding hosted LLM requirements
- Internet access for the first Docker/npm/Python dependency installation
- Gmail app passwords when Gmail IMAP/SMTP is used

## 2. Extract and enter the project

```bash
unzip snoc_emails_agent-merged.zip
cd snoc_emails_agent-merged
```

## 3. Create local configuration

```bash
cp .env.example .env
chmod 600 .env
```

At minimum, review these entries in `.env`:

```dotenv
APP_ENVIRONMENT=development
SNOC_API_BIND_ADDRESS=0.0.0.0
DRY_RUN=true
DRY_RUN_SEND_EMAILS=false
IMAP_SEARCH_CRITERION=UNSEEN
USE_SVM_FALLBACK=false

IMAP_USERNAME=your.receiver@gmail.com
IMAP_PASSWORD=your_gmail_app_password
SMTP_USERNAME=your.receiver@gmail.com
SMTP_PASSWORD=your_gmail_app_password
SMTP_FROM_ADDRESS=your.receiver@gmail.com
SYSTEM_EMAIL_ADDRESS=your.receiver@gmail.com
AUTHORIZED_SENDERS=authorized.sender@example.com
ESCALATION_RECIPIENT=human.reviewer@example.com

DASHBOARD_ADMIN_USERNAME=snoc-admin
DASHBOARD_ADMIN_PASSWORD=replace_with_a_long_random_admin_password
AUTH_JWT_SECRET=replace_with_at_least_32_random_bytes

VLLM_API_KEY=replace_if_required
VLLM_QWEN_BASE_URL=https://qwen-2.majesteye.com/v1
VLLM_QWEN_MODEL=stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ
VLLM_GEMMA_BASE_URL=https://gemma-e4b.majesteye.com/v1
VLLM_GEMMA_MODEL=google/gemma-4-12B-it
VLLM_ANALYZER_DEPLOYMENT=qwen
VLLM_VERIFIER_DEPLOYMENT=gemma
```

Never commit `.env`.

`0.0.0.0` publishes the API on every host interface so an external reverse proxy can reach it. Restrict access with the host firewall and TLS reverse proxy. For local-only access, set `SNOC_API_BIND_ADDRESS=127.0.0.1` instead.

## 4. Clean previous processes safely

This preserves Docker volumes and database data:

```bash
pkill -f 'snoc_agent.run_api' 2>/dev/null || true
fuser -k 5173/tcp 2>/dev/null || true
docker compose down --remove-orphans
```

Do not add `-v` unless you intentionally want to delete PostgreSQL data, dashboard users, and retained raw messages.

## 5. Validate Compose configuration

```bash
docker compose config --quiet
docker compose config --services
```

Expected services include:

```text
postgres
migrate
worker
api
frontend
dashboard
journey
```

## 6. Start the integrated application

```bash
docker compose up -d --build postgres migrate api worker
```

Check state:

```bash
docker compose ps -a
```

Expected state:

- `postgres`: healthy/running
- `migrate`: exited with code 0
- `api`: healthy/running
- `worker`: running

Follow startup logs when needed:

```bash
docker compose logs --tail=150 postgres migrate api worker
```

## 7. Open the coded React dashboard

The Docker image compiles the React frontend into FastAPI. Open:

```text
http://127.0.0.1:8000
```

Local test login:

```text
Use DASHBOARD_ADMIN_USERNAME and DASHBOARD_ADMIN_PASSWORD from .env.
```

The black Streamlit audit UI is a different optional tool. It is not started by the command above.

## 8. Backend checks

```bash
curl -fsS http://127.0.0.1:8000/health/live | python -m json.tool
curl -i http://127.0.0.1:8000/health/ready
curl -fsS http://127.0.0.1:8000/openapi.json -o /tmp/snoc-openapi.json
```

Check container health:

```bash
docker compose exec -T postgres pg_isready
```

Show important table counts:

```bash
docker compose exec -T postgres \
  psql -U "${POSTGRES_USER:-snoc_langgraph}" \
       -d "${POSTGRES_DB:-snoc_langgraph}" \
       -c '
SELECT
  (SELECT COUNT(*) FROM email_messages) AS emails,
  (SELECT COUNT(*) FROM requests) AS requests,
  (SELECT COUNT(*) FROM operations) AS operations,
  (SELECT COUNT(*) FROM model_runs) AS model_runs,
  (SELECT COUNT(*) FROM executions) AS executions,
  (SELECT COUNT(*) FROM escalations) AS escalations;
'
```

## 9. Model routing checks

```bash
docker compose exec -T worker snoc-agent models list
docker compose exec -T worker snoc-agent models check
```

The exact model IDs returned by each `/v1/models` endpoint must match `.env`. Do not substitute a remembered model name.

Optional compact smoke test:

```bash
docker compose exec -T worker \
  snoc-agent models smoke-test \
  --analyzer-model qwen \
  --verifier-model gemma \
  --output-dir outputs/evaluation/vllm_smoke
```

## 10. Gmail end-to-end test

Watch the worker:

```bash
docker compose logs --since=10s -f worker
```

Send one new unread email from an address listed in `AUTHORIZED_SENDERS` to `IMAP_USERNAME`, for example:

```text
Subject: Password reset test

Please reset the password for PDV 12345678.
```

Expected high-level flow:

```text
Ingress -> Security -> NLU -> Policy -> Fulfilment
```

The worker should process the email once and then log that the IMAP message was marked as seen. With the safe defaults, the telecom mutation is simulated and no reply is sent because `DRY_RUN_SEND_EMAILS=false`.

To enable test replies while keeping telecom calls mocked:

```dotenv
DRY_RUN=true
DRY_RUN_SEND_EMAILS=true
```

After changing `.env`, recreate the worker and API:

```bash
docker compose up -d --force-recreate worker api
```

Check remaining unread UIDs:

```bash
docker compose exec -T worker python - <<'PY'
import imaplib
import os

client = imaplib.IMAP4_SSL(
    os.environ["IMAP_HOST"],
    int(os.environ.get("IMAP_PORT", "993")),
)
try:
    client.login(os.environ["IMAP_USERNAME"], os.environ["IMAP_PASSWORD"])
    client.select(os.environ.get("IMAP_MAILBOX", "INBOX"))
    status, data = client.uid("SEARCH", None, "UNSEEN")
    uids = data[0].split() if status == "OK" and data else []
    print("Unread count:", len(uids))
    print("Unread UIDs:", [uid.decode() for uid in uids])
finally:
    client.logout()
PY
```

## 11. React/Vite development mode

Docker's API serves the compiled React build on port 8000. For hot reload, keep the backend containers running and open another terminal:

```bash
cd frontend
npm ci
npm run dev -- --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173
```

Vite proxies `/api` and `/health` to FastAPI at `127.0.0.1:8000`.

Verify the proxy:

```bash
curl -fsS http://127.0.0.1:5173/health/live | python -m json.tool
```

## 12. Optional Streamlit audit dashboard

Start it only when needed:

```bash
docker compose --profile diagnostics up -d dashboard
```

Open:

```text
http://127.0.0.1:8502
```

Stop it without affecting the React app:

```bash
docker compose stop dashboard
```

## 13. Complete local quality gate

Use Python 3.12:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -c constraints-langchain.txt -e '.[dev,dashboard,postgres-checkpoint]'
```

Run backend checks:

```bash
python -m compileall -q src/snoc_agent
pytest -ra
ruff check .
ruff format --check .
mypy src/snoc_agent
```

Run frontend checks:

```bash
npm --prefix frontend ci
npm --prefix frontend run build
```

Run infrastructure checks:

```bash
docker compose config --quiet
docker compose up -d --build postgres migrate api worker
curl --retry 30 --retry-connrefused --retry-delay 1 \
  -fsS http://127.0.0.1:8000/health/live
curl -fsS http://127.0.0.1:8000/health/ready
```

## 14. Useful troubleshooting commands

```bash
docker compose ps -a
docker compose logs --since=10m api
docker compose logs --since=10m worker
docker compose logs --since=10m postgres
ss -ltnp | grep -E ':8000|:8502|:5173'
```

Find 4xx/5xx API responses:

```bash
docker compose logs --since=10m api | grep -E ' 4[0-9]{2} | 5[0-9]{2} |ERROR|Traceback'
```

## 15. Production transition checklist

Do not switch to production merely by changing one flag. Before setting `APP_ENVIRONMENT=production`:

1. Replace the local dashboard administrator password and JWT signing secret with unique secrets.
2. Use HTTPS for the dashboard and API.
3. Use a managed PostgreSQL password and secret store.
4. Restrict CORS to the production dashboard origin.
5. Set valid IMAP/SMTP app credentials and authorized senders.
6. Confirm exact vLLM model IDs and authentication.
7. Configure and test the real business API with idempotency guarantees.
8. Keep `DRY_RUN=true` until end-to-end acceptance passes.
9. Run all tests, frontend build, Docker health checks, and a controlled mailbox journey.
10. Review logs to ensure secrets and email contents are not emitted.
