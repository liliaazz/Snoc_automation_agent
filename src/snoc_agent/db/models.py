"""Database entities for identity, request state, audit, execution, and outbox."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from snoc_agent.db.base import Base, utc_now
from snoc_agent.domain.enums import (
    ClarificationStatus,
    ConversationStatus,
    Direction,
    ExecutionStatus,
    OperationStatus,
    OutboxStatus,
    ProcessingStatus,
    RequestKind,
    RequestStatus,
)

JsonDict = dict[str, Any]


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


class DashboardUser(Base, TimestampMixin):
    __tablename__ = "dashboard_users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    normalized_username: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True, unique=True)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MailAccount(Base, TimestampMixin):
    __tablename__ = "mail_accounts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    mailbox: Mapped[str] = mapped_column(String(200), default="INBOX")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_uidvalidity: Mapped[int | None] = mapped_column(Integer)
    polling_checkpoint: Mapped[int | None] = mapped_column(Integer)

    emails: Mapped[list[EmailMessage]] = relationship(back_populates="mail_account")


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Kept as a logical pointer rather than an FK to avoid a circular DDL dependency:
    # email_messages already references conversations. Repository checks keep it valid.
    root_message_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    normalized_subject: Mapped[str] = mapped_column(String(500), default="")
    primary_sender: Mapped[str | None] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default=ConversationStatus.OPEN.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_message_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    messages: Mapped[list[EmailMessage]] = relationship(
        back_populates="conversation", foreign_keys="EmailMessage.conversation_id"
    )
    requests: Mapped[list[BusinessRequest]] = relationship(back_populates="conversation")


class EmailMessage(Base, TimestampMixin):
    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint(
            "mail_account_id",
            "mailbox_name",
            "uidvalidity",
            "imap_uid",
            name="uq_email_physical_mailbox_locator",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    mail_account_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("mail_accounts.id"), nullable=True, index=True
    )
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("conversations.id"), nullable=True, index=True
    )
    direction: Mapped[str] = mapped_column(String(20), default=Direction.INBOUND.value)
    rfc_message_id: Mapped[str | None] = mapped_column(String(998))
    normalized_message_id: Mapped[str | None] = mapped_column(String(998), index=True)
    in_reply_to: Mapped[str | None] = mapped_column(String(998), index=True)
    references_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    sender: Mapped[str] = mapped_column(String(500), default="")
    reply_to: Mapped[str | None] = mapped_column(String(500))
    recipients_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    cc_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    subject: Mapped[str] = mapped_column(Text, default="")
    normalized_subject: Mapped[str] = mapped_column(String(500), default="", index=True)
    raw_text: Mapped[str] = mapped_column(Text, default="")
    latest_user_message: Mapped[str] = mapped_column(Text, default="")
    quoted_text: Mapped[str] = mapped_column(Text, default="")
    signature_text: Mapped[str] = mapped_column(Text, default="")
    html_body: Mapped[str | None] = mapped_column(Text)
    raw_eml_path: Mapped[str | None] = mapped_column(Text)
    raw_eml_blob: Mapped[bytes | None] = mapped_column(LargeBinary)
    raw_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str | None] = mapped_column(String(200))
    attachment_metadata: Mapped[list[JsonDict]] = mapped_column(JSON, default=list)
    imap_uid: Mapped[int | None] = mapped_column(Integer)
    uidvalidity: Mapped[int | None] = mapped_column(Integer)
    mailbox_name: Mapped[str | None] = mapped_column(String(200))
    flags_json: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider_metadata_json: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    internal_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_status: Mapped[str] = mapped_column(
        String(30), default=ProcessingStatus.STORED.value, index=True
    )
    duplicate_of_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True
    )
    automated_classification: Mapped[str | None] = mapped_column(String(100))
    parsing_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    raw_size_bytes: Mapped[int | None] = mapped_column(Integer)
    quarantine_category: Mapped[str | None] = mapped_column(String(100), index=True)
    quarantine_message: Mapped[str | None] = mapped_column(Text)
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    quarantine_retry_count: Mapped[int] = mapped_column(Integer, default=0)
    context_limit_metadata: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    authorization_allowed: Mapped[bool | None] = mapped_column(Boolean)
    authorization_reason: Mapped[str | None] = mapped_column(Text)
    correlation_details: Mapped[JsonDict] = mapped_column(JSON, default=dict)

    mail_account: Mapped[MailAccount | None] = relationship(back_populates="emails")
    conversation: Mapped[Conversation | None] = relationship(
        back_populates="messages", foreign_keys=[conversation_id]
    )


class BusinessRequest(Base):
    __tablename__ = "requests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    public_reference: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("conversations.id"), index=True
    )
    initiating_email_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("email_messages.id"))
    status: Mapped[str] = mapped_column(String(40), default=RequestStatus.NEW.value, index=True)
    request_kind: Mapped[str] = mapped_column(String(40), default=RequestKind.NEW.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_active_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    latest_completion_marker: Mapped[str | None] = mapped_column(String(100))
    escalation_reason: Mapped[str | None] = mapped_column(Text)
    human_assignment: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)

    conversation: Mapped[Conversation] = relationship(back_populates="requests")
    operations: Mapped[list[Operation]] = relationship(
        back_populates="request", order_by="Operation.sequence_number"
    )
    clarifications: Mapped[list[Clarification]] = relationship(back_populates="request")


class Operation(Base, TimestampMixin):
    __tablename__ = "operations"
    __table_args__ = (
        UniqueConstraint("request_id", "sequence_number", name="uq_operation_request_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[str] = mapped_column(String(40), default=OperationStatus.NEW.value, index=True)
    pdv_code: Mapped[str | None] = mapped_column(String(50))
    phone: Mapped[str | None] = mapped_column(String(50))
    additional_payload: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    missing_fields: Mapped[list[str]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[JsonDict]] = mapped_column(JSON, default=list)
    field_provenance: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    analyzer_confidence: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    verifier_confidence: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    contradiction_data: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    model_agreement: Mapped[bool | None] = mapped_column(Boolean)
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    execution_eligible: Mapped[bool] = mapped_column(Boolean, default=False)
    final_decision: Mapped[str | None] = mapped_column(String(50))

    request: Mapped[BusinessRequest] = relationship(back_populates="operations")
    revisions: Mapped[list[FieldRevision]] = relationship(back_populates="operation")
    executions: Mapped[list[Execution]] = relationship(back_populates="operation")
    scheduled_executions: Mapped[list[ScheduledExecution]] = relationship(
        back_populates="operation"
    )


class FieldRevision(Base):
    __tablename__ = "field_revisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("operations.id"), index=True)
    field_name: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source_email_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("email_messages.id"))
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_runs.id"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    operation: Mapped[Operation] = relationship(back_populates="revisions")


class Clarification(Base):
    __tablename__ = "clarifications"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id"), index=True)
    outbound_email_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True
    )
    source_inbound_email_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("email_messages.id")
    )
    target_operation_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    requested_fields: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    question_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(30), default=ClarificationStatus.PENDING_SEND.value, index=True
    )
    reply_email_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True
    )
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    request: Mapped[BusinessRequest] = relationship(back_populates="clarifications")


class ModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    email_message_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True, index=True
    )
    operation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("operations.id"), nullable=True, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), index=True)
    backend: Mapped[str] = mapped_column(String(100), default="openai_compatible")
    model_family: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str] = mapped_column(String(300))
    base_model_id: Mapped[str | None] = mapped_column(String(300), index=True)
    resolved_model_id: Mapped[str | None] = mapped_column(String(400), index=True)
    requested_route: Mapped[str | None] = mapped_column(String(400))
    reported_provider: Mapped[str | None] = mapped_column(String(150), index=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(300))
    quantization: Mapped[str | None] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(100))
    input_context_hash: Mapped[str] = mapped_column(String(64), index=True)
    input_context: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    raw_output: Mapped[str | None] = mapped_column(Text)
    parsed_output: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    structured_output_valid: Mapped[bool] = mapped_column(Boolean, default=False)
    structured_output_mode: Mapped[str | None] = mapped_column(String(40))
    schema_name: Mapped[str | None] = mapped_column(String(200))
    json_schema: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    schema_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    fallback_reason: Mapped[str | None] = mapped_column(Text)
    parse_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    validation_errors: Mapped[list[JsonDict]] = mapped_column(JSON, default=list)
    reasoning_output: Mapped[str | None] = mapped_column(Text)
    latency_seconds: Mapped[float | None] = mapped_column(Float)
    request_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    pricing_metadata: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    input_cost_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    output_cost_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    total_cost_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    cost_basis: Mapped[str] = mapped_column(String(40), default="unknown")
    generation_settings: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    generation_settings_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    logprobs: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    logprob_metrics: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    error_category: Mapped[str | None] = mapped_column(String(80), index=True)
    cached_from_model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_runs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class InferenceCacheEntry(Base):
    __tablename__ = "inference_cache_entries"

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    model_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("model_runs.id"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(40), index=True)
    base_model_id: Mapped[str] = mapped_column(String(300))
    resolved_model_id: Mapped[str] = mapped_column(String(400))
    prompt_version: Mapped[str] = mapped_column(String(100))
    structured_output_mode: Mapped[str] = mapped_column(String(40))
    context_hash: Mapped[str] = mapped_column(String(64))
    schema_hash: Mapped[str] = mapped_column(String(64))
    generation_settings_hash: Mapped[str] = mapped_column(String(64))
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(40), default="running", index=True)
    dataset_path: Mapped[str] = mapped_column(Text)
    dataset_hash: Mapped[str] = mapped_column(String(64), index=True)
    dataset_split: Mapped[str | None] = mapped_column(String(40))
    configuration_hash: Mapped[str] = mapped_column(String(64), index=True)
    configuration: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    output_dir: Mapped[str] = mapped_column(Text)
    budget_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    stop_before_budget_usd: Mapped[Any | None] = mapped_column(Numeric(18, 9))
    cost_so_far_usd: Mapped[Any] = mapped_column(Numeric(18, 9), default=0)
    budget_status: Mapped[str] = mapped_column(String(50), default="within_budget")
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    unknown_cost_request_count: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_remaining_calls: Mapped[int | None] = mapped_column(Integer)
    checkpoint_row: Mapped[int] = mapped_column(Integer, default=0)
    resumable_command: Mapped[str | None] = mapped_column(Text)
    final_error_category: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvaluationInference(Base):
    __tablename__ = "evaluation_inferences"
    __table_args__ = (
        UniqueConstraint(
            "evaluation_run_id",
            "example_id",
            "stage",
            "base_model_id",
            "proposal_hash",
            name="uq_evaluation_inference_identity",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    evaluation_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("evaluation_runs.id"), index=True
    )
    example_id: Mapped[str] = mapped_column(String(300), index=True)
    stage: Mapped[str] = mapped_column(String(40), index=True)
    analyzer_source_model_id: Mapped[str | None] = mapped_column(String(300))
    base_model_id: Mapped[str] = mapped_column(String(300))
    proposal_hash: Mapped[str] = mapped_column(String(64), default="")
    inference_key: Mapped[str | None] = mapped_column(String(64), index=True)
    model_run_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("model_runs.id"), nullable=True, index=True
    )
    attempt_model_run_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(40), default="complete", index=True)
    error_category: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CalibrationArtifact(Base):
    __tablename__ = "calibration_artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    method: Mapped[str] = mapped_column(String(40))
    dataset_hash: Mapped[str] = mapped_column(String(64), index=True)
    dataset_split: Mapped[str] = mapped_column(String(40), default="calibration")
    feature_version: Mapped[str] = mapped_column(String(100))
    policy_version: Mapped[str] = mapped_column(String(100))
    parameters: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    metrics: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ValidationDecision(Base):
    __tablename__ = "validation_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("operations.id"), index=True)
    analyzer_result: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    verifier_result: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    hard_invariant_results: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    decision: Mapped[str] = mapped_column(String(50), index=True)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_version: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("operations.id"), index=True)
    operation_revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    endpoint: Mapped[str] = mapped_column(Text)
    request_payload: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default=ExecutionStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    operation: Mapped[Operation] = relationship(back_populates="executions")


class ScheduledExecution(Base):
    """Durable grace-period queue item.

    A scheduled row records intent to execute a specific immutable operation
    revision.  It is deliberately separate from ``Execution``: merely waiting
    through a correction window is not a business-API attempt and must not be
    counted as one.
    """

    __tablename__ = "scheduled_executions"
    __table_args__ = (
        UniqueConstraint(
            "operation_id",
            "operation_revision",
            name="uq_scheduled_execution_operation_revision",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'dispatching', 'dispatched', 'cancelled', 'failed')",
            name="scheduled_execution_status",
        ),
        Index(
            "ix_scheduled_executions_status_not_before",
            "status",
            "not_before",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    operation_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("operations.id"), index=True)
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("requests.id"), index=True)
    operation_revision: Mapped[int] = mapped_column(Integer)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    source_email_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), index=True
    )
    not_before: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(30), default="scheduled", index=True)
    cancellation_reason: Mapped[str | None] = mapped_column(Text)
    cancellation_source_email_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True
    )
    cancellation_data: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("executions.id"), nullable=True, unique=True
    )
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )

    operation: Mapped[Operation] = relationship(back_populates="scheduled_executions")


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    related_request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id"), nullable=True, index=True
    )
    related_clarification_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("clarifications.id"), nullable=True
    )
    outbound_email_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), unique=True
    )
    recipient: Mapped[str] = mapped_column(String(500))
    subject: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    headers: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default=OutboxStatus.PENDING.value, index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Escalation(Base):
    __tablename__ = "escalations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("requests.id"), nullable=True, index=True
    )
    email_message_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("email_messages.id"))
    recipient: Mapped[str] = mapped_column(String(500))
    reason_code: Mapped[str] = mapped_column(String(100), index=True)
    summary: Mapped[str] = mapped_column(Text)
    evidence: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkflowRun(Base):
    """Durable lifecycle record for one LangGraph invocation."""

    __tablename__ = "workflow_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    inbound_email_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("email_messages.id"), nullable=True, index=True
    )
    graph_version: Mapped[str] = mapped_column(String(100))
    engine: Mapped[str] = mapped_column(String(40), default="langgraph")
    status: Mapped[str] = mapped_column(String(30), default="running", index=True)
    current_agent: Mapped[str | None] = mapped_column(String(40))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class WorkflowEvent(Base):
    """Bounded per-agent trace without raw mail, prompts, reasoning, or credentials."""

    __tablename__ = "workflow_events"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "sequence", name="uq_workflow_event_run_sequence"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workflow_run_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workflow_runs.id"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    agent: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    input_summary: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    output_summary: Mapped[JsonDict] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_category: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
