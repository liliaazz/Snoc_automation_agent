# SNOC AI Agent — Dashboard + LangGraph Email Agent

This repository is the self-contained next-generation project. It retains the tested
`snoc_agent` Python namespace while isolating its package, migrations, Docker image, PostgreSQL
volume, dashboard, journey reports, and graph audit from the legacy application.

The container stack defaults to `APP_ENVIRONMENT=production`, `WORKFLOW_ENGINE=langgraph`, and live
execution. Copy the deployment template, replace every placeholder, then start the default stack:

```bash
cp .env.example .env
# Edit .env with secret-manager values before continuing.
docker compose up --build -d postgres migrate api worker
```

Compose declares exactly one IMAP worker, and the worker also holds a PostgreSQL advisory lock for
its entire lifetime. A second worker therefore exits before polling, even if a platform scaling
override bypasses the declared replica count.

The guarded real-mail acceptance suite remains explicitly isolated in development mode:

```bash
APP_ENVIRONMENT=development scripts/run_real_acceptance.sh
```

The acceptance command validates code and configuration, runs all six real IMAP/SMTP journeys with
configured vLLM inference, saves timestamped reports/logs, and stops the test worker through an exit
trap. If the original DSIP checkout is detected, it also safely stops and restarts that legacy

See [TEAM_HANDOFF.md](TEAM_HANDOFF.md) before the first push or teammate setup. In particular,
never commit `.env`, raw mail under `var/`, or acceptance reports under `outputs/`.

---

## Preserved implementation notes

This repository now contains a first production-style, safety-first service for telecom support
emails. It stores raw MIME before model work, reconstructs conversations from RFC headers,
separates conversations from business requests and operations, asks structured clarification
questions, independently verifies model proposals, enforces hard invariants, and executes only
through an idempotent business-API adapter.

The historical notebooks, datasets, reports, and standalone scripts remain intact as experimental
and evaluation artifacts. Their production-safe pieces were extracted into `src/snoc_agent`.

## Architecture

```mermaid
flowchart LR
    IMAP[IMAP UID fetch] --> Raw[(email_messages)]
    Raw --> Parse[MIME parser and reply candidates]
    Parse --> Corr[conversation/request correlation]
    Corr --> Context[bounded labelled context]
    Context --> Analyzer[configured analyzer]
    Analyzer --> Verifier[independent verifier]
    Verifier --> Policy[hybrid safety policy]
    Policy -->|missing fields| Clarify[(clarification + outbox)]
    Policy -->|unsafe/ambiguous| Escalate[(structured escalation)]
    Policy -->|safe| Execute[idempotent API adapter]
    Execute --> State[(operation/request state)]
    State --> Summary[(completion outbox)]
```

Models propose and semantically verify operations. They never authorize senders, mutate state,
select endpoints, or call business APIs. The application owns those decisions.

### Feature-flagged LangGraph workflow

The original imperative processor remains the default:

```dotenv
WORKFLOW_ENGINE=legacy
```

Set `WORKFLOW_ENGINE=langgraph` to use the five-node
`Ingress → Security → NLU → Policy → Fulfilment` graph. The graph uses LangChain Runnable
boundaries for the existing audited analyzer and verifier, while authorization, policy and
side-effect code remain deterministic. `Runtime.legacy_processor` remains available for immediate
rollback.

The preserved pre-migration source remains in the original DSIP checkout and is intentionally not
part of this standalone repository. See
[migration status](docs/langgraph_migration_status.md) for the implemented boundary and known next
steps.

## Quick start

Python 3.12 is required.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
```

For the exact LangChain/LangGraph versions used by the parity suite:

```bash
pip install -c constraints-langchain.txt -e ".[dev]"
```

SQLite is the default. The command above creates `snoc_agent.db`; set `DATABASE_URL` to a
PostgreSQL SQLAlchemy URL for deployment.

Run the complete offline workflow without credentials, a GPU, or network access:

```bash
python -m snoc_agent.cli.main replay-email \
  tests/fixtures/emails/scenario_a_complete_unblock/01_complete_unblock.eml

