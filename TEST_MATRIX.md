# Hardening Test Matrix

The machine-readable traceability fixture is
`tests/fixtures/emails/hardening_matrix.json`. Its schema and exact set of 50
required IDs are enforced by `tests/unit/test_hardening_matrix.py`. Functional
coverage is layered across parser, policy, repository, adapter, API, and
end-to-end suites; this file does not claim that the JSON metadata alone executes
the behavior.

| # | Case | Layer | Expected invariant | Coverage |
|---:|---|---|---|---|
| 1 | Account unblock, valid PDV | E2E | AUTO_EXECUTE | acceptance scenarios / Qwen smoke |
| 2 | Password reset, valid PDV | E2E | AUTO_EXECUTE | acceptance scenarios / Qwen smoke |
| 3 | OTP change, valid PDV + phone | E2E | AUTO_EXECUTE | acceptance scenarios / Qwen smoke |
| 4 | VPN, valid PDV + phone | E2E | AUTO_EXECUTE | acceptance scenarios / Qwen smoke |
| 5 | OTP missing phone | E2E | ASK_FOR_INFORMATION | acceptance scenarios / Qwen smoke |
| 6 | VPN missing phone | E2E | ASK_FOR_INFORMATION | acceptance scenarios |
| 7 | Missing PDV | Policy | no auto-execution; clarify | decision/entity tests |
| 8 | Invalid PDV | Policy | no execution | schema/policy tests |
| 9 | Invalid phone | Policy | no execution | schema/policy tests |
| 10 | Two ambiguous phones | Policy | no execution | safety reconciliation |
| 11 | Two ambiguous PDVs | Policy | no execution | safety reconciliation |
| 12 | Multi-operation, all complete | E2E | completed aggregate | acceptance scenarios / Qwen smoke |
| 13 | Multi-operation, one incomplete | E2E | partial completion | acceptance scenarios |
| 14 | Multi-operation, all incomplete | E2E | needs information | acceptance scenarios |
| 15 | French email | Model | supported | smoke/evaluation datasets |
| 16 | English email | Model | supported | model/schema tests |
| 17 | Mixed French/English | Model | supported | model/schema tests |
| 18 | HTML-only email | Parser | parsed safely | mail parser tests |
| 19 | Empty subject | Parser | safe handling | mail parser tests |
| 20 | Empty body | Parser | no execution | mail parser tests |
| 21 | Malformed MIME | Parser | quarantine | inference robustness |
| 22 | Forwarded email | Parser | quoted/history-safe | mail parser/context tests |
| 23 | Quoted history | Policy | no historical execution | Qwen smoke/context tests |
| 24 | Misleading signature | Policy | no signature execution | context filtering tests |
| 25 | Negated OTP | Safety | IGNORE, no IDs | intent safety + E2E regression |
| 26 | Negated password reset | Safety | IGNORE, no IDs | intent safety + E2E regression |
| 27 | Negated VPN | Safety | IGNORE, no IDs | intent safety + E2E regression |
| 28 | Negated account unblock | Safety | IGNORE, no IDs | intent safety + E2E regression |
| 29 | Reporting issue with PDV/phone | Safety | IGNORE | intent safety |
| 30 | Irrelevant HR meeting | Model/policy | IGNORE | intent safety + Qwen smoke |
| 31 | Marketing email | Model/policy | IGNORE | filtering/model tests |
| 32 | Automatic notification | Ingress | IGNORE | mail marker + batch tests |
| 33 | Unauthorized sender | Security | no execution | authorization/acceptance tests |
| 34 | Spoofed display name | Security | address controls; no execution | authorization tests |
| 35 | Duplicate Message-ID | Repository | one logical result | duplicate tests |
| 36 | Duplicate body, new Message-ID | Repository | duplicate, no repeat effects | acceptance regression |
| 37 | Clarification reply, phone only | Correlation | update original | acceptance/Qwen smoke |
| 38 | Clarification reply, PDV only | Correlation | update original | correlation tests |
| 39 | Clarification reply, conflict | Correlation | escalate/no execution | correlation tests |
| 40 | Orphan reply | Correlation | no weak merge | correlation tests |
| 41 | Low confidence | Policy | no execution | decision engine tests |
| 42 | External timeout | Adapter | unknown/failed, bounded retry | adapter/execution-failure tests |
| 43 | External 500 | Adapter | failed, no false completion | adapter status matrix |
| 44 | Outbox failure | Outbox | retryable, no duplicate | acceptance/outbox tests |
| 45 | Concurrent batch | Performance | no duplicates/loss/corruption | 50 sequential + 20 concurrent MIME test |
| 46 | Partial DB failure | Repository | rollback | repository/transaction tests |
| 47 | Idempotent retry | Execution | one external execution | adapter/execution tests |
| 48 | Unicode/accented French | Parser | preserved/supported | parser/model tests |
| 49 | Very long email | Limits | bounded context; no unsafe auto-execution | inference robustness |
| 50 | Prompt injection | Safety | input cannot override policy | intent safety |

## Additional contract and failure coverage

- FastAPI: empty/populated payloads, period aliases, 400/404/422 handling,
  naive SQLite datetimes, password redaction, populated trace, and 50+20 stable
  concurrent dashboard reads.
- Business API: success; 400, 401, 404, 409, 422, 429, 500, 502, and 503;
  timeout; connection failure; malformed/empty/oversized/untrusted JSON; bounded
  retries; and remote-idempotency gating.
- Structured model output: strict JSON-schema success, bounded fallback/repair,
  malformed response failure, usage accounting, provider/model attribution, and
  no demo fallback in Qwen acceptance.
- Outbox: terminal summary, clarification, multi-operation result, escalation,
  send failure/retry, and duplicate prevention are covered by existing integration
  and service tests.

Live external dependencies are intentionally skipped by the ordinary unit suite.
The dedicated Qwen runner is the only acceptance path that requires the configured
vLLM service; real IMAP, SMTP, and business endpoints remain disabled.
