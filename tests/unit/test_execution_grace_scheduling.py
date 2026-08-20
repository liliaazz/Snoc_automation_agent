from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from sqlalchemy import create_engine, inspect, select

from alembic import command
from snoc_agent.business_api import MockBusinessAPI
from snoc_agent.db.models import (
    BusinessRequest,
    Conversation,
    EmailMessage,
    Execution,
    Operation,
    ScheduledExecution,
)
from snoc_agent.db.session import SessionFactory, create_engine_and_session, create_schema
from snoc_agent.domain.enums import (
    Direction,
    ExecutionStatus,
    FinalDecision,
    OperationAction,
    OperationStatus,
    ProcessingStatus,
    RequestStatus,
)
from snoc_agent.workflow.execution_service import (
    ExecutionService,
    ScheduledExecutionStatus,
)


def _database(tmp_path: Path) -> SessionFactory:
    engine, session_factory = create_engine_and_session(
        f"sqlite+pysqlite:///{tmp_path / 'execution-grace.sqlite3'}"
    )
    create_schema(engine)
    return session_factory


def _email(
    *,
    conversation_id: UUID,
    message_id: str,
    body: str,
) -> EmailMessage:
    return EmailMessage(
        conversation_id=conversation_id,
        direction=Direction.INBOUND.value,
        rfc_message_id=message_id,
        normalized_message_id=message_id,
        sender="authorized@example.test",
        recipients_json=["snoc@example.test"],
        cc_json=[],
        subject="Execution grace",
        normalized_subject="execution grace",
        raw_text=body,
        latest_user_message=body,
        quoted_text="",
        signature_text="",
        raw_sha256="c" * 64,
        mime_type="text/plain",
        attachment_metadata=[],
        flags_json=[],
        processing_status=ProcessingStatus.PROCESSED.value,
        parsing_warnings=[],
        correlation_details={},
    )


def _seed_ready_operation(
    session_factory: SessionFactory,
) -> tuple[UUID, UUID, UUID, UUID]:
    with session_factory() as session:
        conversation = Conversation(
            normalized_subject="execution grace",
            primary_sender="authorized@example.test",
        )
        session.add(conversation)
        session.flush()
        source_email = _email(
            conversation_id=conversation.id,
            message_id="<execution-grace@example.test>",
            body="Unlock PDV 12000001.",
        )
        session.add(source_email)
        session.flush()
        request = BusinessRequest(
            public_reference="SNOC-EXEC-GRACE-001",
            conversation_id=conversation.id,
            initiating_email_id=source_email.id,
            status=RequestStatus.READY_FOR_VALIDATION.value,
        )
        session.add(request)
        session.flush()
        operation = Operation(
            request_id=request.id,
            sequence_number=1,
            action=OperationAction.ACCOUNT_UNBLOCK.value,
            status=OperationStatus.READY_FOR_VALIDATION.value,
            pdv_code="12000001",
            additional_payload={},
            missing_fields=[],
            evidence=[],
            execution_eligible=True,
            final_decision=FinalDecision.AUTO_EXECUTE.value,
        )
        session.add(operation)
        session.commit()
        return operation.id, request.id, source_email.id, conversation.id


def test_schedule_waits_without_creating_execution_then_dispatches_once(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id, _request_id, source_email_id, _conversation_id = _seed_ready_operation(
        session_factory
    )
    now = datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
    not_before = now + timedelta(minutes=5)
    business_api = MockBusinessAPI()
    service = ExecutionService(session_factory, business_api, clock=lambda: now)

    scheduled = service.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=not_before,
    )
    duplicate = service.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=not_before + timedelta(hours=1),
    )

    assert scheduled.status == ScheduledExecutionStatus.SCHEDULED
    assert duplicate.scheduled_execution_id == scheduled.scheduled_execution_id
    assert duplicate.not_before == not_before
    assert service.dispatch_due(now=not_before - timedelta(microseconds=1)) == []
    assert business_api.calls == []
    with session_factory() as session:
        assert len(list(session.scalars(select(ScheduledExecution)))) == 1
        assert len(list(session.scalars(select(Execution)))) == 0

    dispatched = service.dispatch_due(now=not_before)

    assert len(dispatched) == 1
    assert dispatched[0].status == ScheduledExecutionStatus.DISPATCHED
    assert dispatched[0].execution is not None
    assert dispatched[0].execution.status == ExecutionStatus.SUCCEEDED
    assert len(business_api.calls) == 1
    assert service.dispatch_due(now=not_before + timedelta(days=1)) == []
    assert len(business_api.calls) == 1
    with session_factory() as session:
        queue_item = session.get_one(
            ScheduledExecution,
            scheduled.scheduled_execution_id,
        )
        executions = list(session.scalars(select(Execution)))
        assert queue_item.status == ScheduledExecutionStatus.DISPATCHED.value
        assert queue_item.execution_id == executions[0].id
        assert len(executions) == 1


