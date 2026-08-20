"""Stage adapter that extracts safe boundaries from the legacy processor.

This compatibility layer deliberately centralizes access to the processor's private stage
methods. It lets the graph replace orchestration without copying business logic. Later phases can
move each stage into a public service while keeping the graph contracts stable.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from snoc_agent.ai.backend import safe_generation_settings
from snoc_agent.ai.intent_safety import apply_intent_safety
from snoc_agent.ai.schemas import EmailAnalysis
from snoc_agent.db.models import EmailMessage, Execution, Operation, ValidationDecision
from snoc_agent.db.session import session_scope
from snoc_agent.domain.enums import ExecutionStatus, FinalDecision, ProcessingStatus
from snoc_agent.mail.parser import parse_email
from snoc_agent.workflow.inbound_processor import (
    ProcessingResult,
    _OperationWork,
)
from snoc_agent.workflow.model_audit import persist_failed_model_run

from .context import GraphExecutionContext


class LegacyStageAdapter:
    """Expose the legacy processor as explicit graph stages."""

    def ingress(self, context: GraphExecutionContext) -> ProcessingResult | None:
        processor = context.processor
        stored = processor._store_raw_minimal(context.raw_message, context.identity)
        context.email_id = stored.email_message_id
        if stored.status == ProcessingStatus.DUPLICATE.value:
            return stored
        if len(context.raw_message) > processor.settings.max_raw_email_bytes:
            return processor._quarantine(
                stored.email_message_id,
                category="raw_email_size_limit",
                safe_message=(
                    "Raw email exceeds MAX_RAW_EMAIL_BYTES; manual inspection is required."
                ),
            )
        try:
            parsed = parse_email(
                context.raw_message,
                system_address=processor.settings.effective_system_email_address,
                content_limits=processor._content_limits,
            )
        except Exception as exc:
            return processor._quarantine(
                stored.email_message_id,
                category=type(exc).__name__,
                safe_message="MIME parsing failed; inspect the retained raw email before retrying.",
            )
        context.parsed = parsed
        applied = processor._apply_parsed(stored.email_message_id, parsed)
        if applied.status == ProcessingStatus.DUPLICATE.value:
            return applied
        return None

    def security(
        self, context: GraphExecutionContext, email_id: uuid.UUID
    ) -> ProcessingResult | None:
        if context.parsed is None:
            raise RuntimeError("ingress did not provide parsed email")
        prepared = context.processor._prepare(email_id, context.parsed)
        if isinstance(prepared, ProcessingResult):
            return prepared
        context.prepared = prepared
        return None

    def analyze(
        self, context: GraphExecutionContext
    ) -> tuple[list[_OperationWork], list[uuid.UUID], ProcessingResult | None]:
        processor = context.processor
        prepared = context.prepared
        parsed = context.parsed
        if prepared is None or parsed is None:
            raise RuntimeError("security did not provide prepared workflow context")
        processor._cancel_pending_execution_for_follow_up(prepared)
        try:
            analysis_result = processor.analyzer.analyze(prepared.context)
        except Exception as exc:
            with session_scope(processor.session_factory) as session:
                persist_failed_model_run(
                    session,
                    stage="analysis",
                    prompt_version=processor.analyzer.prompt_version,
                    input_context=prepared.context,
                    email_message_id=prepared.email_id,
                    model_name=processor.analyzer.config.model,
                    backend=str(
                        getattr(
                            processor.analyzer.backend,
                            "backend_name",
                            type(processor.analyzer.backend).__name__,
                        )
                    ),
                    error=exc,
                    quantization=processor.settings.model_quantization or None,
                    generation_settings=safe_generation_settings(processor.analyzer.config),
                    base_model_id=processor.analyzer.config.base_model,
                    resolved_model_id=processor.analyzer.config.model,
                    requested_route=processor.analyzer.config.model,
                    json_schema=EmailAnalysis.model_json_schema(),
                    schema_name=EmailAnalysis.__name__,
                )
            raise
        analysis = analysis_result.parsed
        if not isinstance(analysis, EmailAnalysis):
            raise TypeError("analyzer returned an unexpected schema")
        safety = apply_intent_safety(
            analysis,
            str(prepared.context.get("latest_user_message", "")),
        )
        analysis = safety.analysis
        processor._cancel_pending_execution_for_follow_up(prepared, analysis=analysis)
        if safety.reasons:
            with session_scope(processor.session_factory) as session:
                email = session.get(EmailMessage, prepared.email_id)
                if email is not None:
                    warnings = list(email.parsing_warnings)
                    warnings.extend(
                        f"intent_safety:{reason}"
                        for reason in safety.reasons
                        if f"intent_safety:{reason}" not in warnings
                    )
                    email.parsing_warnings = warnings
                    metadata = dict(email.context_limit_metadata)
                    metadata["blocked_operation_ids"] = list(safety.blocked_operation_ids)
                    metadata["intent_safety_reasons"] = list(safety.reasons)
                    email.context_limit_metadata = metadata
        context.analysis = analysis
        work, request_ids, early = processor._materialize_analysis(
            prepared, parsed, analysis, analysis_result
        )
        context.work = work
        return work, request_ids, early

    def verify_and_decide(
        self, context: GraphExecutionContext
    ) -> tuple[list[uuid.UUID], list[str]]:
        if (
            context.prepared is None
            or context.parsed is None
            or context.analysis is None
            or context.work is None
        ):
            raise RuntimeError("NLU stage context is incomplete")
        return context.processor._verify_and_decide(
            context.prepared,
            context.parsed,
            context.analysis,
            context.work,
        )

    def assert_policy_outputs(
        self,
        context: GraphExecutionContext,
        operation_ids: list[uuid.UUID],
        execute_ids: list[uuid.UUID],
    ) -> None:
        """Fail closed if graph state and persisted policy records disagree."""

        execute_set = set(execute_ids)
        with session_scope(context.processor.session_factory) as session:
            operations = {
                operation.id: operation
                for operation in session.scalars(
                    select(Operation).where(Operation.id.in_(operation_ids))
                ).all()
            }
            latest_decisions: dict[uuid.UUID, ValidationDecision] = {}
            for decision in session.scalars(
                select(ValidationDecision)
                .where(ValidationDecision.operation_id.in_(operation_ids))
                .order_by(ValidationDecision.created_at)
            ).all():
                if decision is not None:
                    latest_decisions[decision.operation_id] = decision
            for operation_id in execute_set:
                operation = operations.get(operation_id)
                persisted_decision = latest_decisions.get(operation_id)
                if (
                    operation is None
                    or persisted_decision is None
                    or persisted_decision.decision != FinalDecision.AUTO_EXECUTE.value
                    or not operation.execution_eligible
                ):
                    raise RuntimeError(
                        f"policy persistence mismatch for executable operation {operation_id}"
                    )
                key = context.processor.execution_service.idempotency_key(operation)
                if session.scalar(select(Execution).where(Execution.idempotency_key == key)):
                    raise RuntimeError(
                        f"operation revision {operation_id} already has an execution record"
                    )

    def fulfil(
        self,
        context: GraphExecutionContext,
        *,
        email_id: uuid.UUID,
        request_ids: list[uuid.UUID],
        execute_ids: list[uuid.UUID],
        decisions: list[str],
    ) -> ProcessingResult:
        processor = context.processor
        for operation_id in execute_ids if context.execute_operations else []:
            outcome = processor._execute_or_schedule(email_id, operation_id)
            if outcome is None:
                continue
            if outcome.status != ExecutionStatus.SUCCEEDED:
                processor._record_execution_escalation(
                    email_id=email_id,
                    operation_id=operation_id,
                    outcome=outcome,
                )
                decisions.append(FinalDecision.ESCALATE.value)
        clarification_id = context.prepared.clarification_id if context.prepared else None
        return processor._finalize(
            email_id=email_id,
            request_ids=request_ids,
            decisions=decisions,
            clarification_id=clarification_id,
        )

    def mark_failed(
        self, context: GraphExecutionContext, email_id: uuid.UUID, error: Exception
    ) -> ProcessingResult:
        context.processor._mark_failed(email_id, str(error))
        return ProcessingResult(
            email_message_id=email_id,
            status=ProcessingStatus.FAILED.value,
            detail=str(error),
        )
