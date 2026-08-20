# Backend and Agent Hardening Audit

Audit date: 2026-07-26

## Architecture

The application is a FastAPI dashboard/API over SQLAlchemy repositories and an
email-processing workflow. Raw MIME is persisted before parsing. Correlation,
authorization, analyzer, verifier, policy decision, business adapter, execution,
outbox, and audit records form the processing chain. Provider-neutral model
interfaces support deterministic demo tests and OpenAI-compatible vLLM deployments.
The configured production path remains vLLM with
`Qwen/Qwen2.5-7B-Instruct-AWQ`. Automated acceptance uses isolated SQLite and
`DRY_RUN=true`.

The active environment is the root `.env`; `.env.example` is documentation only.
All documented keys were merged into `.env` without overwriting existing values.
No credential value is reproduced in this report.

## Problems found, root causes, and fixes

| Area | Root cause | Fix |
|---|---|---|
| Negated false positives | Model proposals were materialized without a deterministic exclusion-context boundary | Added a fail-closed intent-safety pass before request/operation persistence. English/French negation, historical-only mentions, unsupported reporting/HR intent, and prompt injection suppress unsafe proposals. An all-blocked email becomes irrelevant/`IGNORE` with no request or operation IDs. |
| Irrelevant and unsupported mail | Misleading action words and entity-shaped values could survive model classification | Safety filtering uses action-local context, unsupported-intent cues, and injection detection. End-to-end regression tests assert no request, operation, execution, verifier call, or response for negated cases. |
| Duplicate body with new Message-ID | Deduplication depended mainly on physical locator, raw digest, or RFC Message-ID | Added canonical sender + normalized subject + latest logical body comparison. A same subject with genuinely different body remains independent. |
| Dashboard counting | Request and operation counts were mixed | Summary/trend queries now use aggregate request semantics where the UI says requests, and expose explicit email and operation fields. Unauthorized/rejected email counts are period-scoped. |
| Datetime crash | SQLite returns naive datetimes while runtime cutoffs are aware UTC | Added one UTC utility module and normalized comparisons, durations, and serialization. Naive SQLite values are explicitly interpreted as UTC. |
| Model snapshot truthfulness | Schema validity was labelled as accuracy/F1 and empty-state zeros implied measurements | Accuracy, precision, recall, and F1 are now `null` without labelled ground truth. A separate `structuredOutputValidityRate` reports the real telemetry measure. Provider/model/fallback state derives from persisted runs and active configuration. |
| Frontend contract gaps | Runtime, analytics, and trace calls existed without matching stable routes | Added backend routes using live database aggregates. Added period/range alias validation, populated trace coverage, and stable empty responses. |
| Account data exposure | Account list returned persisted password material | Password fields are removed from API responses. |
| OpenAI-compatible structured output | A valid model could return truncated/malformed strict-schema JSON and abort the whole run | Added one bounded provider-supported JSON fallback/repair attempt. Fallback is separately audited and never changes provider/model. |
| vLLM discovery | Qwen-only acceptance failed because an unrelated Gemma deployment was unhealthy | Exact-model discovery now checks only deployments selected by that run. |
| FastAPI test/runtime deadlock | In this environment AnyIO's default worker-thread portal hangs | API handlers are async and the static response path avoids threadpool-backed file responses. API tests use ASGI transport directly. |
| External API status policy | Status coverage was incomplete | Added permanent 400/401/404/409/422/500 fail-closed tests; bounded 429/502, timeout, and connection retry tests; malformed/empty response and idempotency tests already exist. |
| Concurrency evidence | No explicit requested batch gate | Added 50 sequential plus 20 concurrent real MIME processing operations on an isolated SQLite database, asserting 70 unique stored messages and no conversations, requests, operations, executions, or outbox leakage. |

## Safety invariants verified

- Missing, ambiguous, conflicting, unsupported, unauthorized, negated, and
  prompt-injection inputs cannot auto-execute.
- Business adapters retry only bounded transient failures and only when remote
  idempotency is explicitly guaranteed.
- DRY_RUN executions are marked simulated and automated tests perform no real
  IMAP, SMTP, or business API side effects.
- Analyzer/verifier provider, exact model, prompt version, confidence payload,
  structured-output mode, fallback reason, decision reason, and model usage are
  persisted where available.
- Logs use structured identifiers at correlation, authorization, execution, and
  decision boundaries; credentials and raw authorization headers are not logged.

## Remaining limitations and risks

1. The latest real Qwen production-sequence semantic smoke score is 90% exact
   (9/10). Ambiguous mail was safely escalated as unknown instead of receiving the
   expected ambiguous label. Safety held—there were zero unsafe auto-execution
   proposals after the deterministic production boundary—but model-quality tuning
   remains.
2. Runtime model telemetry has no labelled outcomes, so precision/recall/F1 and
   accuracy are intentionally unknown. Offline/Qwen evaluation is the correct
   source for those metrics.
3. SQLite is validated for the supported bounded workload (50 sequential plus 20
   concurrent ignored messages). It is not a substitute for a server database for
   sustained multi-worker production write throughput.
4. FastAPI route bodies currently perform small synchronous SQLAlchemy queries in
   async handlers. This prevents the observed AnyIO deadlock but can block an event
   loop under high dashboard load. Migrating API persistence to async SQLAlchemy is
   recommended before high-concurrency deployment.
5. The frontend production bundle is about 875 kB before gzip and Vite emits a
   chunk-size warning. This is a performance warning, not a contract or build
   failure, and visual/frontend code was deliberately left unchanged.
6. Qwen acceptance is fake-data-only and DRY_RUN. Real production IMAP, SMTP, and
   business systems were intentionally not exercised.
