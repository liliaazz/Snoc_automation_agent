# LangGraph Migration Status

## Implemented

- A checksum-verified pre-migration snapshot exists at
  `legacy/pre_langchain_20260723/`.
- `WORKFLOW_ENGINE=legacy` preserves the original `InboundProcessor` and remains the default.
- `WORKFLOW_ENGINE=langgraph` selects a compiled five-node `StateGraph`:
  `ingress → security → nlu → policy → fulfilment`.
- The runtime exposes both `processor` and `legacy_processor`, so switching engines does not
  require restoring source files.
- `WorkflowState` is JSON-serializable and contains IDs and bounded snapshots rather than raw MIME,
  credentials, SQLAlchemy objects, or adapters.
- The analyzer and verifier execute through LangChain `Runnable` adapters while retaining the
  current provider clients, Pydantic schemas, retry/fallback behavior, model audit rows, cache
  provenance, usage, and cost metadata.
- The graph policy node fails closed unless every executable operation has a persisted
  `AUTO_EXECUTE` validation decision and `execution_eligible=true`.
- Fulfilment continues to use the existing execution idempotency record and transactional outbox.
- SMTP delivery remains outside the graph in the independent outbox worker.
- The isolated project persists bounded `workflow_runs` and `workflow_events` records for every
  canonical and idempotent duplicate route. Raw MIME, prompts, credentials, and model reasoning are
  excluded from graph-event summaries.
- The dashboard exposes workflow health metrics and per-agent traces.

## Compatibility layer

The graph currently calls stage methods on a shallow copy of the legacy processor through
`graph/legacy_adapter.py`. This prevents business-logic duplication and establishes the five
ownership boundaries, but it is intentionally transitional:

- analyzer materialization, semantic verification, and the deterministic decision engine still
  share one legacy service implementation;
- graph invocation context is process-local and the graph is compiled without a durable
  checkpointer;
- `retry_stored` deliberately delegates to the proven legacy retry path.

The legacy processor instance itself is not modified: the graph copy owns the LangChain Runnable
adapters.

## Verification

Automated checks cover:

- default legacy selection
- graph selection with the original processor retained separately
- authorized end-to-end graph/legacy parity through dry-run execution
- unauthorized mail stopping before NLU, validation, or execution
- Ruff and mypy checks for the new graph and LangChain code
- fresh and repeated PostgreSQL migrations plus an Alembic model-drift check
- workflow-event ordering, terminal prefixes, node failures, and duplicate ingestion
- deterministic request-level ambiguity blocking and single-field clarification reconciliation

The isolated real-mail acceptance run `20260723T143450Z` passed all six scenarios using Qwen for
analysis and Gemma for semantic verification:

- 6 passed, 0 failed, 0 Gmail threading failures
- 4 unique successful telecom executions, all with `dry_run=true`
- no execution for the ambiguous multi-operation request
- no model inference for the automated out-of-office message
- 13 journey model runs, all schema-valid and without recorded errors
- 101 completed workflow runs and 126 unique ordered workflow events, including ingress-only
  idempotent duplicate routes
- all 8 outbox rows sent

The report and container logs are retained under
`outputs/20260723T143450Z/`. The isolated worker was stopped after acceptance and the root legacy
worker was restarted.

The known pre-existing suite failure remains
`test_dataset_builder_labels_194_demo_candidates_as_not_qwen`, whose source CSV
`labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv` is absent from the repository.

## Next migration slice

1. Split semantic verification from deterministic decision persistence into separate public
   services so the NLU and Policy ownership boundary no longer uses a compatibility adapter.
2. Reconstruct node input from persisted email/request IDs, allowing graph resume without
   process-local parsed/prepared objects.
3. Add a PostgreSQL LangGraph checkpointer in an isolated schema and crash/resume tests.
4. Add same-request concurrency control using request versioning or row locking.
5. Add correction and mixed-operation parity scenarios beyond the accepted six-journey baseline.
6. Canary the graph worker in a staging mailbox before changing the default engine.

Do not remove the legacy path until all six items pass their release gates.
