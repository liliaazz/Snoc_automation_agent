"""Stateful inbound-email application service.

The processor deliberately uses several short database transactions:

1. raw mail and physical identity are committed before any AI call;
2. model proposals and operation revisions are committed before execution;
3. the execution service durably records an idempotency key before calling an API;
4. aggregate status and outbound replies are finalized afterwards.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import safe_generation_settings
from snoc_agent.ai.context_builder import ContextBuilder
from snoc_agent.ai.intent_safety import apply_intent_safety
from snoc_agent.ai.schemas import (
    EmailAnalysis,
    FieldEvidence,
    ProposedOperation,
    SemanticVerification,
)
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.config import Settings
from snoc_agent.db.base import utc_now
from snoc_agent.db.models import (
    BusinessRequest,
    Clarification,
    Conversation,
    EmailMessage,
    Escalation,
    Execution,
    FieldRevision,
    MailAccount,
    Operation,
    ScheduledExecution,
    ValidationDecision,
)
from snoc_agent.db.repositories import (
    ClarificationRepository,
    ConversationRepository,
    EmailRepository,
    OperationRepository,
    RequestRepository,
)
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.domain.entities import CorrelationResult, OperationSnapshot
from snoc_agent.domain.enums import (
    ClarificationStatus,
    CorrelationStrength,
    Direction,
    ExecutionStatus,
    FinalDecision,
    OperationStatus,
    ProcessingStatus,
    RequestKind,
    RequestStatus,
)
from snoc_agent.domain.value_objects import (
    canonical_action,
    normalize_numeric,
    validate_operation_fields,
)
from snoc_agent.mail.correlation import correlate_email, uuid_or_none
from snoc_agent.mail.headers import bare_address
from snoc_agent.mail.markers import generate_request_reference
from snoc_agent.mail.mime import ContentLimits
from snoc_agent.mail.parser import ParsedEmail, parse_email
from snoc_agent.workflow.authorizer import SenderAuthorizer
from snoc_agent.workflow.clarification_service import ensure_clarification
from snoc_agent.workflow.decision_engine import (
    DecisionContext,
    HybridDecisionEngine,
)
from snoc_agent.workflow.escalation_service import create_escalation
from snoc_agent.workflow.execution_service import (
    ExecutionOutcome,
    ExecutionService,
    ScheduledDispatchOutcome,
    ScheduledExecutionStatus,
)
from snoc_agent.workflow.model_audit import persist_failed_model_run, persist_model_run
from snoc_agent.workflow.operation_service import (
    apply_proposal_fields,
    effective_missing_fields,
    set_operation_status,
)
from snoc_agent.workflow.pending_execution_service import (
    ensure_pending_execution_acknowledgement,
)
from snoc_agent.workflow.reply_summary_service import ensure_terminal_summary
from snoc_agent.workflow.request_safety import assess_request_safety
from snoc_agent.workflow.request_service import refresh_request_status

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InboundIdentity:
    account_id: uuid.UUID | None = None
    mailbox: str | None = None
    uidvalidity: int | None = None
    uid: int | None = None
    internal_date: datetime | None = None
    flags: tuple[str, ...] = ()
    provider_metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class ProcessingResult:
    email_message_id: uuid.UUID
    status: str
    conversation_id: uuid.UUID | None = None
    request_ids: list[uuid.UUID] = field(default_factory=list)
    operation_ids: list[uuid.UUID] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    duplicate_of_id: uuid.UUID | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class _Prepared:
    email_id: uuid.UUID
    conversation_id: uuid.UUID
    correlation: CorrelationResult
    context: dict[str, Any]
    request_id: uuid.UUID | None
    clarification_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _OperationWork:
    operation_id: uuid.UUID
    request_id: uuid.UUID
    proposal: ProposedOperation
    correlation_strength: CorrelationStrength
    may_use_stored_state: bool = False
    analysis_override: EmailAnalysis | None = None


_ATTRIBUTION_UNCERTAINTY_MARKERS = (
    "ne sais pas quel",
    "ne sais plus quel",
    "ne sait pas quel",
    "ne sait plus quel",
    "pas certain de quel",
    "pas certaine de quel",
    "not sure which",
    "do not know which",
    "don't know which",
    "cannot tell which",
)
_ATTRIBUTION_RELATION_MARKERS = (
    "correspond",
    "attribu",
    "associ",
    "demande",
    "opération",
    "operation",
    "request",
    "belongs",
    "maps",
)
_REQUEST_NOUN_MARKERS = ("request", "demande", "ticket", "طلب")
_INDEPENDENT_REQUEST_MARKERS = (
    "independent",
    "separate",
    "distinct",
    "indépend",
    "sépar",
    "مستقل",
    "منفصل",
)
_EXPLICIT_CORRECTION_OR_CANCELLATION_RE = re.compile(
    r"(?:\b(?:correction|corrig(?:er|ez|é)|rectification|annul(?:er|ez|ation)|"
    r"cancel|do\s+not\s+(?:process|execute)|ne\s+(?:traitez|faites|exécutez)\s+pas)\b|"
    r"(?:تصحيح|إلغاء|لا\s+(?:تنفذ|تعالج))|\b(?:sahh|baddel)\b)",
    re.IGNORECASE,
)


def _has_explicit_cross_operation_ambiguity(message: str) -> bool:
    """Detect an explicit refusal to attribute values across multiple operations."""

    normalized = " ".join(message.casefold().split())
    return any(marker in normalized for marker in _ATTRIBUTION_UNCERTAINTY_MARKERS) and any(
        marker in normalized for marker in _ATTRIBUTION_RELATION_MARKERS
    )


def _has_explicit_independent_request(message: str) -> bool:
    """Recognize an explicit request boundary inside an existing mail thread."""

    normalized = " ".join(message.casefold().split())
    return any(marker in normalized for marker in _REQUEST_NOUN_MARKERS) and any(
        marker in normalized for marker in _INDEPENDENT_REQUEST_MARKERS
    )


class InboundProcessor:
    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        settings: Settings,
        analyzer: EmailAnalyzer,
        verifier: SemanticVerifier,
        authorizer: SenderAuthorizer,
        decision_engine: HybridDecisionEngine,
        execution_service: ExecutionService,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.analyzer = analyzer
        self.verifier = verifier
        self.authorizer = authorizer
        self.decision_engine = decision_engine
        self.execution_service = execution_service
        self.context_builder = ContextBuilder(
            max_context_characters=settings.max_model_context_characters,
            max_latest_characters=settings.max_latest_message_characters,
            max_relevant_thread_characters=settings.max_relevant_thread_characters,
        )

    @property
    def _content_limits(self) -> ContentLimits:
        return ContentLimits(
            max_text_part_bytes=self.settings.max_text_part_bytes,
            max_html_part_bytes=self.settings.max_html_part_bytes,
            max_attachment_count=self.settings.max_attachment_count,
            max_attachment_bytes=self.settings.max_attachment_bytes,
        )

    def process_raw(
        self,
        raw_message: bytes,
        *,
        identity: InboundIdentity | None = None,
        execute_operations: bool = True,
    ) -> ProcessingResult:
        identity = identity or InboundIdentity()
        stored = self._store_raw_minimal(raw_message, identity)
        if stored.status == ProcessingStatus.DUPLICATE.value:
            return stored
        if len(raw_message) > self.settings.max_raw_email_bytes:
            return self._quarantine(
                stored.email_message_id,
                category="raw_email_size_limit",
                safe_message="Raw email exceeds MAX_RAW_EMAIL_BYTES; manual inspection is required.",
            )
        try:
            parsed = parse_email(
                raw_message,
                system_address=self.settings.effective_system_email_address,
                content_limits=self._content_limits,
            )
        except Exception as exc:
            return self._quarantine(
                stored.email_message_id,
                category=type(exc).__name__,
                safe_message="MIME parsing failed; inspect the retained raw email before retrying.",
            )
        applied = self._apply_parsed(stored.email_message_id, parsed)
        if applied.status == ProcessingStatus.DUPLICATE.value:
            return applied
        return self._process_stored(
            stored.email_message_id, parsed, execute_operations=execute_operations
        )

    def retry_stored(self, email_id: uuid.UUID) -> ProcessingResult:
        """Retry a failed or quarantined message without creating another email row."""

        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None:
                raise LookupError(f"stored email {email_id} was not found")
            if email.processing_status not in {
                ProcessingStatus.FAILED.value,
                ProcessingStatus.QUARANTINED.value,
            }:
                return ProcessingResult(
                    email.id,
                    email.processing_status,
                    conversation_id=email.conversation_id,
                    detail="only failed or quarantined messages are retried",
                )
            if email.raw_eml_blob is not None:
                raw_message = email.raw_eml_blob
            elif email.raw_eml_path:
                raw_message = Path(email.raw_eml_path).read_bytes()
            else:
                raise RuntimeError("failed email has no recoverable raw MIME source")
            email.processing_status = ProcessingStatus.STORED.value
            email.quarantine_retry_count += 1
            email.quarantine_category = None
            email.quarantine_message = None
            email.quarantined_at = None
        if len(raw_message) > self.settings.max_raw_email_bytes:
            return self._quarantine(
                email_id,
                category="raw_email_size_limit",
                safe_message="Raw email still exceeds MAX_RAW_EMAIL_BYTES.",
            )
        try:
            parsed = parse_email(
                raw_message,
                system_address=self.settings.effective_system_email_address,
                content_limits=self._content_limits,
            )
        except Exception as exc:
            return self._quarantine(
                email_id,
                category=type(exc).__name__,
                safe_message="MIME parsing failed again; inspect the retained raw email.",
            )
        applied = self._apply_parsed(email_id, parsed)
        if applied.status == ProcessingStatus.DUPLICATE.value:
            return applied
        return self._process_stored(email_id, parsed)

    def _process_stored(
        self, email_id: uuid.UUID, parsed: ParsedEmail, *, execute_operations: bool = True
    ) -> ProcessingResult:
        try:
            prepared_or_result = self._prepare(email_id, parsed)
            if isinstance(prepared_or_result, ProcessingResult):
                return prepared_or_result
            prepared = prepared_or_result
            self._cancel_pending_execution_for_follow_up(prepared)
            try:
                analysis_result = self.analyzer.analyze(prepared.context)
            except Exception as exc:
                with session_scope(self.session_factory) as session:
                    persist_failed_model_run(
                        session,
                        stage="analysis",
                        prompt_version=self.analyzer.prompt_version,
                        input_context=prepared.context,
                        email_message_id=prepared.email_id,
                        model_name=self.analyzer.config.model,
                        backend=str(
                            getattr(
                                self.analyzer.backend,
                                "backend_name",
                                type(self.analyzer.backend).__name__,
                            )
                        ),
                        error=exc,
                        quantization=self.settings.model_quantization or None,
                        generation_settings=safe_generation_settings(self.analyzer.config),
                        base_model_id=self.analyzer.config.base_model,
                        resolved_model_id=self.analyzer.config.model,
                        requested_route=self.analyzer.config.model,
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
            self._cancel_pending_execution_for_follow_up(prepared, analysis=analysis)
            if safety.reasons:
                with session_scope(self.session_factory) as session:
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
            work, request_ids, early = self._materialize_analysis(
                prepared, parsed, analysis, analysis_result
            )
            if early is not None:
                return early
            execute_ids, decisions = self._verify_and_decide(prepared, parsed, analysis, work)
            for operation_id in execute_ids if execute_operations else []:
                outcome = self._execute_or_schedule(prepared.email_id, operation_id)
                if outcome is None:
                    continue
                if outcome.status != ExecutionStatus.SUCCEEDED:
                    self._record_execution_escalation(
                        email_id=prepared.email_id,
                        operation_id=operation_id,
                        outcome=outcome,
                    )
                    decisions.append(FinalDecision.ESCALATE.value)
            return self._finalize(
                email_id=prepared.email_id,
                request_ids=request_ids,
                decisions=decisions,
                clarification_id=prepared.clarification_id,
            )
        except Exception as exc:
            LOGGER.exception(
                "inbound processing failed",
                extra={"email_message_id": str(email_id)},
            )
            self._mark_failed(email_id, str(exc))
            return ProcessingResult(
                email_message_id=email_id,
                status=ProcessingStatus.FAILED.value,
                detail=str(exc),
            )

    def _cancel_pending_execution_for_follow_up(
        self,
        prepared: _Prepared,
        *,
        analysis: EmailAnalysis | None = None,
    ) -> int:
        """Cancel a correlated queued revision before a correction can race dispatch."""

        if (
            prepared.request_id is None
            or prepared.correlation.strength != CorrelationStrength.STRONG
        ):
            return 0
        latest = str(prepared.context.get("latest_user_message", ""))
        explicit = _EXPLICIT_CORRECTION_OR_CANCELLATION_RE.search(latest) is not None
        message_kind = analysis.message_kind if analysis is not None else None
        model_supported = message_kind in {
            "correction",
            "cancellation",
        }
        if not explicit and not model_supported:
            return 0
        count = self.execution_service.cancel_scheduled_for_request(
            prepared.request_id,
            reason=(
                "explicit_same_thread_correction_or_cancellation"
                if explicit
                else f"analyzer_message_kind:{message_kind}"
            ),
            source_email_id=prepared.email_id,
        )
        if count:
            with session_scope(self.session_factory) as session:
                email = session.get(EmailMessage, prepared.email_id)
                if email is not None:
                    metadata = dict(email.context_limit_metadata)
                    metadata["cancelled_scheduled_execution_count"] = count
                    metadata["scheduled_execution_cancellation_reason"] = (
                        "same_thread_correction_or_cancellation"
                    )
                    email.context_limit_metadata = metadata
        return count

    def _execute_or_schedule(
        self,
        email_id: uuid.UUID,
        operation_id: uuid.UUID,
    ) -> ExecutionOutcome | None:
        grace_seconds = self.settings.execution_correction_grace_seconds
        if grace_seconds == 0:
            return self.execution_service.execute(operation_id)
        self.execution_service.schedule(
            operation_id,
            source_email_id=email_id,
            not_before=utc_now() + timedelta(seconds=grace_seconds),
        )
        return None

    def dispatch_due_scheduled_executions(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> list[ScheduledDispatchOutcome]:
        """Dispatch durable grace items and finalize their request/outbox state."""

        outcomes = self.execution_service.dispatch_due(now=now, limit=limit)
        for outcome in outcomes:
            with session_scope(self.session_factory) as session:
                scheduled = session.get(ScheduledExecution, outcome.scheduled_execution_id)
                if scheduled is None:
                    continue
                source_email_id = scheduled.source_email_id
                operation_id = scheduled.operation_id
                request_id = scheduled.request_id
            if (
                outcome.execution is not None
                and outcome.execution.status != ExecutionStatus.SUCCEEDED
            ):
                self._record_execution_escalation(
                    email_id=source_email_id,
                    operation_id=operation_id,
                    outcome=outcome.execution,
                )
            elif outcome.status == ScheduledExecutionStatus.FAILED:
                with session_scope(self.session_factory) as session:
                    email = session.get(EmailMessage, source_email_id)
                    operation = session.get(Operation, operation_id)
                    request = session.get(BusinessRequest, request_id)
                    if email is not None and operation is not None:
                        if OperationStatus(operation.status) != OperationStatus.COMPLETED:
                            operation.status = OperationStatus.ESCALATED.value
                            operation.execution_eligible = False
                        create_escalation(
                            session,
                            email=email,
                            request=request,
                            recipient=self.settings.escalation_recipient,
                            reason_code="scheduled_execution_dispatch_failure",
                            summary="The durable execution queue could not dispatch the operation.",
                            evidence={
                                "scheduled_execution_id": str(outcome.scheduled_execution_id),
                                "operation_id": str(operation_id),
                                "detail": outcome.detail,
                            },
                            queue_email=self.settings.outbound_email_enabled,
                            sender_address=self.settings.smtp_from_address,
                        )
            if outcome.execution is not None or outcome.status == ScheduledExecutionStatus.FAILED:
                self._finalize(
                    email_id=source_email_id,
                    request_ids=[request_id],
                    decisions=[],
                    clarification_id=None,
                )
        return outcomes

    def _record_execution_escalation(
        self,
        *,
        email_id: uuid.UUID,
        operation_id: uuid.UUID,
        outcome: ExecutionOutcome,
    ) -> None:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            operation = session.get(Operation, operation_id)
            execution = session.get(Execution, outcome.execution_id)
            if email is None or operation is None:
                raise LookupError("execution escalation aggregate disappeared")
            request = session.get(BusinessRequest, operation.request_id)
            reason_code = (
                "business_api_unknown_outcome"
                if outcome.status == ExecutionStatus.UNKNOWN
                else "business_api_failure"
            )
            create_escalation(
                session,
                email=email,
                request=request,
                recipient=self.settings.escalation_recipient,
                reason_code=reason_code,
                summary=(
                    "The business API outcome is unknown; automatic retry is prohibited."
                    if outcome.status == ExecutionStatus.UNKNOWN
                    else "The business API rejected or failed the operation."
                ),
                evidence={
                    "operation_id": str(operation.id),
                    "operation_action": operation.action,
                    "execution_id": str(outcome.execution_id),
                    "execution_status": outcome.status.value,
                    "detail": outcome.detail,
                    "endpoint": execution.endpoint if execution else None,
                    "request_payload": execution.request_payload if execution else {},
                    "response_status": execution.response_status if execution else None,
                    "response_body": execution.response_body if execution else {},
                    "attempt_count": execution.attempt_count if execution else None,
                    "recommended_action": (
                        "Reconcile the idempotency key with the remote system before any retry."
                        if outcome.status == ExecutionStatus.UNKNOWN
                        else "Review the response and complete the operation manually if appropriate."
                    ),
                },
                queue_email=self.settings.outbound_email_enabled,
                sender_address=self.settings.smtp_from_address,
            )

    def _raw_path(self, email_id: uuid.UUID, raw_message: bytes) -> str | None:
        if not self.settings.store_raw_eml:
            return None
        directory = Path(self.settings.raw_eml_directory)
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"{email_id}.eml"
        target.write_bytes(raw_message)
        return str(target)

    def _store_raw_minimal(self, raw_message: bytes, identity: InboundIdentity) -> ProcessingResult:
        digest = hashlib.sha256(raw_message).hexdigest()
        with session_scope(self.session_factory) as session:
            emails = EmailRepository(session)
            if (
                identity.account_id
                and identity.mailbox is not None
                and identity.uidvalidity is not None
                and identity.uid is not None
            ):
                existing_physical = emails.by_physical_locator(
                    identity.account_id, identity.mailbox, identity.uidvalidity, identity.uid
                )
                if existing_physical:
                    return ProcessingResult(
                        email_message_id=existing_physical.id,
                        status=ProcessingStatus.DUPLICATE.value,
                        conversation_id=existing_physical.conversation_id,
                        duplicate_of_id=existing_physical.id,
                        detail="physical IMAP locator already stored",
                    )
                account = session.get(MailAccount, identity.account_id)
                if account is not None:
                    account.last_uidvalidity = identity.uidvalidity
                    account.polling_checkpoint = max(account.polling_checkpoint or 0, identity.uid)

            logical_duplicate = emails.by_raw_sha256(digest)
            email_id = uuid.uuid4()
            path = self._raw_path(email_id, raw_message)
            message = EmailMessage(
                id=email_id,
                mail_account_id=identity.account_id,
                conversation_id=logical_duplicate.conversation_id if logical_duplicate else None,
                direction=Direction.INBOUND.value,
                raw_eml_path=path,
                raw_eml_blob=None if path else raw_message,
                raw_sha256=digest,
                raw_size_bytes=len(raw_message),
                imap_uid=identity.uid,
                uidvalidity=identity.uidvalidity,
                mailbox_name=identity.mailbox,
                flags_json=list(identity.flags),
                provider_metadata_json=dict(identity.provider_metadata),
                internal_date=identity.internal_date,
                processing_status=(
                    ProcessingStatus.DUPLICATE.value
                    if logical_duplicate
                    else ProcessingStatus.STORED.value
                ),
                duplicate_of_id=logical_duplicate.id if logical_duplicate else None,
            )
            emails.add(message)
            if logical_duplicate:
                return ProcessingResult(
                    email_message_id=message.id,
                    status=ProcessingStatus.DUPLICATE.value,
                    conversation_id=message.conversation_id,
                    duplicate_of_id=logical_duplicate.id,
                    detail="logical message was already stored",
                )
            return ProcessingResult(message.id, ProcessingStatus.STORED.value)

    def _apply_parsed(self, email_id: uuid.UUID, parsed: ParsedEmail) -> ProcessingResult:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None:
                raise LookupError(f"stored email {email_id} disappeared")
            duplicate = None
            if parsed.normalized_message_id:
                duplicate = session.scalar(
                    select(EmailMessage)
                    .where(
                        EmailMessage.id != email.id,
                        EmailMessage.normalized_message_id == parsed.normalized_message_id,
                        EmailMessage.duplicate_of_id.is_(None),
                    )
                    .order_by(EmailMessage.created_at)
                )
            if duplicate is None and parsed.segmentation.latest_message_candidate:
                logical_candidates = session.scalars(
                    select(EmailMessage)
                    .where(
                        EmailMessage.id != email.id,
                        EmailMessage.direction == Direction.INBOUND.value,
                        EmailMessage.duplicate_of_id.is_(None),
                        EmailMessage.normalized_subject == parsed.normalized_subject,
                        EmailMessage.latest_user_message
                        == parsed.segmentation.latest_message_candidate,
                    )
                    .order_by(EmailMessage.created_at)
                )
                duplicate = next(
                    (
                        candidate
                        for candidate in logical_candidates
                        if bare_address(candidate.sender) == parsed.sender_address
                    ),
                    None,
                )
            email.rfc_message_id = parsed.rfc_message_id
            email.normalized_message_id = parsed.normalized_message_id
            email.in_reply_to = parsed.in_reply_to
            email.references_json = parsed.references
            email.sender = parsed.sender
            email.reply_to = parsed.reply_to
            email.recipients_json = parsed.recipients
            email.cc_json = parsed.cc
            email.subject = parsed.subject
            email.normalized_subject = parsed.normalized_subject
            email.raw_text = parsed.text_body
            email.latest_user_message = parsed.segmentation.latest_message_candidate
            email.quoted_text = parsed.segmentation.quoted_thread_candidate
            email.signature_text = parsed.segmentation.signature_candidate
            email.html_body = parsed.html_body
            email.mime_type = parsed.mime_type
            email.attachment_metadata = parsed.attachment_metadata
            email.message_date = parsed.message_date
            email.automated_classification = parsed.automated_classification
            email.parsing_warnings = parsed.parsing_warnings
            parse_limit_warnings = [
                warning for warning in parsed.parsing_warnings if "limit_exceeded" in warning
            ]
            email.context_limit_metadata = {"mime_limit_warnings": parse_limit_warnings}
            email.quarantine_category = None
            email.quarantine_message = None
            email.quarantined_at = None
            if duplicate is not None:
                email.processing_status = ProcessingStatus.DUPLICATE.value
                email.duplicate_of_id = duplicate.id
                email.conversation_id = duplicate.conversation_id
                return ProcessingResult(
                    email_message_id=email.id,
                    status=ProcessingStatus.DUPLICATE.value,
                    conversation_id=email.conversation_id,
                    duplicate_of_id=duplicate.id,
                    detail="logical message was already stored",
                )
            email.processing_status = ProcessingStatus.STORED.value
            return ProcessingResult(email.id, ProcessingStatus.STORED.value)

    def _quarantine(
        self, email_id: uuid.UUID, *, category: str, safe_message: str
    ) -> ProcessingResult:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None:
                raise LookupError(f"stored email {email_id} disappeared")
            email.processing_status = ProcessingStatus.QUARANTINED.value
            email.quarantine_category = category[:100]
            email.quarantine_message = safe_message[:2000]
            email.quarantined_at = utc_now()
            warnings = list(email.parsing_warnings)
            warning = f"quarantined:{category[:100]}"
            if warning not in warnings:
                warnings.append(warning)
            email.parsing_warnings = warnings
            return ProcessingResult(
                email_message_id=email.id,
                status=ProcessingStatus.QUARANTINED.value,
                conversation_id=email.conversation_id,
                detail=safe_message,
            )

    def _prepare(self, email_id: uuid.UUID, parsed: ParsedEmail) -> _Prepared | ProcessingResult:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None:
                raise LookupError(f"stored email {email_id} disappeared")
            authorization = self.authorizer.authorize(parsed.sender)
            email.authorization_allowed = authorization.authorized
            email.authorization_reason = authorization.reason
            if parsed.automated_classification:
                email.processing_status = ProcessingStatus.IGNORED.value
                return ProcessingResult(
                    email.id,
                    ProcessingStatus.IGNORED.value,
                    detail=parsed.automated_classification,
                )
            if not authorization.authorized:
                LOGGER.info(
                    "escalating email from non-whitelisted sender",
                    extra={
                        "email_message_id": str(email.id),
                        "sender": parsed.sender_address,
                        "authorization_reason": authorization.reason,
                    },
                )
                create_escalation(
                    session,
                    email=email,
                    request=None,
                    recipient=self.settings.escalation_recipient,
                    reason_code="sender_not_whitelisted",
                    summary="The sender is not authorized for automatic SNOC processing.",
                    evidence={
                        "authorization_allowed": False,
                        "authorization_reason": authorization.reason,
                        "sender": parsed.sender_address,
                        "recommended_action": "Review the forwarded client email manually.",
                    },
                    queue_email=self.settings.outbound_email_enabled,
                    sender_address=self.settings.smtp_from_address,
                )
                email.processing_status = ProcessingStatus.PROCESSED.value
                return ProcessingResult(
                    email.id,
                    ProcessingStatus.PROCESSED.value,
                    decisions=[FinalDecision.ESCALATE.value],
                    detail="sender_not_whitelisted_escalated",
                )

            correlation = correlate_email(session, parsed)
            email.correlation_details = {
                "strength": correlation.strength.value,
                "matched_by": correlation.matched_by,
                "request_id": correlation.request_id,
                "clarification_id": correlation.clarification_id,
                "conflicts": correlation.conflicts,
            }
            conversation_id = uuid_or_none(correlation.conversation_id)
            conversation = (
                ConversationRepository(session).get(conversation_id) if conversation_id else None
            )
            if conversation is None:
                conversation = Conversation(
                    normalized_subject=parsed.normalized_subject,
                    primary_sender=parsed.sender_address,
                    last_message_at=parsed.message_date or utc_now(),
                )
                ConversationRepository(session).add(conversation)
                conversation.root_message_id = email.id
            else:
                conversation.last_message_at = parsed.message_date or utc_now()
            email.conversation_id = conversation.id
            LOGGER.info(
                "email correlation selected",
                extra={
                    "email_message_id": str(email.id),
                    "rfc_message_id": email.rfc_message_id,
                    "conversation_id": str(conversation.id),
                    "request_id": correlation.request_id,
                    "clarification_id": correlation.clarification_id,
                    "correlation_strength": correlation.strength.value,
                },
            )

            if correlation.strength == CorrelationStrength.CONFLICT or (
                correlation.strength == CorrelationStrength.WEAK and correlation.conflicts
            ):
                create_escalation(
                    session,
                    email=email,
                    request=None,
                    recipient=self.settings.escalation_recipient,
                    reason_code="request_correlation_conflict",
                    summary="Independent request-correlation signals conflict or match several open requests.",
                    evidence=email.correlation_details,
                    queue_email=self.settings.outbound_email_enabled,
                    sender_address=self.settings.smtp_from_address,
                )
                email.processing_status = ProcessingStatus.PROCESSED.value
                return ProcessingResult(
                    email.id,
                    ProcessingStatus.PROCESSED.value,
                    conversation_id=conversation.id,
                    decisions=[FinalDecision.ESCALATE.value],
                    detail="correlation conflict",
                )

            request_id = uuid_or_none(correlation.request_id)
            clarification_id = uuid_or_none(correlation.clarification_id)
            if clarification_id:
                clarification = ClarificationRepository(session).get(clarification_id)
                if clarification is None:
                    raise LookupError("correlated clarification no longer exists")
                request = RequestRepository(session).get(clarification.request_id)
                if request is None:
                    raise LookupError("clarification request no longer exists")
                operations = [
                    operation
                    for raw_id in clarification.target_operation_ids
                    if (operation := OperationRepository(session).get(uuid.UUID(raw_id)))
                    is not None
                ]
                context = self.context_builder.clarification_reply(
                    parsed,
                    request_reference=request.public_reference,
                    previous_agent_question=clarification.question_text,
                    operations=[
                        OperationSnapshot(
                            operation_id=str(operation.id),
                            action=canonical_action(operation.action),
                            pdv_code=operation.pdv_code,
                            phone=operation.phone,
                            missing_fields=list(operation.missing_fields),
                            additional_payload=dict(operation.additional_payload),
                        )
                        for operation in operations
                    ],
                )
                request_id = request.id
            elif correlation.strength == CorrelationStrength.WEAK:
                open_requests = RequestRepository(session).open_for_conversation(conversation.id)
                context = self.context_builder.possible_follow_up(
                    parsed,
                    [
                        {
                            "request_id": str(request.id),
                            "request_reference": request.public_reference,
                            "status": request.status,
                            "operations": [
                                {
                                    "operation_id": str(operation.id),
                                    "action": operation.action,
                                    "missing_fields": operation.missing_fields,
                                }
                                for operation in request.operations
                            ],
                        }
                        for request in open_requests
                    ],
                )
            elif request_id:
                request = RequestRepository(session).get(request_id)
                if request is None:
                    raise LookupError("correlated request no longer exists")
                context = self.context_builder.correlated_request_reply(
                    parsed,
                    request_reference=request.public_reference,
                    request_status=request.status,
                    operations=[
                        OperationSnapshot(
                            operation_id=str(operation.id),
                            action=canonical_action(operation.action),
                            pdv_code=operation.pdv_code,
                            phone=operation.phone,
                            missing_fields=list(operation.missing_fields),
                            additional_payload=dict(operation.additional_payload),
                        )
                        for operation in request.operations
                    ],
                )
            else:
                context = self.context_builder.new_request(parsed)
            context_warnings = list(context.get("context_limit_warnings", []))
            mime_warnings = list(email.context_limit_metadata.get("mime_limit_warnings", []))
            if mime_warnings:
                context["automatic_execution_allowed"] = False
                context["mime_limit_warnings"] = mime_warnings
            email.context_limit_metadata = {
                "mime_limit_warnings": mime_warnings,
                "context_limit_warnings": context_warnings,
                "automatic_execution_allowed": not (mime_warnings or context_warnings),
            }
            if context_warnings:
                warnings = list(email.parsing_warnings)
                for warning in context_warnings:
                    if warning not in warnings:
                        warnings.append(warning)
                email.parsing_warnings = warnings
            email.processing_status = ProcessingStatus.PROCESSING.value
            return _Prepared(
                email.id,
                conversation.id,
                correlation,
                context,
                request_id,
                clarification_id,
            )

    def _create_request(
        self,
        session: Session,
        *,
        conversation_id: uuid.UUID,
        email_id: uuid.UUID,
        kind: RequestKind,
    ) -> BusinessRequest:
        request = BusinessRequest(
            public_reference=generate_request_reference(),
            conversation_id=conversation_id,
            initiating_email_id=email_id,
            status=RequestStatus.ANALYZING.value,
            request_kind=kind.value,
        )
        return RequestRepository(session).add(request)

    @staticmethod
    def _new_operation(
        session: Session,
        *,
        request: BusinessRequest,
        proposal: ProposedOperation,
        sequence: int,
        source_email: EmailMessage,
        model_run_id: uuid.UUID,
    ) -> Operation:
        operation = Operation(
            request_id=request.id,
            sequence_number=sequence,
            action=canonical_action(proposal.action).value,
            status=OperationStatus.NEW.value,
            pdv_code=normalize_numeric(proposal.pdv_code),
            phone=normalize_numeric(proposal.phone, keep_leading_plus=True),
            additional_payload=dict(proposal.additional_fields),
            missing_fields=list(proposal.missing_fields),
            evidence=[evidence.model_dump(mode="json") for evidence in proposal.evidence],
            field_provenance={
                evidence.field_name: evidence.source for evidence in proposal.evidence
            },
            analyzer_confidence={
                "raw_action_confidence": proposal.raw_action_confidence,
                "raw_field_confidence": proposal.raw_field_confidence,
            },
            contradiction_data={},
            current_revision=1,
            execution_eligible=False,
        )
        session.add(operation)
        session.flush()
        for field_name, value in (
            ("pdv_code", operation.pdv_code),
            ("phone", operation.phone),
        ):
            if value is not None:
                session.add(
                    FieldRevision(
                        operation_id=operation.id,
                        field_name=field_name,
                        old_value=None,
                        new_value=value,
                        source_email_id=source_email.id,
                        model_run_id=model_run_id,
                        reason="initial_extraction",
                    )
                )
        operation.missing_fields = effective_missing_fields(operation)
        return operation

    def _single_field_clarification_proposal(
        self,
        prepared: _Prepared,
        operation: Operation,
    ) -> ProposedOperation | None:
        """Reconcile one unambiguous phone candidate into one open target operation."""

        if OperationStatus(operation.status) in {
            OperationStatus.COMPLETED,
            OperationStatus.CANCELLED,
        }:
            return None
        action = canonical_action(operation.action)
        expected_missing = effective_missing_fields(operation)
        if expected_missing not in (["phone"], ["new_phone"]):
            return None
        raw_candidates = prepared.context.get("numeric_candidates_from_latest_reply", [])
        phone_candidates = {
            normalized
            for item in raw_candidates
            if isinstance(item, dict)
            and item.get("kind_hint") == "phone_or_unknown"
            and (
                normalized := normalize_numeric(
                    str(item.get("value", "")),
                    keep_leading_plus=True,
                )
            )
        }
        if len(phone_candidates) != 1:
            return None
        phone = next(iter(phone_candidates))
        stored_pdv = normalize_numeric(operation.pdv_code)
        pdv_candidates = {
            normalized
            for item in raw_candidates
            if isinstance(item, dict)
            and item.get("kind_hint") == "pdv_or_unknown"
            and (
                normalized := normalize_numeric(
                    str(item.get("value", "")),
                )
            )
        }
        if pdv_candidates and (stored_pdv is None or pdv_candidates != {stored_pdv}):
            return None
        allowed_values = {phone}
        if stored_pdv is not None:
            allowed_values.add(stored_pdv)
        candidate_values = {
            normalized
            for item in raw_candidates
            if isinstance(item, dict)
            and (
                normalized := normalize_numeric(
                    str(item.get("value", "")),
                    keep_leading_plus=True,
                )
            )
        }
        if not candidate_values.issubset(allowed_values):
            return None
        field_check = validate_operation_fields(
            action=action,
            pdv_code=operation.pdv_code,
            phone=phone,
            pdv_pattern=self.settings.pdv_pattern,
            phone_pattern=self.settings.phone_pattern,
        )
        if not field_check.passed:
            return None
        phone_field = "new_phone" if expected_missing == ["new_phone"] else "phone"
        evidence = [
            FieldEvidence(
                field_name="pdv_code",
                value=operation.pdv_code,
                source="stored_request_state",
                evidence_text=f"stored PDV {operation.pdv_code}",
                support="supported",
            ),
            FieldEvidence(
                field_name=phone_field,
                value=phone,
                source="latest_user_message",
                evidence_text=phone,
                support="supported",
            ),
        ]
        return ProposedOperation(
            local_operation_id=str(operation.id),
            action=action.value,
            pdv_code=operation.pdv_code,
            phone=phone,
            missing_fields=[],
            evidence=evidence,
        )

    @staticmethod
    def _clarification_proposal_matches_canonical(
        proposal: ProposedOperation,
        canonical: ProposedOperation,
    ) -> bool:
        """Reject model conflicts before using deterministic clarification state."""

        if canonical_action(proposal.action) != canonical_action(canonical.action):
            return False
        if proposal.additional_fields or proposal.ambiguity_reasons:
            return False
        proposed_pdv = normalize_numeric(proposal.pdv_code)
        proposed_phone = normalize_numeric(proposal.phone, keep_leading_plus=True)
        canonical_pdv = normalize_numeric(canonical.pdv_code)
        canonical_phone = normalize_numeric(canonical.phone, keep_leading_plus=True)
        return proposed_pdv in {None, canonical_pdv} and proposed_phone in {
            None,
            canonical_phone,
        }

    def _materialize_analysis(
        self,
        prepared: _Prepared,
        parsed: ParsedEmail,
        analysis: EmailAnalysis,
        analysis_result: Any,
    ) -> tuple[list[_OperationWork], list[uuid.UUID], ProcessingResult | None]:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, prepared.email_id)
            if email is None:
                raise LookupError("email missing during analysis persistence")
            run = persist_model_run(
                session,
                result=analysis_result,
                stage="analysis",
                prompt_version=self.analyzer.prompt_version,
                input_context=prepared.context,
                email_message_id=email.id,
                quantization=self.settings.model_quantization or None,
                generation_settings=safe_generation_settings(self.analyzer.config),
            )
            if (
                analysis.message_kind in {"irrelevant", "automated"}
                and prepared.clarification_id is None
            ):
                email.processing_status = ProcessingStatus.IGNORED.value
                return (
                    [],
                    [],
                    ProcessingResult(
                        email.id,
                        ProcessingStatus.IGNORED.value,
                        conversation_id=prepared.conversation_id,
                        decisions=[FinalDecision.IGNORE.value],
                        detail=analysis.message_kind,
                    ),
                )

            work: list[_OperationWork] = []
            request_ids: list[uuid.UUID] = []
            proposals_remaining = list(analysis.operations)
            routing_analysis = analysis
            uncorrelated_model_correction = (
                prepared.correlation.strength == CorrelationStrength.NEW
                and prepared.request_id is None
                and analysis.message_kind == "correction"
            )
            if uncorrelated_model_correction:
                routing_analysis = analysis.model_copy(
                    update={
                        "message_kind": "new_request",
                        "referenced_existing_operation_ids": [],
                        "contradiction_with_stored_state": False,
                        "contradiction_details": [],
                    }
                )
            independent_request_in_completed_thread = False
            if (
                prepared.clarification_id is None
                and prepared.request_id is not None
                and analysis.new_request_present
                and _has_explicit_independent_request(
                    str(prepared.context.get("latest_user_message", ""))
                )
            ):
                correlated_request = session.get(BusinessRequest, prepared.request_id)
                independent_request_in_completed_thread = bool(
                    correlated_request
                    and correlated_request.status == RequestStatus.COMPLETED.value
                )
                if independent_request_in_completed_thread:
                    routing_analysis = analysis.model_copy(
                        update={
                            "message_kind": "new_request",
                            "referenced_existing_operation_ids": [],
                            "contradiction_with_stored_state": False,
                            "contradiction_details": [],
                        }
                    )

            if prepared.clarification_id:
                clarification = session.get(Clarification, prepared.clarification_id)
                if clarification is None:
                    raise LookupError("clarification disappeared")
                request = session.get(BusinessRequest, clarification.request_id)
                if request is None:
                    raise LookupError("clarification request disappeared")
                request_ids.append(request.id)
                targets = [
                    operation
                    for raw_id in clarification.target_operation_ids
                    if (operation := session.get(Operation, uuid.UUID(raw_id))) is not None
                ]
                deterministic_proposal_ids: set[str] = set()
                if len(targets) == 1 and len(proposals_remaining) <= 1:
                    deterministic_proposal = self._single_field_clarification_proposal(
                        prepared,
                        targets[0],
                    )
                    model_proposal = proposals_remaining[0] if proposals_remaining else None
                    if deterministic_proposal is not None and (
                        model_proposal is None
                        or self._clarification_proposal_matches_canonical(
                            model_proposal,
                            deterministic_proposal,
                        )
                    ):
                        proposals_remaining = [deterministic_proposal]
                        deterministic_proposal_ids.add(deterministic_proposal.local_operation_id)
                matched_proposals: set[int] = set()
                for operation in targets:
                    proposal_index = next(
                        (
                            index
                            for index, proposal in enumerate(proposals_remaining)
                            if proposal.local_operation_id == str(operation.id)
                        ),
                        None,
                    )
                    if proposal_index is None and len(targets) == len(proposals_remaining) == 1:
                        proposal_index = 0
                    if proposal_index is None:
                        continue
                    proposal = proposals_remaining[proposal_index]
                    matched_proposals.add(proposal_index)
                    if canonical_action(proposal.action).value != operation.action:
                        operation.contradiction_data = {
                            "type": "action_changed_in_clarification",
                            "proposed_action": proposal.action,
                        }
                    elif OperationStatus(operation.status) in {
                        OperationStatus.COMPLETED,
                        OperationStatus.CANCELLED,
                    }:
                        operation.contradiction_data = {
                            "type": "terminal_operation_change_proposed",
                            "proposal": proposal.model_dump(mode="json"),
                        }
                    else:
                        apply_proposal_fields(
                            session,
                            operation=operation,
                            proposal=proposal,
                            source_email=email,
                            reason="clarification",
                            model_run_id=run.id,
                        )
                        operation.evidence = [
                            evidence.model_dump(mode="json") for evidence in proposal.evidence
                        ]
                        operation.field_provenance = {
                            evidence.field_name: evidence.source for evidence in proposal.evidence
                        }
                    work.append(
                        _OperationWork(
                            operation.id,
                            request.id,
                            proposal,
                            CorrelationStrength.STRONG,
                            may_use_stored_state=True,
                            analysis_override=(
                                analysis.model_copy(
                                    update={
                                        "message_kind": "clarification_reply",
                                        "referenced_existing_operation_ids": [str(operation.id)],
                                        "operations": [proposal],
                                        "new_request_present": False,
                                        "contradiction_with_stored_state": False,
                                        "contradiction_details": [],
                                        "unresolved_ambiguities": [],
                                    }
                                )
                                if proposal.local_operation_id in deterministic_proposal_ids
                                else None
                            ),
                        )
                    )
                proposals_remaining = [
                    proposal
                    for index, proposal in enumerate(proposals_remaining)
                    if index not in matched_proposals
                ]
                clarification.reply_email_id = email.id

            elif (
                analysis.message_kind == "correction"
                and prepared.request_id
                and not independent_request_in_completed_thread
            ):
                request = session.get(BusinessRequest, prepared.request_id)
                if request:
                    request_ids.append(request.id)
                    existing = list(request.operations)
                    matched: set[uuid.UUID] = set()
                    for proposal in proposals_remaining:
                        operation = next(
                            (
                                candidate
                                for candidate in existing
                                if candidate.id not in matched
                                and (
                                    proposal.local_operation_id == str(candidate.id)
                                    or canonical_action(proposal.action).value == candidate.action
                                )
                            ),
                            None,
                        )
                        if operation is None:
                            continue
                        matched.add(operation.id)
                        if canonical_action(proposal.action).value != operation.action:
                            operation.contradiction_data = {
                                "type": "action_changed_in_correction",
                                "proposed_action": proposal.action,
                            }
                        elif OperationStatus(operation.status) in {
                            OperationStatus.COMPLETED,
                            OperationStatus.CANCELLED,
                        }:
                            operation.contradiction_data = {
                                "type": "terminal_operation_change_proposed",
                                "proposal": proposal.model_dump(mode="json"),
                            }
                        else:
                            apply_proposal_fields(
                                session,
                                operation=operation,
                                proposal=proposal,
                                source_email=email,
                                reason="correction",
                                model_run_id=run.id,
                            )
                        work.append(
                            _OperationWork(
                                operation.id,
                                request.id,
                                proposal,
                                prepared.correlation.strength,
                                may_use_stored_state=True,
                            )
                        )
                    proposals_remaining = []

            if proposals_remaining:
                kind = (
                    RequestKind.MIXED
                    if routing_analysis.message_kind == "mixed"
                    else RequestKind.CORRECTION
                    if routing_analysis.message_kind == "correction"
                    else RequestKind.NEW
                )
                request = self._create_request(
                    session,
                    conversation_id=prepared.conversation_id,
                    email_id=email.id,
                    kind=kind,
                )
                request_ids.append(request.id)
                proposal_correlation = (
                    CorrelationStrength.WEAK
                    if prepared.correlation.strength == CorrelationStrength.WEAK
                    else CorrelationStrength.NEW
                )
                for sequence, proposal in enumerate(proposals_remaining, 1):
                    operation = self._new_operation(
                        session,
                        request=request,
                        proposal=proposal,
                        sequence=sequence,
                        source_email=email,
                        model_run_id=run.id,
                    )
                    work.append(
                        _OperationWork(
                            operation.id,
                            request.id,
                            proposal,
                            proposal_correlation,
                            analysis_override=(
                                routing_analysis
                                if (
                                    uncorrelated_model_correction
                                    or independent_request_in_completed_thread
                                )
                                else None
                            ),
                        )
                    )

            if not work:
                request = session.get(BusinessRequest, request_ids[0]) if request_ids else None
                if request is None:
                    request = self._create_request(
                        session,
                        conversation_id=prepared.conversation_id,
                        email_id=email.id,
                        kind=RequestKind.NEW,
                    )
                    request_ids.append(request.id)
                request.status = RequestStatus.ESCALATED.value
                request.last_active_at = utc_now()
                request.version += 1
                create_escalation(
                    session,
                    email=email,
                    request=request,
                    recipient=self.settings.escalation_recipient,
                    reason_code="no_separable_operation",
                    summary="The analyzer did not produce a safely separable operation.",
                    evidence={"analysis": analysis.model_dump(mode="json")},
                    queue_email=self.settings.outbound_email_enabled,
                    sender_address=self.settings.smtp_from_address,
                )
                email.processing_status = ProcessingStatus.PROCESSED.value
                return (
                    [],
                    request_ids,
                    ProcessingResult(
                        email.id,
                        ProcessingStatus.PROCESSED.value,
                        conversation_id=prepared.conversation_id,
                        request_ids=request_ids,
                        decisions=[FinalDecision.ESCALATE.value],
                        detail="no safely separable operation",
                    ),
                )
            return work, request_ids, None

    def _verification_payload(
        self,
        prepared: _Prepared,
        operation: Operation,
        proposal: ProposedOperation,
        correlation_strength: CorrelationStrength,
    ) -> dict[str, Any]:
        return {
            "context_mode": str(prepared.context.get("mode", "new_request")),
            "subject": str(prepared.context.get("subject", "")),
            "latest_user_message": str(prepared.context.get("latest_user_message", "")),
            "stored_operation_state": {
                "operation_id": str(operation.id),
                "action": operation.action,
                "pdv_code": operation.pdv_code,
                "phone": operation.phone,
                "missing_fields": operation.missing_fields,
                "status": operation.status,
                "current_revision": operation.current_revision,
            },
            "proposed_operation": proposal.model_dump(mode="json"),
            "candidate_evidence": prepared.context.get("numeric_candidates")
            or prepared.context.get("numeric_candidates_from_latest_reply", []),
            "correlation_strength": correlation_strength.value,
        }

    @staticmethod
    def _allowed_context_values(
        context: dict[str, Any],
    ) -> tuple[frozenset[str], frozenset[str], frozenset[tuple[str, str]]]:
        raw_candidates = context.get("numeric_candidates") or context.get(
            "numeric_candidates_from_latest_reply", []
        )
        current = {
            normalized
            for item in raw_candidates
            if isinstance(item, dict)
            and (
                normalized := normalize_numeric(str(item.get("value", "")), keep_leading_plus=True)
            )
        }
        stored: set[str] = set()
        stored_additional: set[tuple[str, str]] = set()
        for collection_name in ("target_operations", "stored_operations"):
            operations = context.get(collection_name, [])
            if not isinstance(operations, list):
                continue
            for item in operations:
                if not isinstance(item, dict):
                    continue
                known_fields = item.get("known_fields", {})
                if not isinstance(known_fields, dict):
                    continue
                for value in known_fields.values():
                    normalized = normalize_numeric(
                        str(value) if value is not None else None,
                        keep_leading_plus=True,
                    )
                    if normalized:
                        stored.add(normalized)
                for field_name, value in known_fields.items():
                    if field_name in {"pdv_code", "phone", "new_phone"} or value is None:
                        continue
                    stored_additional.add((str(field_name), str(value).strip().casefold()))
        return frozenset(current), frozenset(stored), frozenset(stored_additional)

    def _verify_and_decide(
        self,
        prepared: _Prepared,
        parsed: ParsedEmail,
        analysis: EmailAnalysis,
        work: list[_OperationWork],
    ) -> tuple[list[uuid.UUID], list[str]]:
        execute_ids: list[uuid.UUID] = []
        decisions: list[str] = []
        (
            current_candidate_values,
            stored_field_values,
            stored_additional_field_values,
        ) = self._allowed_context_values(prepared.context)
        latest_user_message = str(prepared.context.get("latest_user_message", ""))
        request_level_ambiguity = len(work) > 1 and _has_explicit_cross_operation_ambiguity(
            latest_user_message
        )
        request_safety = assess_request_safety(
            subject=parsed.subject,
            latest_user_message=latest_user_message,
            full_visible_body=parsed.text_body,
            correlation_strength=prepared.correlation.strength,
            analysis=analysis,
            parsing_warnings=tuple(parsed.parsing_warnings),
            allow_subject_body_conflict_auto_execution=(
                self.settings.allow_subject_body_conflict_auto_execution
            ),
            allow_forwarded_content_auto_execution=(
                self.settings.allow_forwarded_content_auto_execution
            ),
            allow_untrusted_workflow_marker_auto_execution=(
                self.settings.allow_untrusted_workflow_marker_auto_execution
            ),
        )
        if request_safety.reasons or request_safety.policy_overrides:
            with session_scope(self.session_factory) as session:
                email = session.get(EmailMessage, prepared.email_id)
                if email is not None:
                    metadata = dict(email.context_limit_metadata)
                    metadata["request_safety_reasons"] = list(request_safety.reasons)
                    metadata["request_safety_policy_overrides"] = list(
                        request_safety.policy_overrides
                    )
                    email.context_limit_metadata = metadata
        for item in work:
            with session_scope(self.session_factory) as session:
                operation = session.get(Operation, item.operation_id)
                if operation is None:
                    raise LookupError("operation disappeared before verification")
                verification_payload = self._verification_payload(
                    prepared,
                    operation,
                    item.proposal,
                    item.correlation_strength,
                )
            try:
                verification_result = self.verifier.verify(
                    context_mode=str(verification_payload["context_mode"]),
                    latest_user_message=str(verification_payload["latest_user_message"]),
                    stored_operation_state=dict(verification_payload["stored_operation_state"]),
                    proposed_operation=item.proposal,
                    candidate_evidence=list(verification_payload["candidate_evidence"]),
                    correlation_strength=item.correlation_strength.value,
                    subject=str(verification_payload["subject"]),
                )
                verification = verification_result.parsed
                if not isinstance(verification, SemanticVerification):
                    raise TypeError("verifier returned an unexpected schema")
            except Exception as exc:
                with session_scope(self.session_factory) as session:
                    operation = session.get(Operation, item.operation_id)
                    email = session.get(EmailMessage, prepared.email_id)
                    request = session.get(BusinessRequest, item.request_id)
                    persist_failed_model_run(
                        session,
                        stage="verification",
                        prompt_version=self.verifier.prompt_version,
                        input_context=verification_payload,
                        email_message_id=prepared.email_id,
                        operation_id=item.operation_id,
                        model_name=self.verifier.config.model,
                        backend=str(
                            getattr(
                                self.verifier.backend,
                                "backend_name",
                                type(self.verifier.backend).__name__,
                            )
                        ),
                        error=exc,
                        quantization=self.settings.model_quantization or None,
                        generation_settings=safe_generation_settings(self.verifier.config),
                        base_model_id=self.verifier.config.base_model,
                        resolved_model_id=self.verifier.config.model,
                        requested_route=self.verifier.config.model,
                        json_schema=SemanticVerification.model_json_schema(),
                        schema_name=SemanticVerification.__name__,
                    )
                    if operation and OperationStatus(operation.status) != OperationStatus.COMPLETED:
                        operation.status = OperationStatus.ESCALATED.value
                        operation.final_decision = FinalDecision.ESCALATE.value
                    if email:
                        create_escalation(
                            session,
                            email=email,
                            request=request,
                            recipient=self.settings.escalation_recipient,
                            reason_code="semantic_verifier_failure",
                            summary="Semantic verification failed; execution was refused.",
                            evidence={
                                "error": str(exc),
                                "analysis": analysis.model_dump(mode="json"),
                                "proposal": item.proposal.model_dump(mode="json"),
                            },
                            queue_email=self.settings.outbound_email_enabled,
                            sender_address=self.settings.smtp_from_address,
                        )
                decisions.append(FinalDecision.ESCALATE.value)
                continue

            with session_scope(self.session_factory) as session:
                operation = session.get(Operation, item.operation_id)
                email = session.get(EmailMessage, prepared.email_id)
                request = session.get(BusinessRequest, item.request_id)
                if operation is None or email is None or request is None:
                    raise LookupError("workflow aggregate disappeared during decision")
                verifier_run = persist_model_run(
                    session,
                    result=verification_result,
                    stage="verification",
                    prompt_version=self.verifier.prompt_version,
                    input_context=verification_payload,
                    email_message_id=email.id,
                    operation_id=operation.id,
                    quantization=self.settings.model_quantization or None,
                    generation_settings=safe_generation_settings(self.verifier.config),
                )
                key = ExecutionService.idempotency_key(operation)
                previous_execution = session.scalar(
                    select(Execution).where(Execution.idempotency_key == key)
                )
                decision = self.decision_engine.decide(
                    analysis=item.analysis_override or analysis,
                    proposal=item.proposal,
                    verification=verification,
                    context=DecisionContext(
                        authorized_sender=bool(email.authorization_allowed),
                        correlation_strength=item.correlation_strength,
                        correlation_conflict=prepared.correlation.strength
                        == CorrelationStrength.CONFLICT,
                        structured_output_valid=True,
                        operation_previously_executed=previous_execution is not None,
                        operation_status=OperationStatus(operation.status),
                        api_available=True,
                        execution_explicitly_enabled_or_dry_run=bool(
                            self.settings.dry_run or self.settings.business_api_base_url
                        ),
                        pdv_pattern=self.settings.pdv_pattern,
                        phone_pattern=self.settings.phone_pattern,
                        current_candidate_values=current_candidate_values,
                        stored_field_values=(
                            stored_field_values if item.may_use_stored_state else frozenset()
                        ),
                        enforce_evidence_provenance=self.settings.enforce_evidence_provenance,
                        analyzer_min_raw_confidence=self.settings.analyzer_min_raw_confidence,
                        verifier_min_raw_confidence=self.settings.verifier_min_raw_confidence,
                        vpn_allowed_additional_fields=(
                            self.settings.vpn_allowed_additional_field_set
                        ),
                        expected_existing_action=(
                            canonical_action(operation.action)
                            if item.may_use_stored_state
                            else None
                        ),
                        latest_user_message=latest_user_message,
                        stored_additional_field_values=(
                            stored_additional_field_values
                            if item.may_use_stored_state
                            else frozenset()
                        ),
                        input_context_complete=bool(
                            email.context_limit_metadata.get("automatic_execution_allowed", True)
                        ),
                        request_level_ambiguity=request_level_ambiguity,
                        request_safety_reasons=request_safety.reasons,
                    ),
                )
                operation.verifier_confidence = {
                    "raw_confidence": verification.raw_confidence,
                    "model_run_id": str(verifier_run.id),
                }
                operation.model_agreement = decision.analyzer_verifier_agreement
                operation.final_decision = decision.decision.value
                operation.execution_eligible = decision.decision == FinalDecision.AUTO_EXECUTE
                session.add(
                    ValidationDecision(
                        operation_id=operation.id,
                        analyzer_result=item.proposal.model_dump(mode="json"),
                        verifier_result=verification.model_dump(mode="json"),
                        hard_invariant_results=decision.hard_invariants,
                        decision=decision.decision.value,
                        reasons=list(decision.reasons),
                        policy_version=self.decision_engine.policy_version,
                    )
                )
                if decision.decision == FinalDecision.AUTO_EXECUTE:
                    if OperationStatus(operation.status) != OperationStatus.READY_FOR_VALIDATION:
                        set_operation_status(operation, OperationStatus.READY_FOR_VALIDATION)
                    execute_ids.append(operation.id)
                elif decision.decision == FinalDecision.ASK_FOR_INFORMATION:
                    operation.missing_fields = effective_missing_fields(operation)
                    if OperationStatus(operation.status) != OperationStatus.NEEDS_INFORMATION:
                        set_operation_status(operation, OperationStatus.NEEDS_INFORMATION)
                elif decision.decision in {
                    FinalDecision.ESCALATE,
                    FinalDecision.REVIEW_CORRECTION,
                }:
                    if OperationStatus(operation.status) != OperationStatus.COMPLETED:
                        set_operation_status(operation, OperationStatus.ESCALATED)
                    create_escalation(
                        session,
                        email=email,
                        request=request,
                        recipient=self.settings.escalation_recipient,
                        reason_code=decision.decision.value.casefold(),
                        summary="Automatic execution was refused by the hybrid safety policy.",
                        evidence={
                            "operation_id": str(operation.id),
                            "analysis": analysis.model_dump(mode="json"),
                            "proposal": item.proposal.model_dump(mode="json"),
                            "verification": verification.model_dump(mode="json"),
                            "reasons": list(decision.reasons),
                            "hard_invariants": decision.hard_invariants,
                        },
                        queue_email=self.settings.outbound_email_enabled,
                        sender_address=self.settings.smtp_from_address,
                    )
                decisions.append(decision.decision.value)
                LOGGER.info(
                    "operation safety decision",
                    extra={
                        "email_message_id": str(email.id),
                        "conversation_id": str(email.conversation_id),
                        "request_id": str(request.id),
                        "request_reference": request.public_reference,
                        "operation_id": str(operation.id),
                        "action": operation.action,
                        "decision": decision.decision.value,
                        "correlation_strength": item.correlation_strength.value,
                        "status": operation.status,
                    },
                )
        return execute_ids, decisions

    def _finalize(
        self,
        *,
        email_id: uuid.UUID,
        request_ids: list[uuid.UUID],
        decisions: list[str],
        clarification_id: uuid.UUID | None,
    ) -> ProcessingResult:
        operation_ids: list[uuid.UUID] = []
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email is None:
                raise LookupError("email disappeared during finalization")
            sender_recipient = bare_address(email.sender)
            requested_reply_to = bare_address(email.reply_to or "")
            recipient = (
                requested_reply_to
                if requested_reply_to and requested_reply_to == sender_recipient
                else sender_recipient
            )
            if requested_reply_to and requested_reply_to != sender_recipient:
                warnings = list(email.parsing_warnings)
                warnings.append("reply_to_sender_mismatch_ignored")
                email.parsing_warnings = warnings
            for request_id in request_ids:
                request = session.get(BusinessRequest, request_id)
                if request is None:
                    continue
                operations = list(request.operations)
                operation_ids.extend(operation.id for operation in operations)
                waiting = [
                    operation
                    for operation in operations
                    if operation.status == OperationStatus.NEEDS_INFORMATION.value
                ]
                if waiting:
                    previous_rounds = ClarificationRepository(session).open_for_request(request.id)
                    latest_round = max(
                        (clarification.round_number for clarification in previous_rounds), default=0
                    )
                    is_incomplete_reply = clarification_id is not None
                    if (
                        is_incomplete_reply
                        and latest_round >= self.settings.max_clarification_rounds
                    ):
                        for operation in waiting:
                            set_operation_status(operation, OperationStatus.ESCALATED)
                            operation.final_decision = FinalDecision.ESCALATE.value
                        create_escalation(
                            session,
                            email=email,
                            request=request,
                            recipient=self.settings.escalation_recipient,
                            reason_code="clarification_limit_reached",
                            summary="The reply remained incomplete after the configured clarification round.",
                            evidence={
                                "operation_ids": [str(operation.id) for operation in waiting],
                                "remaining_fields": {
                                    str(operation.id): operation.missing_fields
                                    for operation in waiting
                                },
                            },
                            queue_email=self.settings.outbound_email_enabled,
                            sender_address=self.settings.smtp_from_address,
                        )
                        if clarification_id:
                            clarification = session.get(Clarification, clarification_id)
                            if clarification:
                                clarification.status = ClarificationStatus.EXPIRED.value
                                clarification.resolved_at = utc_now()
                    else:
                        ensure_clarification(
                            session,
                            request=request,
                            source_email=email,
                            operations=waiting,
                            sender_address=self.settings.smtp_from_address,
                            recipient=recipient,
                        )
                status = refresh_request_status(session, request)
                LOGGER.info(
                    "request aggregate status refreshed",
                    extra={
                        "email_message_id": str(email.id),
                        "conversation_id": str(request.conversation_id),
                        "request_id": str(request.id),
                        "request_reference": request.public_reference,
                        "status": status.value,
                    },
                )
                if status == RequestStatus.COMPLETED:
                    request.completed_at = utc_now()
                if clarification_id and not waiting:
                    clarification = session.get(Clarification, clarification_id)
                    if clarification:
                        clarification.status = ClarificationStatus.RESOLVED.value
                        clarification.resolved_at = utc_now()
                pending_operation_ids = set(
                    session.scalars(
                        select(ScheduledExecution.operation_id).where(
                            ScheduledExecution.request_id == request.id,
                            ScheduledExecution.status == ScheduledExecutionStatus.SCHEDULED.value,
                        )
                    )
                )
                pending_operations = [
                    operation for operation in operations if operation.id in pending_operation_ids
                ]
                if pending_operations:
                    ensure_pending_execution_acknowledgement(
                        session,
                        request=request,
                        source_email=email,
                        operations=pending_operations,
                        sender_address=self.settings.smtp_from_address,
                        recipient=recipient,
                        grace_seconds=self.settings.execution_correction_grace_seconds,
                    )
                ensure_terminal_summary(
                    session,
                    request=request,
                    source_email=email,
                    operations=operations,
                    sender_address=self.settings.smtp_from_address,
                    recipient=recipient,
                )
            email.processing_status = ProcessingStatus.PROCESSED.value
            return ProcessingResult(
                email_message_id=email.id,
                status=ProcessingStatus.PROCESSED.value,
                conversation_id=email.conversation_id,
                request_ids=request_ids,
                operation_ids=operation_ids,
                decisions=decisions,
            )

    def _mark_failed(self, email_id: uuid.UUID, error: str) -> None:
        with session_scope(self.session_factory) as session:
            email = session.get(EmailMessage, email_id)
            if email:
                email.processing_status = ProcessingStatus.FAILED.value
                warnings = list(email.parsing_warnings)
                warnings.append(f"processing_error:{error[:500]}")
                email.parsing_warnings = warnings
                existing = session.scalar(
                    select(Escalation).where(
                        Escalation.email_message_id == email.id,
                        Escalation.reason_code == "processing_failure",
                    )
                )
                if existing is None:
                    request = None
                    raw_request_id = email.correlation_details.get("request_id")
                    if raw_request_id:
                        try:
                            request = session.get(BusinessRequest, uuid.UUID(raw_request_id))
                        except (TypeError, ValueError):
                            request = None
                    create_escalation(
                        session,
                        email=email,
                        request=request,
                        recipient=self.settings.escalation_recipient,
                        reason_code="processing_failure",
                        summary="Stored inbound email processing failed and requires review.",
                        evidence={"error": error[:2000]},
                        queue_email=self.settings.outbound_email_enabled,
                        sender_address=self.settings.smtp_from_address,
                    )
