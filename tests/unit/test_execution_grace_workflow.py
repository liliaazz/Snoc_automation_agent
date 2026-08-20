from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import GenerationConfig
from snoc_agent.ai.mock_backend import MockLLMBackend
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.business_api import MockBusinessAPI
from snoc_agent.config import Settings
from snoc_agent.datetime_utils import ensure_utc
from snoc_agent.db.models import (
    EmailMessage,
    Execution,
    Operation,
    OutboxMessage,
    ScheduledExecution,
)
from snoc_agent.db.session import SessionFactory, create_engine_and_session, create_schema
from snoc_agent.domain.enums import (
    ExecutionStatus,
    FinalDecision,
    OperationStatus,
    ProcessingStatus,
)
from snoc_agent.mail.fake_mailbox import FakeSMTPTransport
from snoc_agent.workflow.authorizer import StaticSenderAuthorizer
from snoc_agent.workflow.decision_engine import HybridDecisionEngine
from snoc_agent.workflow.execution_service import (
    ExecutionService,
    ScheduledExecutionStatus,
)
from snoc_agent.workflow.inbound_processor import InboundProcessor
from snoc_agent.workflow.outbox_service import OutboxService
from snoc_agent.workflow.pending_execution_service import PENDING_EXECUTION_HEADER

SENDER = "superviseur.theta@example.invalid"
ORIGINAL_MESSAGE_ID = "<grace-original-otp@example.invalid>"
ORIGINAL_EMAIL = f"""From: Superviseur Theta <{SENDER}>
To: Agent SNOC <snoc-agent@example.invalid>
Date: Tue, 28 Jul 2026 09:00:00 +0100
Message-ID: {ORIGINAL_MESSAGE_ID}
Subject: Changement numero OTP PDV 82000001
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: 8bit

Bonjour,

Merci de remplacer le numéro OTP du PDV 82000001 par le 0770000040.

Cordialement
""".encode()
CORRECTION_EMAIL = f"""From: Superviseur Theta <{SENDER}>
To: Agent SNOC <snoc-agent@example.invalid>
Date: Tue, 28 Jul 2026 09:00:05 +0100
Message-ID: <grace-correction-otp@example.invalid>
In-Reply-To: {ORIGINAL_MESSAGE_ID}
References: {ORIGINAL_MESSAGE_ID}
Subject: Re: Changement numero OTP PDV 82000001
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: 8bit

Correction avant traitement : pour le PDV 82000001, le bon nouveau numéro
est 0770000041, et non 0770000040.
""".encode()


def _analysis(*, phone: str, message_kind: str = "new_request") -> dict[str, Any]:
    return {
        "message_kind": message_kind,
        "referenced_existing_operation_ids": [],
        "operations": [
            {
                "local_operation_id": f"otp-{phone}",
                "action": "otp_number_change",
                "pdv_code": "82000001",
                "phone": phone,
                "additional_fields": {},
                "missing_fields": [],
                "evidence": [
                    {
                        "field_name": "pdv_code",
                        "value": "82000001",
                        "source": "latest_user_message",
                        "evidence_text": "PDV 82000001",
                        "support": "supported",
                    },
                    {
                        "field_name": "new_phone",
                        "value": phone,
                        "source": "latest_user_message",
                        "evidence_text": phone,
                        "support": "supported",
                    },
                ],
                "ambiguity_reasons": [],
                "raw_action_confidence": 0.99,
                "raw_field_confidence": {
                    "pdv_code": 0.99,
                    "new_phone": 0.99,
                },
            }
        ],
        "new_request_present": message_kind == "new_request",
        "contradiction_with_stored_state": False,
        "contradiction_details": [],
        "unresolved_ambiguities": [],
        "direct_current_instruction": True,
        "hypothetical_or_conditional": False,
        "forwarded_content": False,
        "cancellation_detected": False,
        "subject_body_conflict": False,
        "candidate_mapping_explicit": True,
    }


def _verification(*, correction: bool = False) -> dict[str, Any]:
    return {
        "action_supported": "yes",
        "pdv_supported": "yes",
        "phone_supported": "yes",
        "stored_state_compatible": "yes",
        "contradiction_present": False,
        "contradiction_type": None,
        "missing_fields": [],
        "additional_fields_supported": {},
        "correction_detected": correction,
        "new_request_detected": not correction,
        "evidence_summary": ["The current message explicitly supplies the operation fields."],
        "raw_confidence": 0.99,
        "correction_supported": correction,
        "correction_evidence": (
            ["Correction avant traitement", "0770000041"] if correction else []
        ),
        "ambiguity_detected": False,
        "ambiguity_reason": None,
        "direct_current_instruction": "yes",
        "hypothetical_or_conditional": False,
        "subject_body_conflict": False,
        "evidence_sources_valid": True,
        "candidate_mapping_explicit": True,
    }