python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_c_multi_operation/
```

With `LLM_PROVIDER=demo`, replay uses the explicitly labelled deterministic demo backend. If
`LLM_PROVIDER` is omitted, an empty `LLM_BASE_URL` preserves that legacy default. Demo inference
is only a workflow simulator. `DRY_RUN=true` uses the mock business API and fake SMTP transport.

The clarification sequence can also be replayed across two commands while preserving the SQLite
state:

```bash
python -m snoc_agent.cli.main replay-email \
  tests/fixtures/emails/scenario_b_otp_clarification/01_incomplete_otp.eml
python -m snoc_agent.cli.main replay-email \
  tests/fixtures/emails/scenario_b_otp_clarification/03_reply_phone_only.eml
```

Replay mode is forcibly dry-run and authorizes only the fixture senders it is currently injecting.
Normal IMAP workers still require `AUTHORIZED_SENDERS`.

Scenarios G–I have self-seeding `scenario.json` manifests. They use a fresh in-memory database,
bind generated request/outbound identifiers into later messages, and require no manual SQL:

```bash
python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_g_mixed_reply
python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_h_corrections --scenario before-execution
python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_h_corrections --scenario after-execution
python -m snoc_agent.cli.main replay-directory \
  tests/fixtures/emails/scenario_i_correlation_markers
```

## Local model configuration

The real backend targets a local OpenAI-compatible `/chat/completions` API and is independent of
the serving implementation. For example:

```dotenv
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=http://127.0.0.1:8000/v1
LLM_API_KEY=
ANALYZER_MODEL=Qwen2.5-7B-Instruct
VERIFIER_MODEL=Qwen3-8B
ANALYZER_TEMPERATURE=0
VERIFIER_TEMPERATURE=0
QWEN3_ENABLE_THINKING=false
QWEN3_SEND_THINKING_PARAMETER=true
LLM_JSON_SCHEMA_MODE=true
# Configure only after offline calibration; unset means no raw-confidence gate.
# ANALYZER_MIN_RAW_CONFIDENCE=0.85
# VERIFIER_MIN_RAW_CONFIDENCE=0.85
```

For Qwen3, `QWEN3_ENABLE_THINKING` is sent as a chat-template option when supported by the server.
Disable `QWEN3_SEND_THINKING_PARAMETER` for servers that reject that extension. Set
`LLM_JSON_SCHEMA_MODE=false` for servers that support JSON-object mode but not OpenAI JSON Schema.
Every run stores backend/model names, quantization label, prompt version, bounded context hash,
raw and parsed output, latency, token counts, and optional log probabilities.

No model weights are bundled or downloaded automatically. Real local-model checks are marked
`local_model` and remain optional.

## Primary vLLM deployments

`LLM_PROVIDER=vllm` is the primary hosted-model workflow. It reuses the audited
OpenAI-compatible client, but routes the two roles to independently discovered deployments. The
application requires the exact IDs returned by each `/v1/models` endpoint and never substitutes a
different model:

```dotenv
LLM_PROVIDER=vllm
VLLM_API_KEY=replace_me
VLLM_QWEN_BASE_URL=https://qwen.example.com/v1
VLLM_QWEN_MODEL=Qwen/Qwen2.5-7B-Instruct-AWQ
VLLM_GEMMA_BASE_URL=https://gemma.example.com/v1
VLLM_GEMMA_MODEL=google/gemma-4-12B-it
VLLM_QWEN3_30B_BASE_URL=https://qwen3-30b.example.com/v1
VLLM_QWEN3_30B_MODEL=stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ
VLLM_ANALYZER_DEPLOYMENT=gemma
VLLM_VERIFIER_DEPLOYMENT=gemma
DRY_RUN=true
```

The deployment selectors accept `qwen`, `gemma`, `qwen3_30b`, or the exact configured model ID. Qwen and
Gemma and Qwen3 30B use separate base URLs and backend instances. No provider routing suffix or
pricing policy is applied. Tokens, latency, schema mode,
reasoning, retries, endpoint alias, and exact served model are audited; USD cost is recorded as
unknown because these endpoints publish no pricing metadata.

```bash
python -m snoc_agent.cli.main models list
python -m snoc_agent.cli.main models check
python -m snoc_agent.cli.main models smoke-test \
  --analyzer-model gemma \
  --verifier-model gemma \
  --output-dir outputs/evaluation/vllm_smoke