def test_request_correction_cancels_waiting_items_with_audit_data(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id, request_id, source_email_id, conversation_id = _seed_ready_operation(
        session_factory
    )
    now = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)
    not_before = now + timedelta(minutes=5)
    business_api = MockBusinessAPI()
    service = ExecutionService(session_factory, business_api, clock=lambda: now)
    scheduled = service.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=not_before,
    )
    with session_factory() as session:
        correction = _email(
            conversation_id=conversation_id,
            message_id="<execution-correction@example.test>",
            body="Correction: do not unlock this PDV.",
        )
        session.add(correction)
        session.commit()
        correction_id = correction.id

    cancelled_count = service.cancel_scheduled_for_request(
        request_id,
        reason="correction_received",
        source_email_id=correction_id,
    )

    assert cancelled_count == 1
    assert service.dispatch_due(now=not_before + timedelta(hours=1)) == []
    assert business_api.calls == []
    with session_factory() as session:
        queue_item = session.get_one(
            ScheduledExecution,
            scheduled.scheduled_execution_id,
        )
        assert queue_item.status == ScheduledExecutionStatus.CANCELLED.value
        assert queue_item.cancellation_reason == "correction_received"
        assert queue_item.cancellation_source_email_id == correction_id
        assert queue_item.cancellation_data == {
            "reason": "correction_received",
            "source_email_id": str(correction_id),
        }
        assert queue_item.cancelled_at is not None
        assert len(list(session.scalars(select(Execution)))) == 0


def test_dispatch_cancels_stale_revision_without_business_api_call(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id, _request_id, source_email_id, _conversation_id = _seed_ready_operation(
        session_factory
    )
    now = datetime(2026, 7, 28, 11, 0, tzinfo=UTC)
    business_api = MockBusinessAPI()
    service = ExecutionService(session_factory, business_api, clock=lambda: now)
    scheduled = service.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=now,
    )
    with session_factory() as session:
        operation = session.get_one(Operation, operation_id)
        operation.current_revision += 1
        operation.pdv_code = "12000002"
        session.commit()

    outcomes = service.dispatch_due()

    assert len(outcomes) == 1
    assert outcomes[0].status == ScheduledExecutionStatus.CANCELLED
    assert outcomes[0].detail == "stale_operation_revision"
    assert business_api.calls == []
    with session_factory() as session:
        queue_item = session.get_one(
            ScheduledExecution,
            scheduled.scheduled_execution_id,
        )
        assert queue_item.status == ScheduledExecutionStatus.CANCELLED.value
        assert queue_item.cancellation_reason == "stale_operation_revision"
        assert queue_item.cancellation_data == {
            "reason": "stale_operation_revision",
            "scheduled_revision": 1,
            "observed_revision": 2,
        }
        assert len(list(session.scalars(select(Execution)))) == 0


def test_dispatch_cancels_operation_that_is_no_longer_ready(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id, _request_id, source_email_id, _conversation_id = _seed_ready_operation(
        session_factory
    )
    now = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)
    business_api = MockBusinessAPI()
    service = ExecutionService(session_factory, business_api, clock=lambda: now)
    scheduled = service.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=now,
    )
    with session_factory() as session:
        operation = session.get_one(Operation, operation_id)
        operation.status = OperationStatus.ESCALATED.value
        operation.execution_eligible = False
        operation.final_decision = FinalDecision.ESCALATE.value
        session.commit()

    outcomes = service.dispatch_due()

    assert len(outcomes) == 1
    assert outcomes[0].status == ScheduledExecutionStatus.CANCELLED
    assert outcomes[0].detail == "operation_not_ready"
    assert business_api.calls == []
    with session_factory() as session:
        queue_item = session.get_one(
            ScheduledExecution,
            scheduled.scheduled_execution_id,
        )
        assert queue_item.cancellation_reason == "operation_not_ready"
        assert len(list(session.scalars(select(Execution)))) == 0


def test_claim_is_atomic_compare_and_set_across_workers(tmp_path: Path) -> None:
    session_factory = _database(tmp_path)
    operation_id, _request_id, source_email_id, _conversation_id = _seed_ready_operation(
        session_factory
    )
    now = datetime(2026, 7, 28, 13, 0, tzinfo=UTC)
    business_api = MockBusinessAPI()
    first_worker = ExecutionService(session_factory, business_api, clock=lambda: now)
    second_worker = ExecutionService(session_factory, business_api, clock=lambda: now)
    scheduled = first_worker.schedule(
        operation_id,
        source_email_id=source_email_id,
        not_before=now,
    )

    assert first_worker._claim(scheduled.scheduled_execution_id, now) is True
    assert second_worker._claim(scheduled.scheduled_execution_id, now) is False

    outcome = first_worker._dispatch_claimed(scheduled.scheduled_execution_id, now)

    assert outcome.status == ScheduledExecutionStatus.DISPATCHED
    assert len(business_api.calls) == 1
    with session_factory() as session:
        assert len(list(session.scalars(select(Execution)))) == 1


def test_alembic_migration_creates_and_removes_scheduled_execution_table(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path}"
    config = Config("alembic.ini")
    config.attributes["runtime_database_url"] = database_url

    command.upgrade(config, "head")

    engine = create_engine(database_url)
    inspector = inspect(engine)
    assert "scheduled_executions" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("scheduled_executions")}
    assert {
        "operation_id",
        "request_id",
        "operation_revision",
        "idempotency_key",
        "source_email_id",
        "not_before",
        "status",
        "cancellation_reason",
        "cancellation_data",
        "cancelled_at",
        "created_at",
        "updated_at",
    } <= columns
    engine.dispose()

    command.downgrade(config, "a92e710c4b35")

    engine = create_engine(database_url)
    assert "scheduled_executions" not in inspect(engine).get_table_names()
    engine.dispose()
