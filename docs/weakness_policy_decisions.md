# Weakness-corpus product-policy decisions

- Status: Conservative defaults accepted
- Date: 2026-07-28
- Cases: 28, 50, 60, and 66

## Configuration status

The production default for every case in this document is no unintended automatic execution.
All four settings are implemented in the typed configuration and documented in `.env.example`.
The three exception switches default to `false`; the correction window defaults to 30 seconds.
Every applied exception is persisted in
`EmailMessage.context_limit_metadata.request_safety_policy_overrides`.

All alternatives below are narrow exceptions. Even when enabled, they must
not bypass authorization, structured-output validation, evidence provenance, identifier
validation, ambiguity checks, model agreement, correlation rules, duplicate suppression, or
execution idempotency.

## Case 28: subject/body action conflict

### Decision

- Conservative corpus expectation: no automatic execution when the subject says account unblock
  but the current body requests password reset and explicitly negates unblock.
- Current remediated behavior: deterministic request-wide safety reports
  `subject_body_action_conflict`. The decision engine sets
  `request_safety_checks_passed=false`, produces `ESCALATE`, and makes the operation ineligible
  even when analyzer and verifier agree on the positive body instruction.
- Safety risk: a stale, malicious, accidentally reused, or misunderstood subject can disagree
  with the current instruction. Silently selecting either source can target the wrong endpoint.
- Usability argument: normal email clients often preserve stale subjects, while a clear current
  body can be the sender's most recent and most deliberate instruction. A blanket review creates
  false positives for legitimate users.
- Recommended production default: block automatic execution and ask for review or clarification.
  Subject/body PDV and phone conflicts remain blocked independently with
  `subject_body_pdv_conflict` and `subject_body_phone_conflict`.

### Optional alternative

Implemented typed setting:

```text
allow_subject_body_conflict_auto_execution: bool = False
ALLOW_SUBJECT_BODY_CONFLICT_AUTO_EXECUTION=false
```

When enabled, `true` may suppress only `subject_body_action_conflict` when the current visible
body contains an explicit affirmative action, explicitly negates the subject action, has complete
current-message evidence, and every other hard invariant passes. It must never suppress an
identifier conflict, ambiguity, hypothetical, hidden-content, quoted-content, or forwarded-content
reason. The decision audit must add `subject_body_conflict_explicit_body_override`; the default
denial reason remains `subject_body_action_conflict`.

### Tests

- Denied mode, implemented:
  `tests/unit/test_request_safety.py::test_subject_and_body_action_conflict_is_blocked_despite_negation`.
- Policy propagation, implemented:
  `tests/unit/test_decision_engine.py::test_request_wide_deterministic_safety_blocker_overrides_model_agreement`.
- Allowed-mode gate and audit:
  `tests/unit/test_request_safety.py::test_policy_alternatives_are_narrow_and_audited`.
- Identifier boundary:
  `tests/unit/test_request_safety.py::test_subject_override_never_suppresses_identifier_conflict`.
- Boundary tests required in both modes: differing PDV or phone, ambiguous candidates, weak
  evidence, and non-explicit negation must always produce zero endpoint calls.

## Case 50: correction arrives before dispatch

### Decision

- Conservative corpus expectation: the original complete request must not execute if a strongly
  correlated correction or cancellation arrives inside the approved window.
- Previous risk: synchronous fulfilment executed the first request before the second email could
  be observed. The correction was then accurately recognized but was too late to prevent the side
  effect.
- Current remediated behavior: validated operations use the durable `scheduled_executions` queue
  when the grace is nonzero. A same-thread correction or cancellation changes waiting rows to
  `cancelled`; due dispatch revalidates the immutable revision before calling the business API.
- Safety risk: immediate execution turns a correctable typing error into a real, potentially
  irreversible side effect. A process-local delay would merely move the race and lose state on
  restart.
- Usability argument: users value low latency, and some controlled deployments may have upstream
  validation that makes a correction window unnecessary.
- Recommended production default: durable correction grace with a nonzero duration. The shipped
  default is 30 seconds.

### Exact configuration and alternative

Implemented typed setting:

```text
execution_correction_grace_seconds: int = 30
EXECUTION_CORRECTION_GRACE_SECONDS=30
```

- Any positive value enables durable queued dispatch.
- `0` is the explicit immediate-mode alternative.
- Negative values are rejected.

The queue status `scheduled` is the audit proof that grace was applied. Correction audit values
include `explicit_same_thread_correction_or_cancellation`,
`analyzer_message_kind:correction`, and `analyzer_message_kind:cancellation`. Dispatch-time
denials include `stale_operation_revision`, `operation_not_ready`,
`operation_not_execution_eligible`, and `operation_decision_not_auto_execute`.

### Tests

- Grace/denied dispatch mode, implemented:
  `tests/unit/test_execution_grace_scheduling.py::test_request_correction_cancels_waiting_items_with_audit_data`.
- Due execution and idempotency, implemented:
  `tests/unit/test_execution_grace_scheduling.py::test_schedule_waits_without_creating_execution_then_dispatches_once`.
- Dispatch safety and concurrency, implemented: the stale-revision, no-longer-ready, and
  atomic-claim tests in `tests/unit/test_execution_grace_scheduling.py`.
- Immediate/allowed dispatch mode, exercised by integration scenarios whose settings explicitly
  use `execution_correction_grace_seconds=0`.