```

`models check` probes `/health`, checks both exact served IDs, sends a minimal chat completion, and
validates a strict JSON-Schema response. The compact smoke command uses ten synthetic French
cases and creates no mail or business adapter. Live compatibility testing in this repository found
that the bounded Qwen analyzer response could exhaust its output limit on one password-reset case.
In the 10-case four-pair matrix, both Gemma-analyzer pairs had zero unsafe decisions and the
deterministic selection rule chose Gemma/Gemma; it is therefore the active default. This is compact
suite evidence, not a production-quality or release claim. See [the vLLM guide](docs/vllm_inference.md).

## PostgreSQL and workers

Start the development database if Docker is available:

```bash
docker compose up -d postgres
export DATABASE_URL=postgresql+psycopg://snoc_agent:local-development-only@localhost:5432/snoc_agent
alembic upgrade head
```

The base package includes Psycopg 3 with its binary distribution so the documented PostgreSQL URL
works after installation. Deployment images may replace that extra with a locally compiled
Psycopg build if required by their packaging policy.

Worker commands are synchronous and independently callable:

```bash
python -m snoc_agent.cli.main db init
python -m snoc_agent.cli.main mail poll --once
python -m snoc_agent.cli.main outbox send --once
python -m snoc_agent.cli.main processing retry-failed
python -m snoc_agent.cli.main worker run
```

### Docker black-box mailbox journey and audit dashboard

The Docker stack can exercise the application as an external user while keeping telecom actions
simulated. Configure two distinct mailboxes in `.env`: `IMAP_USERNAME`/`SMTP_USERNAME` are the
agent mailbox, while `SENDER_USERNAME`/`SENDER_PASSWORD` are the personal test mailbox. When
`AUTHORIZED_SENDERS` contains exactly that one personal address, `SENDER_USERNAME` may be omitted.
Keep `DRY_RUN=true` so the business adapter remains `MockBusinessAPI`, and opt into real test mail
with `DRY_RUN_SEND_EMAILS=true`.

For an isolated run, set this exact criterion so the worker ignores unrelated inbox traffic:

```dotenv
IMAP_SEARCH_CRITERION=HEADER X-SNOC-Test-Run "docker-e2e"
```

Start the acceptance services and the localhost-only Streamlit diagnostic dashboard:

```bash
APP_ENVIRONMENT=development docker compose --profile diagnostics up --build -d postgres worker dashboard
```

Open <http://localhost:8502>. The dashboard lists treated inbound emails and exposes IMAP
metadata, the RFC message chain, Gmail thread IDs, analyzer/verifier inputs and outputs, schema
modes, policy decisions, request/operation state, dry-run executions, and outbound delivery. It
also renders the latest journey report from the read-only `outputs/` mount. It reads PostgreSQL and
artifacts without mutating workflow state.

Run the compact external-user journey:

```bash
docker compose --profile test run --rm journey
```

The driver sends real, naturally titled emails from the personal mailbox to the agent, waits for
the SMTP reply, answers incomplete requests in the same RFC thread, and waits for a terminal
state. Run/case identifiers exist only in `X-SNOC-Test-*` headers. Its six journeys cover OTP
clarification, account unblock, VPN, quoted-history password reset, ambiguous multi-operation
attribution, and automated out-of-office metadata. On Gmail it fetches `X-GM-THRID` from the
sender's All Mail mailbox and requires every message in a journey to share one thread. The report
is written to `outputs/docker_mail_journey/report.json`.

With `DRY_RUN=true`, analyzer/verifier calls, decisions, state changes, validation records, and
dry-run execution records still happen. `DRY_RUN_SEND_EMAILS=true` additionally uses real SMTP for
clarification and terminal replies. Only the telecom business mutation is replaced by
`MockBusinessAPI`; every recorded execution must have `dry_run=true`.

Inspection commands:

```bash
python -m snoc_agent.cli.main request show SNOC-REQ-A84F91C274D2
python -m snoc_agent.cli.main conversation show UUID
python -m snoc_agent.cli.main operation show UUID
python -m snoc_agent.cli.main failures list
python -m snoc_agent.cli.main quarantine list
python -m snoc_agent.cli.main quarantine retry EMAIL_UUID
```

## Offline evaluation

The evaluation path supports legacy CSVs and attributed multi-operation JSON/JSONL datasets:

```bash
python -m snoc_agent.cli.main evaluate \
  --dataset "labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv" \
  --analyzer-model Qwen2.5-7B-Instruct \
  --verifier-model Qwen3-8B \
  --output-dir outputs/evaluation/qwen25_qwen3