@dataclass(slots=True)
class Harness:
    session_factory: SessionFactory
    settings: Settings
    processor: InboundProcessor
    business_api: MockBusinessAPI
    smtp: FakeSMTPTransport
    outbox: OutboxService


def _processor(
    *,
    session_factory: SessionFactory,
    settings: Settings,
    business_api: MockBusinessAPI,
    analyzer_outputs: list[dict[str, Any]],
    verifier_outputs: list[dict[str, Any]],
) -> InboundProcessor:
    return InboundProcessor(
        session_factory=session_factory,
        settings=settings,
        analyzer=EmailAnalyzer(
            MockLLMBackend(analyzer_outputs),
            GenerationConfig(model="grace-workflow-analyzer", temperature=0.0),
        ),
        verifier=SemanticVerifier(
            MockLLMBackend(verifier_outputs),
            GenerationConfig(model="grace-workflow-verifier", temperature=0.0),
        ),
        authorizer=StaticSenderAuthorizer(settings.authorized_sender_set),
        decision_engine=HybridDecisionEngine(),
        execution_service=ExecutionService(session_factory, business_api),
    )


def _harness(
    tmp_path: Path,
    *,
    grace_seconds: int,
    analyzer_outputs: list[dict[str, Any]] | None = None,
    verifier_outputs: list[dict[str, Any]] | None = None,
) -> Harness:
    database_path = tmp_path / "execution-grace-workflow.sqlite3"
    engine, session_factory = create_engine_and_session(f"sqlite+pysqlite:///{database_path}")
    create_schema(engine)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+pysqlite:///{database_path}",
        authorized_senders=SENDER,
        dry_run=True,
        dry_run_send_emails=True,
        store_raw_eml=False,
        smtp_from_address="snoc-agent@example.invalid",
        escalation_recipient="human-support@example.invalid",
        execution_correction_grace_seconds=grace_seconds,
    )
    business_api = MockBusinessAPI()
    processor = _processor(
        session_factory=session_factory,
        settings=settings,
        business_api=business_api,
        analyzer_outputs=analyzer_outputs or [_analysis(phone="0770000040")],
        verifier_outputs=verifier_outputs or [_verification()],
    )
    smtp = FakeSMTPTransport()
    return Harness(
        session_factory=session_factory,
        settings=settings,
        processor=processor,
        business_api=business_api,
        smtp=smtp,
        outbox=OutboxService(
            session_factory,
            smtp,
            sender=settings.smtp_from_address,
        ),
    )


def test_auto_execute_is_scheduled_without_api_call_and_has_one_pending_ack(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, grace_seconds=30)

    result = harness.processor.process_raw(ORIGINAL_EMAIL)
    duplicate = harness.processor.process_raw(ORIGINAL_EMAIL)

    assert result.status == ProcessingStatus.PROCESSED.value
    assert result.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert duplicate.status == ProcessingStatus.DUPLICATE.value
    assert harness.business_api.calls == []
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        scheduled = session.scalars(select(ScheduledExecution)).one()
        acknowledgements = [
            message
            for message in session.scalars(select(OutboxMessage))
            if message.headers.get(PENDING_EXECUTION_HEADER) == "true"
        ]
        assert operation.status == OperationStatus.READY_FOR_VALIDATION.value
        assert scheduled.operation_id == operation.id
        assert scheduled.status == ScheduledExecutionStatus.SCHEDULED.value
        assert list(session.scalars(select(Execution))) == []
        assert len(acknowledgements) == 1
        acknowledgement = acknowledgements[0]
        assert "traitement automatique commencera dans environ 30 secondes" in acknowledgement.body
        assert "OP-01" not in acknowledgement.body
        assert "82000001" not in acknowledgement.body
        assert "se terminant par 0001" in acknowledgement.body
        assert acknowledgement.headers["X-SNOC-Reply-Type"] == "acknowledgement"
        assert "X-SNOC-Operation-IDs" not in acknowledgement.headers


