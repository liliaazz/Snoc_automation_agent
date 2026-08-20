# ADR: Durable correction grace period

- Status: Accepted
- Date: 2026-07-28
- Scope: Operations that have passed validation with `AUTO_EXECUTE`
- Decision driver: weakness-corpus case 50

## Context

Case 50 exposes an ordering problem, not an extraction problem. A complete first email can pass
analysis, verification, and policy and reach the business API before a correction in the same
thread is ingested. Once that side effect has happened, later correlation and correction handling
cannot safely undo it.

The design therefore needs a bounded opportunity for a user to correct or cancel an otherwise
valid request. It must not depend on an in-memory sleep, must preserve execution idempotency, and
must remain auditable across worker restarts.

## Options considered

In the comparison below, option B means a process-local timer or sleep. Option C means a persisted
queue whose due time is configurable; the selected design combines C with the short duration
proposed by B.

| Concern | A. Immediate execution | B. Configurable in-process grace | C. Durable queued two-phase execution | D. Manual approval for selected operations | E. Mailbox batch coalescing |
|---|---|---|---|---|---|
| Safety | No correction window; a valid but stale instruction can execute immediately. | Gives a short correction window, but loses safety state on a crash or deploy. | Gives a correction window and revalidates the immutable operation revision immediately before dispatch. | Highest pre-execution human control for selected actions, but remains dependent on reviewer quality. | Can observe corrections already present in the batch, but cannot protect against corrections that arrive after the batch boundary. |
| Latency | Lowest. | Adds the configured delay. | Adds the configured delay plus up to one worker polling interval. | Unbounded and dependent on staffing. | Adds the batch collection interval. |
| Operational complexity | Lowest. | Timer lifecycle, cancellation, and shutdown coordination are deceptively complex. | Requires a table, migration, dispatcher, state transitions, reconciliation, and monitoring. | Requires a review queue, authorization, staffing, expiry, and escalation procedures. | Requires deterministic mailbox watermarking, ordering, and batch-boundary rules. |
| Idempotency | Existing operation-revision key applies at execution. | Timer retries need their own durable identity or can schedule duplicates. | One queue row and one execution key per `operation_id:revision`; unique constraints and atomic claims prevent two workers from dispatching the same revision. | Approval and dispatch both need idempotency; a repeated approval must not repeat the side effect. | Duplicate suppression must span every message in the batch and the eventual dispatch. |
| Restart recovery | No pending state to recover, but an in-flight API outcome can still be unknown. | Poor: an in-memory wait disappears on restart. | `scheduled` rows survive restarts and are rediscovered. Claimed and in-flight states are persisted for audit and reconciliation. | Approval state can be restart-safe when persisted, at the cost of another workflow subsystem. | A persisted mailbox watermark can recover; an in-memory batch cannot. |
| User expectations | Appears instant, but leaves no practical time to retract a mistake. | A short delay is understandable only if acknowledged explicitly. | The acknowledgement says that validation succeeded, execution is queued, and a reply can correct or cancel it during the window. A completion message is sent only after dispatch. | Users must understand that the request is waiting for a person and may not have a predictable completion time. | Users see variable delay tied to mailbox activity rather than to their request. |
| Correction handling | A later correction can only be reviewed after the side effect. | Can cancel only while the original process and timer are alive. | A strongly correlated, explicit correction or cancellation changes waiting rows to `cancelled` with source-email audit data; stale revisions and ineligible operations are also cancelled at dispatch. | Reviewer applies the newest state before approval, but concurrent edits still need deterministic locking. | Corrections in the same batch can supersede earlier messages; later corrections retain the original race. |
| Existing worker | No change. | Blocks a worker or adds a process-local scheduler. | The worker polls IMAP, dispatches due durable rows, sends the outbox, and reports dispatch/failure counts on every loop. It does not sleep per operation. | Adds a separate reviewer-facing work queue and approval dispatcher. | Changes polling from message-by-message processing to watermark-based batch orchestration. |
| Existing tests | Existing synchronous assertions continue unchanged. | Time-dependent tests become fragile unless a clock/scheduler is injected. | Existing immediate tests opt into `0`; queue tests use an injected clock and assert scheduling, cancellation, atomic claims, revalidation, migration, and single execution. | Many automatic-execution tests must model approval state. | Stateful mail tests must model batch windows and ordering. |

## Decision

Use option C: a durable two-phase scheduled-execution queue with a configurable, short correction
window.

The exact application setting is:

```text
execution_correction_grace_seconds
```

The corresponding environment variable is:

```text
EXECUTION_CORRECTION_GRACE_SECONDS
```

