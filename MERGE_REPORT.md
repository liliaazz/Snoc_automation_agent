# SNOC branch comparison and selective merge report

## Inputs

- `snoc_emails_agent-main.zip`
  - SHA-256: `26897dc0dd1a776448174ec9fe4ec8466be602b87a1868d4e455baa4bac48f0e`
  - 330 normalized files
- `snoc_emails_agent-agent-imap-escalation-vllm-routing.zip`
  - SHA-256: `a4132f96cddc1b771eacf0bc8a3015f7afd67dfee102bb29bf03552ec7cdc9e1`
  - 325 normalized files

Normalized comparison: 267 identical files, 53 differing common files, 10 main-only files, and 5 feature-only files.

## Test evidence before merging

The sandbox did not contain Docker, the npm dependency cache, or all optional Python packages. Tests that could run independently produced:

| Source | Collected | Passed | Skipped | Failed/errors |
|---|---:|---:|---:|---:|
| main, excluding FastAPI/LangGraph dependency-bound modules | 287 | 282 | 5 | 0 |
| feature branch, same limitation | 270 | 266 | 4 | 0 |

Both Python trees compiled successfully. Neither ZIP contained unsafe traversal paths or merge-conflict markers.

## Main branch advantages retained

- OpenAI-compatible/vLLM provider routing, budget controls, smoke tests, and live-test wiring.
- SVM fallback artifacts and evaluation support, disabled by default.
- `analyzer_v3` prompt with stronger negation and prompt-injection handling.
- Larger test suite.
- More defensive IMAP acknowledgement:
  - fetches with `BODY.PEEK[]`;
  - marks handled messages with `UID STORE +FLAGS.SILENT (\\Seen)`;
  - verifies `UIDVALIDITY` before acknowledgement;
  - treats durable failed results as database-retry work rather than repeatedly rediscovering the Gmail UID.

## Feature branch advantages ported

- Keycloak browser login and backend JWT/JWKS validation.
- Removal of hardcoded API usernames and passwords.
- Admin authorization on consequential API operations.
- Production-mode validation for database, mail, model, business API, and authentication settings.
- PostgreSQL advisory lock so only one worker polls IMAP.
- Human escalation for unauthorized senders.
- Escalation forwarding that preserves original email content.
- Raw MIME retention for outbox messages.
- Third independently routed `qwen3_30b` vLLM deployment.
- Docker diagnostics profile for Streamlit instead of starting it with the ordinary application.
- Localhost-only default API and Keycloak port bindings.
- Container-publishing GitHub workflow.

## Important design decisions in this merged tree

1. The feature branch is the stronger production/security direction, while main has broader evaluation functionality. The merge is therefore selective rather than a wholesale overwrite.
2. Main's UIDVALIDITY-aware IMAP implementation is retained.
3. SVM fallback remains optional; model inference uses the configured demo, OpenAI-compatible, or vLLM backend.
4. Local dashboard authentication, production validation, worker lease, escalation, and third-vLLM routing are added.
5. `USE_SVM_FALLBACK=false` remains the safe default.
6. `DRY_RUN=true` and `DRY_RUN_SEND_EMAILS=false` remain the safe local defaults.
7. `IMAP_SEARCH_CRITERION=UNSEEN` is the default for normal Gmail operation.
8. The Streamlit audit dashboard is optional under the `diagnostics` profile. The coded React dashboard is compiled into the API image and served on port 8000, or run through Vite on port 5173 during development.

## Merged validation performed

- All Python source compiled.
- Non-API/non-LangGraph suite: 298 collected, 293 passed, 5 skipped, 0 failed/errors.
- API suite with a minimal local SlowAPI import shim: 47 collected, 46 passed, 1 skipped, 0 failed/errors. This validates application route behavior, not SlowAPI's rate-limiter internals.
- Total executed: 345 collected, 339 passed, 6 skipped, 0 failed/errors.
- YAML structure parsed successfully.
- Frontend package and lock file contain no external identity-provider dependency.
- Original main hardcoded API passwords are absent.
- No conflict markers were found.

## Validation not possible in the sandbox

- Actual Docker Compose startup and PostgreSQL runtime.
- Actual SlowAPI rate-limiting behavior.
- LangGraph tests requiring unavailable LangChain/LangGraph dependencies.
- `npm ci` and Vite production build because registry access/cache were unavailable.
- Real Gmail, local-auth browser, business API, Qwen, or Gemma network calls.

Run the complete release gate in `RUN_AND_TEST.md` on a machine with Docker and internet access before production use.

## Development credentials warning

The included Keycloak realm intentionally contains a local test account:

- Username: `snoc-admin`
- Password: `snoc-admin`

Compose also defaults the Keycloak administrator to `admin` / `admin`. These values are only for isolated local development. Remove or replace them before any shared, staging, or production deployment.