```

Or run the complete seven-pair vLLM matrix and safety-first comparison:

```bash
python -m snoc_agent.cli.main evaluate \
  --dataset "labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv" \
  --matrix \
  --use-cache \
  --resume \
  --budget-usd 20 \
  --output-dir outputs/evaluation/vllm_matrix
```

Both the explicit single-pair command and `--matrix` use the same persistent evaluation runner and
write predictions, summary JSON, confusion data, categorized errors, model configuration,
checkpoints, cost/token counts, and Markdown metrics. Matrix mode runs each analyzer once per input
and each verifier once per unique proposal/context, then materializes all configured policies.
`--use-cache` reads/writes successful persistent results, `--no-cache` bypasses the global cache,
`--refresh-cache` replaces cache pointers while retaining old model-run audits, and `--resume`
reuses completed calls from an interrupted matching run. Cache, resume, budget, stop-threshold,
checkpoint, and confirmation flags apply to both forms.

If `EVALUATION_REQUIRE_BUDGET_CONFIRMATION=true`, the evaluation command refuses to start until
`--confirm-budget` is supplied after the operator reviews the configured limit. A clean budget
stop writes a resumable command instead of creating a fake failed model run.

Build explicit smoke, safety, oracle-diagnostic, stateful, development, calibration, and held-out
subsets before the safety run:

```bash
python -m snoc_agent.cli.main evaluation datasets build \
  --source "labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv" \
  --output-dir outputs/evaluation

python -m snoc_agent.cli.main evaluate \
  --dataset outputs/evaluation/safety_regression.jsonl \
  --matrix --use-cache --resume --budget-usd 2 \
  --output-dir outputs/evaluation/vllm_safety_smoke
```

The historical full deterministic-demo run produced 194 unsafe candidate rows. Those rows came
from heuristic `DemoLLMBackend` output, not Qwen. The dataset builder reproduces their IDs and
source hash in `demo_unsafe_candidates_manifest.json`, labels them explicitly, and adds them to the
safety regression subset. It also writes an initially empty
`demo_vs_real_regression_report.json`; a completed matrix writes `failure_attribution.json` with
separate analyzer, verifier, policy, and ground-truth-review categories. Real categories remain
unpopulated until genuine provider calls are run.

Optional confidence calibration supports `none`, `logistic`, and `isotonic` and accepts only rows
mapped to the calibration split:

```bash
python -m snoc_agent.cli.main evaluation calibrate \
  --predictions outputs/evaluation/vllm_matrix/qwen25_qwen3/predictions.csv \
  --method isotonic \
  --split-manifest outputs/evaluation/split_manifest.json \
  --output outputs/evaluation/calibration_isotonic.json