- Required workflow regression: process a case-50-style two-message thread with a positive grace
  and assert cancellation plus zero API calls, then repeat with explicit zero mode and assert the
  documented immediate first call. The test must use an injected clock or forced due time, never a
  real sleep.

The complete architecture decision is in
`docs/adr/ADR-correction-grace-period.md`.

## Case 60: authorized sender adopts a forwarded instruction

### Decision

- Conservative corpus expectation: review; do not automatically execute the forwarded third-party
  VPN instruction even though the current sender asks the system to process it.
- Current remediated behavior: the parser separates the forwarded block and records
  `forwarded_content_detected`. Deterministic request safety adds
  `forwarded_third_party_content`; model-originated signals can also add
  `analyzer_forwarded_content` or
  `non_executable_message_kind:forwarded_request`. Any of these safety signals makes automatic
  execution ineligible.
- Safety risk: forwarding does not prove that the third party is authorized, that the text is
  current, or that the forwarding sender deliberately adopts every embedded identifier and
  operation. Forwarded history is also easy to edit.
- Usability argument: an authorized operator may intentionally delegate a clearly identified
  forwarded request and expect it to be handled without retyping it.
- Recommended production default: forwarded third-party evidence requires review. Current-message
  text may describe the forward, but must not silently convert the forwarded block into trusted
  operational evidence.

### Optional alternative

Implemented typed setting:

```text
allow_forwarded_content_auto_execution: bool = False
ALLOW_FORWARDED_CONTENT_AUTO_EXECUTION=false
```

When enabled, `true` removes the forwarded-content review gate only when the analyzer also reports
`direct_current_instruction=true`. Authorization remains an earlier mandatory gate. Forwarded
provenance and complete, unambiguous operation-to-identifier attribution remain required. The
setting does not treat forwarding alone as consent, and quoted or hidden content remains
ineligible. The allowed-mode audit adds
`forwarded_third_party_instruction_explicitly_adopted`; the default denial reason remains
`forwarded_third_party_content`.

### Tests

- Denied mode, implemented:
  `tests/unit/test_request_safety.py::test_forwarded_and_untrusted_workflow_markers_are_blocked`.
- Provenance, implemented: forwarded segmentation tests in
  `tests/unit/test_reply_segmenter.py` and
  `tests/unit/test_mail_parser.py::test_parser_preserves_forwarded_content_outside_the_current_message`.
- Allowed-mode gate and audit:
  `tests/unit/test_request_safety.py::test_policy_alternatives_are_narrow_and_audited`.
- Boundary tests required in both modes: unauthorized sender, ambiguous identifiers, nested
  forward, edited/hidden content, forwarded-only command without current adoption, and a forward
  containing multiple operations must produce zero endpoint calls unless independently and
  explicitly supported.

## Case 66: forged or unknown workflow marker

### Decision

- Conservative corpus expectation: review and zero automatic execution, even when the same email
  also contains a fresh direct unblock request.
- Current remediated behavior: correlation correctly does not trust an unknown marker. For
  `new` or `weak` correlation, a workflow-looking marker in the subject or visible body adds
  `untrusted_workflow_marker`, fails the deterministic request-safety invariant, and escalates the
  otherwise fresh operation.
- Safety risk: treating user-supplied text as an internal completion/reference marker can forge
  workflow state, confuse operators, or conceal a new side effect behind a fabricated audit
  narrative.
- Usability argument: a harmless copied or mistyped reference should not necessarily invalidate a
  separate, fully supported request if correlation ignores it.
- Recommended production default: any unknown or forged workflow marker makes the whole message
  ineligible for automatic execution. A marker becomes trusted through persisted correlation
  state and authenticated mail/thread evidence, not by matching its textual shape.

### Optional alternative

Implemented typed setting:

```text
allow_untrusted_workflow_marker_auto_execution: bool = False
ALLOW_UNTRUSTED_WORKFLOW_MARKER_AUTO_EXECUTION=false
```

When enabled, `true` still ignores the unknown marker for correlation and may permit only a
separate fresh direct request whose current-message evidence independently passes every hard
invariant. It must add `untrusted_workflow_marker_ignored_for_fresh_request` to the decision audit.
It must never turn the marker into a trusted request or operation reference. The default denial
reason remains `untrusted_workflow_marker`.

### Tests

- Denied mode, implemented:
  `tests/unit/test_request_safety.py::test_forwarded_and_untrusted_workflow_markers_are_blocked`.
- Allowed-mode gate and audit:
  `tests/unit/test_request_safety.py::test_policy_alternatives_are_narrow_and_audited`.
- Boundary tests required in both modes: a forged marker with no fresh request, a marker naming
  another sender's request, malformed marker variants, and a marker plus ambiguous or conflicting
  identifiers must produce zero endpoint calls.
- Trusted-path regression: a real persisted request reference with valid same-sender thread
  headers continues to correlate normally and is not labelled forged.

## Change-control rule

The conservative corpus oracle remains authoritative. Enabling any future alternative must be a
reviewed product-policy change with:

1. a typed, documented setting with a conservative default;
2. deployment-level authorization for changing it;
3. a distinct persisted audit reason for every override;
4. denied-mode, allowed-mode, and invariant-boundary tests;
5. repeated real-model corpus runs showing no wrong endpoint, identifier, pairing, hidden-content,
   hypothetical, or duplicate execution.

The exception modes are intentionally off in the conservative corpus and production defaults.
