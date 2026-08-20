from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from snoc_agent.business_api import BusinessAPIResult, MockBusinessAPI
from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    Conversation,
    EmailMessage,
    Execution,
    Operation,
)
from snoc_agent.db.session import (
    SessionFactory,
    create_engine_and_session,
    create_schema,
)
from snoc_agent.domain.enums import (
    Direction,
    ExecutionStatus,
    FinalDecision,
    OperationAction,
    OperationStatus,
    ProcessingStatus,
    RequestStatus,
)
from snoc_agent.workflow.execution_service import ExecutionService


def _database(tmp_path: Path) -> SessionFactory:
    engine, session_factory = create_engine_and_session(
        f"sqlite+pysqlite:///{tmp_path / 'execution-safety.sqlite3'}"
    )
    create_schema(engine)
    return session_factory


def _seed_ready_operation(
    session_factory: SessionFactory,
    *,
    action: OperationAction,
    pdv_code: str,
    phone: str | None = None,
    additional_payload: dict[str, object] | None = None,
) -> UUID:
    with session_factory() as session:
        conversation = Conversation(
            normalized_subject="execution safety",
            primary_sender="authorized@example.test",
        )
        session.add(conversation)
        session.flush()
        inbound = EmailMessage(
            conversation_id=conversation.id,
            direction=Direction.INBOUND.value,
            rfc_message_id="<execution-safety@example.test>",
            normalized_message_id="<execution-safety@example.test>",
            sender="authorized@example.test",
            recipients_json=["snoc@example.test"],
            cc_json=[],
            subject="Execution safety",
            normalized_subject="execution safety",
            raw_text="Execute this accepted operation.",
            latest_user_message="Execute this accepted operation.",
            quoted_text="",
            signature_text="",
            raw_sha256="b" * 64,
            mime_type="text/plain",
            attachment_metadata=[],
            flags_json=[],
            processing_status=ProcessingStatus.PROCESSED.value,
            parsing_warnings=[],
            correlation_details={},
        )
        session.add(inbound)
        session.flush()
        request = BusinessRequest(
            public_reference="SNOC-EXEC-SAFETY-001",
            conversation_id=conversation.id,
            initiating_email_id=inbound.id,
            status=RequestStatus.READY_FOR_VALIDATION.value,
        )
        session.add(request)
        session.flush()
        operation = Operation(
            request_id=request.id,
            sequence_number=1,
            action=action.value,
            status=OperationStatus.READY_FOR_VALIDATION.value,
            pdv_code=pdv_code,
            phone=phone,
            additional_payload=dict(additional_payload or {}),
            missing_fields=[],
            evidence=[],
            execution_eligible=True,
            final_decision=FinalDecision.AUTO_EXECUTE.value,
        )
        session.add(operation)
        session.commit()
        return operation.id


class UnexpectedFailureAPI(MockBusinessAPI):
    def __init__(self) -> None:
        super().__init__()
        self.attempt_count = 0

    def unlock_account(
        self,
        *,
        pdv_code: str,
        idempotency_key: str,
    ) -> BusinessAPIResult:
        self.attempt_count += 1
        raise ValueError(f"unexpected adapter failure for {pdv_code} ({idempotency_key})")


def test_unexpected_adapter_exception_is_unknown_escalated_and_replay_safe(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id = _seed_ready_operation(
        session_factory,
        action=OperationAction.ACCOUNT_UNBLOCK,
        pdv_code="12000001",
    )
    business_api = UnexpectedFailureAPI()
    service = ExecutionService(session_factory, business_api)

    first = service.execute(operation_id)

    assert first.status == ExecutionStatus.UNKNOWN
    assert "unexpected adapter failure" in first.detail
    assert business_api.attempt_count == 1
    with session_factory() as session:
        operation = session.get_one(Operation, operation_id)
        execution = session.scalars(select(Execution)).one()

        assert execution.id == first.execution_id
        assert execution.status == ExecutionStatus.UNKNOWN.value
        assert execution.attempt_count == 1
        assert execution.response_body["exception_type"] == "ValueError"
        assert "unexpected adapter failure" in execution.response_body["error"]
        assert operation.status == OperationStatus.ESCALATED.value
        assert operation.execution_eligible is False

    replay = service.execute(operation_id)

    assert replay.execution_id == first.execution_id
    assert replay.status == ExecutionStatus.UNKNOWN
    assert replay.detail == "identical operation revision was already recorded"
    assert business_api.attempt_count == 1
    with session_factory() as session:
        assert len(list(session.scalars(select(Execution)))) == 1


def test_unapproved_additional_field_fails_preflight_without_adapter_call(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    operation_id = _seed_ready_operation(
        session_factory,
        action=OperationAction.VPN_ACCESS,
        pdv_code="12000002",
        phone="0770000002",
        additional_payload={"privileged_role": "administrator"},
    )
    business_api = MockBusinessAPI()
    service = ExecutionService(
        session_factory,
        business_api,
        vpn_allowed_additional_fields=frozenset({"region"}),
    )

    first = service.execute(operation_id)

    assert first.status == ExecutionStatus.FAILED
    assert first.detail == ("operation contains unapproved additional fields: privileged_role")
    assert business_api.calls == []
    with session_factory() as session:
        operation = session.get_one(Operation, operation_id)
        execution = session.scalars(select(Execution)).one()

        assert execution.id == first.execution_id
        assert execution.status == ExecutionStatus.FAILED.value
        assert execution.attempt_count == 0
        assert execution.endpoint == "rejected_preflight:vpn_access"
        assert execution.request_payload == {
            "pdv_code": "12000002",
            "phone": "0770000002",
            "privileged_role": "administrator",
        }
        assert execution.response_body == {
            "error": "operation contains unapproved additional fields: privileged_role"
        }
        assert operation.status == OperationStatus.ESCALATED.value
        assert operation.execution_eligible is False

    replay = service.execute(operation_id)
    assert replay.execution_id == first.execution_id
    assert replay.status == ExecutionStatus.FAILED
    assert business_api.calls == []


@pytest.mark.parametrize("reserved_name", ["pdv_code", "phone", "idempotency_key"])
def test_settings_rejects_reserved_vpn_allowlist_keys(reserved_name: str) -> None:
    with pytest.raises(
        ValidationError,
        match="VPN additional field names must be safe, non-reserved identifiers",
    ):
        Settings(business_api_vpn_allowed_additional_fields=f"region,{reserved_name}")