def test_explicit_same_thread_correction_cancels_queue_before_dispatch(
    tmp_path: Path,
) -> None:
    harness = _harness(
        tmp_path,
        grace_seconds=30,
        analyzer_outputs=[
            _analysis(phone="0770000040"),
            _analysis(phone="0770000041", message_kind="correction"),
        ],
        verifier_outputs=[_verification(), _verification(correction=True)],
    )
    first = harness.processor.process_raw(ORIGINAL_EMAIL)

    correction = harness.processor.process_raw(CORRECTION_EMAIL)

    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert correction.request_ids == first.request_ids
    assert correction.decisions == [FinalDecision.REVIEW_CORRECTION.value]
    assert harness.business_api.calls == []
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        scheduled = session.scalars(select(ScheduledExecution)).one()
        correction_email = session.get_one(EmailMessage, correction.email_message_id)
        assert operation.current_revision == 2
        assert operation.phone == "0770000041"
        assert operation.status == OperationStatus.ESCALATED.value
        assert scheduled.status == ScheduledExecutionStatus.CANCELLED.value
        assert scheduled.cancellation_reason == "explicit_same_thread_correction_or_cancellation"
        assert scheduled.cancellation_source_email_id == correction.email_message_id
        assert correction_email.context_limit_metadata["cancelled_scheduled_execution_count"] == 1
        due = ensure_utc(scheduled.not_before)
        assert due is not None
        due += timedelta(hours=1)
    assert harness.processor.dispatch_due_scheduled_executions(now=due) == []
    assert harness.business_api.calls == []


def test_restarted_worker_dispatches_once_and_queues_one_final_summary(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path, grace_seconds=30)
    result = harness.processor.process_raw(ORIGINAL_EMAIL)
    assert harness.outbox.send_once() == (1, 0)
    with harness.session_factory() as session:
        scheduled = session.scalars(select(ScheduledExecution)).one()
        due = ensure_utc(scheduled.not_before)
        assert due is not None

    restarted_api = MockBusinessAPI()
    restarted = _processor(
        session_factory=harness.session_factory,
        settings=harness.settings,
        business_api=restarted_api,
        analyzer_outputs=[],
        verifier_outputs=[],
    )
    outcomes = restarted.dispatch_due_scheduled_executions(now=due)

    assert len(outcomes) == 1
    assert outcomes[0].status == ScheduledExecutionStatus.DISPATCHED
    assert outcomes[0].execution is not None
    assert outcomes[0].execution.status == ExecutionStatus.SUCCEEDED
    assert len(restarted_api.calls) == 1
    assert harness.outbox.send_once() == (1, 0)
    assert len(harness.smtp.sent) == 2
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        queue_item = session.scalars(select(ScheduledExecution)).one()
        executions = list(session.scalars(select(Execution)))
        outbox_messages = list(session.scalars(select(OutboxMessage)))
        pending_acks = [
            message
            for message in outbox_messages
            if message.headers.get(PENDING_EXECUTION_HEADER) == "true"
        ]
        final_summaries = [
            message
            for message in outbox_messages
            if message.headers.get("X-SNOC-Reply-Type") == "completion"
        ]
        assert operation.status == OperationStatus.COMPLETED.value
        assert queue_item.status == ScheduledExecutionStatus.DISPATCHED.value
        assert queue_item.execution_id == executions[0].id
        assert len(executions) == 1
        assert len(pending_acks) == 1
        assert len(final_summaries) == 1

    second_restart = _processor(
        session_factory=harness.session_factory,
        settings=harness.settings,
        business_api=MockBusinessAPI(),
        analyzer_outputs=[],
        verifier_outputs=[],
    )
    assert second_restart.dispatch_due_scheduled_executions(now=due + timedelta(days=1)) == []
    assert len(restarted_api.calls) == 1
    assert harness.outbox.send_once() == (0, 0)
    assert result.request_ids


def test_zero_grace_preserves_immediate_execution_compatibility(tmp_path: Path) -> None:
    harness = _harness(tmp_path, grace_seconds=0)

    result = harness.processor.process_raw(ORIGINAL_EMAIL)

    assert result.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert len(harness.business_api.calls) == 1
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        execution = session.scalars(select(Execution)).one()
        outbox = session.scalars(select(OutboxMessage)).one()
        assert operation.status == OperationStatus.COMPLETED.value
        assert execution.status == ExecutionStatus.SUCCEEDED.value
        assert list(session.scalars(select(ScheduledExecution))) == []
        assert outbox.headers.get(PENDING_EXECUTION_HEADER) is None
        assert outbox.headers.get("X-SNOC-Reply-Type") == "completion"
        assert "SNOC_REQUEST_CLOSED" not in outbox.body
