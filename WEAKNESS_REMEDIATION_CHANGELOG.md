# SNOC Weakness Remediation Changelog

Date: 2026-07-28

Branch and base: `main` at `79a21d01795f581b223680b306026c86a666196d`.
The worktree is intentionally uncommitted.

## Safety and email analysis

- Added analyzer prompt v4 and verifier prompt v3 with explicit multilingual
  new-request, correction, clarification, hypothetical, forwarding, ambiguity,
  and evidence rules.
- Added deterministic request-wide gates for ambiguous candidate attribution,
  subject/body conflicts, hypothetical or conditional text, forwarded content,
  forged workflow markers, reporting/examples, signature-like operational
  text, prompt injection, and conflicting identifiers in correlated replies.
- Added exact structured fields for direct-current instruction, candidate
  mapping, correction evidence, subject/body conflict, and evidence-source
  validity.
- Sanitized non-visible HTML and preserved warnings/provenance before model
  inference.
- Corrected Darija numeric token boundaries and format-aware PDV/phone
  candidate handling.
- Made deterministic correlation authoritative over unsupported model
  correction labels and fixed clarification/new-request-in-old-thread routing.

New stable reason codes include:

- `multiple_pdv_candidates`
- `multiple_phone_candidates`
- `ambiguous_identifier_attribution`
- `positional_pairing_not_explicit`
- `request_wide_identifier_mapping_ambiguous`
- `subject_body_pdv_conflict`
- `subject_body_phone_conflict`
- `subject_body_action_conflict`
- `hypothetical_or_conditional_request`
- `reporting_or_example_context`
- `signature_like_operational_text`
- `prompt_injection_pattern`
- `forwarded_content_requires_review`
- `forwarded_third_party_content`
- `untrusted_workflow_marker`
- `unknown_request_reference`
- `forged_completion_marker`
- `correlated_reply_pdv_conflict`
- `correlated_reply_phone_conflict`
- `correction_identifier_conflict`
- `model_context_limit`
- `provider_rejected_context`

## Execution safety and reliability

- Added a durable `scheduled_executions` queue and Alembic migration.
- Added configurable correction grace with a conservative 30-second default.
- Revalidated operation revision, eligibility, decision, and idempotency at
  dispatch time.
- Added atomic queue claims, correction cancellation, pending acknowledgement,
  terminal completion mail, and worker dispatch telemetry.
- Fixed IMAP UIDVALIDITY-aware acknowledgement merge defects.
- Durable `FAILED` outcomes are marked seen and retried from retained MIME,
  while processor exceptions remain unread.

## Provider handling

- Added context-window-aware output reservation for vLLM.
- Oversized prompts fail closed before transport and are audited without
  unsafe retry or malformed operations.
- Active runtime providers are demo, OpenAI-compatible, and vLLM. No Hugging
  Face runtime/provider integration is present.

## Frontend/backend integration

- Kept the dashboard UI unchanged while replacing invented/default chart
  values with typed backend data and explicit nullability.
- Added dashboard summary, trend, intent, recent-event, model, workflow,
  confidence, missing-entity, execution, runtime, and request-trace contracts.
- Replaced Keycloak with the supplied local dashboard authentication design:
  PBKDF2 password hashes, persistent database users, expiring signed bearer
  tokens, an environment-managed bootstrap administrator, and immediate token
  revocation for disabled or deleted database users.
- Removed the Keycloak service, realm import, frontend dependency, build
  arguments, volumes, and environment settings.
- Made auth/API modules safe for both Vite and Node contract tests.
- Added frontend adapter contracts for real execution, reply, duration,
  security-event, and model-quality data.

## Validation

- Python: 381 passed, 4 skipped.
- Ruff, Ruff format, and mypy: passed.
- Frontend clean install: passed; npm reported 6 dependency advisories
  (1 moderate, 5 high), not automatically rewritten.
- Frontend contracts and production build: passed.
- Docker Compose configuration: passed.
- Alembic has one head; a clean upgrade created both `scheduled_executions`
  and `dashboard_users`.
- Live local-auth frontend/backend contract check: 17 endpoints plus request
  trace returned 200 with zero literal `N/A` values; account lifecycle and
  disabled-user token revocation passed.
- Offline 70-case corpus: 57 passed, 13 conservative failures, 0
  oracle-contrary executions.
- Repeated safety corpus: 36/36 passed with 0 contrary executions.
- Full post-fix Qwen/Gemma corpus: pending because the configured Gemma
  endpoint timed out.