The conservative default is `30` seconds. A negative value is rejected. Setting the value to `0`
is the explicit immediate-execution compatibility mode; it calls the execution service directly
and creates no scheduled row. Deployments must not use `0` merely to make timing-sensitive tests
pass.

The durable path has these semantics:

1. Policy still decides whether an operation is `AUTO_EXECUTE`. The grace period does not weaken
   authorization, field validation, evidence provenance, or any other hard invariant.
2. An eligible immutable operation revision is persisted in `scheduled_executions` with
   `not_before`, source email, request, operation revision, and the same
   `operation_id:revision` idempotency key used by real execution.
3. Waiting is not an API attempt: no `executions` row and no business call is created while the
   schedule is pending.
4. A same-thread correction or cancellation may cancel only a strongly correlated waiting
   request. The cancellation reason and source email are persisted.
5. At or after `not_before`, a worker atomically changes one row from `scheduled` to
   `dispatching`. Other workers lose that compare-and-set race.
6. Immediately before the business call, dispatch rechecks the operation's request, revision,
   idempotency key, status, eligibility, and `AUTO_EXECUTE` decision. A changed precondition
   cancels the queue item without calling the endpoint.
7. Successful dispatch links the scheduled row to the single execution record and changes it to
   `dispatched`. Terminal transport or application failures remain visible as audited failure or
   escalation state.

The persisted state flow is:

```text
AUTO_EXECUTE
    |
    v
scheduled --same-thread correction/cancellation--> cancelled
    |
    | not_before reached; atomic claim
    v
dispatching --precondition changed---------------> cancelled
    | \
    |  \ dispatch error
    |   v
    |  failed
    v
dispatched --> execution record --> completed/escalated
```

Only complete `AUTO_EXECUTE` operations enter this queue. Clarification requests and operations
already routed to review or escalation are not delayed.

## Audit and user communication

The scheduled row is the authoritative audit record. It preserves its source email, immutable
revision, due time, status timestamps, linked execution, last error, and cancellation data.
Current cancellation reason values include:

- `explicit_same_thread_correction_or_cancellation`;
- `analyzer_message_kind:correction` or `analyzer_message_kind:cancellation`;
- `stale_operation_revision`;
- `operation_not_ready`;
- `operation_not_execution_eligible`;
- `operation_decision_not_auto_execute`;
- `idempotency_key_mismatch`.

While a request waits, the outbox creates at most one acknowledgement marked with
`X-SNOC-Pending-Execution: true`. It states the configured correction window and does not claim
that the business operation completed. The terminal summary remains tied to actual dispatch.

Because dispatch occurs once per worker loop, observed latency is approximately the configured
grace plus scheduling and polling jitter. With the defaults it can be up to roughly one
`IMAP_POLL_SECONDS` interval longer than the nominal grace.

## Restart and failure considerations

Rows still in `scheduled` state survive a restart and are eligible on the next worker loop.
Persistence also makes a `dispatching` row visible after a crash. The current implementation does
not automatically lease and reclaim an abandoned `dispatching` row, so production monitoring
must alert on aged rows and an operator must reconcile them. A future change should add a claim
lease and a business-API-aware recovery procedure; it must not blindly redispatch an operation
whose remote outcome may be unknown.

Similarly, the external API must honor the idempotency key for safe retry after an ambiguous
transport outcome. The local unique key prevents two local execution records for the same
revision, but it cannot by itself prove what a remote system did.

## Validation

The durable queue is covered in
`tests/unit/test_execution_grace_scheduling.py`, including:

- no execution before the due time and exactly one execution afterward;
- idempotent repeated scheduling;
- audited cancellation by a correction email;
- cancellation of stale revisions and no-longer-eligible operations;
- atomic claims across workers;
- Alembic upgrade and downgrade.

Immediate compatibility behavior is exercised by integration scenarios that construct settings
with `execution_correction_grace_seconds=0`. The workflow-level regressions in
`tests/unit/test_execution_grace_workflow.py` assert both profiles: nonzero grace cancels a
case-50-style correction without an endpoint call, while explicit zero mode performs the
documented immediate dispatch. They also cover one pending acknowledgement, restart-safe
dispatch, terminal completion mail, and idempotent redispatch.

## Consequences

The chosen design intentionally trades a small, visible latency increase for a meaningful
correction opportunity and a durable audit trail. It adds database and worker complexity, so
queue-depth, oldest-due age, aged `dispatching` rows, cancellation rate, dispatch failures, and
end-to-end latency should be operational metrics.

Manual approval remains appropriate as a separate policy for unusually high-risk operation
classes. Mailbox batch coalescing is not selected because its safety boundary depends on arbitrary
poll timing and does not remove the later-correction race.