```

Raw confidence is never presented as a correctness probability without such an artifact. A release
recommendation is emitted only when both `unsafe_auto_execute_count == 0` and
`validation_pass_but_wrong_count == 0`. Oracle rescue analysis remains evaluation-only.

## Tests and quality

```bash
pytest
ruff check .
ruff format --check .
mypy src/snoc_agent
```

The default test suite uses SQLite, fake IMAP/SMTP, deterministic model responses, and the mock
business API. It requires no internet, credentials, local models, or GPU.

Opt-in live tests use only synthetic content and are skipped unless explicitly enabled:

```dotenv
# .env in the project root

For the independently hosted vLLM deployments:

```dotenv
RUN_VLLM_LIVE_TESTS=true
```

```bash
pytest -m vllm_live
```

The live-test module loads the project-root `.env` through `Settings`; real environment
variables override it. The CLI-only `--env-file` option does not apply to pytest. All unmarked
tests force the demo provider, so a developer `.env` cannot turn the normal suite into paid
inference. The live test requires usable route pricing before inference and disables unknown-cost
calls; missing pricing fails before credits are spent.

## Safety defaults

- `DRY_RUN=true` is the development/library default; the Compose production template explicitly
  sets `APP_ENVIRONMENT=production` and `DRY_RUN=false`.
- Live execution additionally requires a non-demo LLM provider with its required credentials or
  endpoint, plus configured business API and SMTP endpoints.
- An empty sender whitelist denies normal inbound senders.
- Raw MIME is stored before MIME parsing or model processing and remains retryable.
- Parse-fatal and raw-size failures are quarantined and are not reparsed by normal polling.
- MIME, attachment, latest-message, thread, and total model-context limits create explicit warnings
  and prevent automatic execution.
- IMAP identity uses account/mailbox/UIDVALIDITY/UID; sequence numbers are never persisted.
- Message-ID and raw-hash signals prevent duplicate logical processing.
- Each operation revision has a stable, unique API idempotency key.
- Closed/cancelled operations, weak/conflicting correlation, unsupported evidence, quoted-history
  fields, contradictions, and analyzer/verifier disagreement cannot auto-execute.
- Email content is untrusted, and structured model outputs are strictly validated with Pydantic.
- Full email-body logging is off by default.

Do not set `DRY_RUN=false` until endpoint behavior, authorization sources, TLS, backups, retention,
alerting, and a production safety threshold have been validated in the target environment.

## Current limitations

- LDAP/Active Directory is an adapter seam; only the static whitelist is implemented locally.
- Attachments are hashed and described but their binary content is not analyzed.
- HTML-to-text conversion is intentionally small and dependency-free.
- The automated default suite mocks IMAP/SMTP; the opt-in Docker mailbox journey above exercises
  real IMAP/SMTP with operator-supplied test credentials. The business API remains mocked in that
  journey.
- Qwen confidence values are stored but are not treated as calibrated probabilities.
- PostgreSQL is the deployment target, while automated tests currently exercise SQLite.
- Automatic clarification defaults to one round; subsequent incompleteness escalates.
- Corrections to completed operations require human review.
- The historical flat dataset has no RFC/stateful ground truth and only five unattributed
  `multiple` rows; the new `.eml` fixtures cover the stateful workflow separately.
- A USD cap cannot be enforced locally for calls whose vLLM endpoint exposes neither response
  cost nor usable pricing metadata when `EVALUATION_ALLOW_UNKNOWN_COST=true`.

Further detail is in [the migration plan](docs/langchain_five_agent_migration_plan.md),
[migration status](docs/langgraph_migration_status.md), and
[the vLLM guide](docs/vllm_inference.md).
