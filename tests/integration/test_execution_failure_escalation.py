from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import GenerationConfig
from snoc_agent.ai.mock_backend import MockLLMBackend
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.business_api import (
    BusinessAPI,
    BusinessAPIResult,
    BusinessAPITransportError,
    MockBusinessAPI,
)
from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    EmailMessage,
    Escalation,
    Execution,
    Operation,
    OutboxMessage,
)
from snoc_agent.db.session import (
    SessionFactory,
    create_engine_and_session,
    create_schema,
)
from snoc_agent.domain.enums import (
    ExecutionStatus,
    FinalDecision,
    OperationAction,
    OperationStatus,
    ProcessingStatus,
    RequestStatus,
)
from snoc_agent.workflow.authorizer import StaticSenderAuthorizer
from snoc_agent.workflow.decision_engine import HybridDecisionEngine
from snoc_agent.workflow.execution_service import ExecutionService
from snoc_agent.workflow.inbound_processor import InboundProcessor

FIXTURES = Path(__file__).parents[1] / "fixtures"
RAW_UNBLOCK = (FIXTURES / "emails/scenario_a_complete_unblock/01_complete_unblock.eml").read_bytes()


def _model_output(relative_path: str) -> dict[str, object]:
    return json.loads((FIXTURES / "model_outputs" / relative_path).read_text(encoding="utf-8"))


class TransportFailingBusinessAPI(MockBusinessAPI):
    def __init__(self) -> None:
        super().__init__()
        self.attempt_count = 0
        self.idempotency_keys: list[str] = []

    def unlock_account(
        self,
        *,
        pdv_code: str,
        idempotency_key: str,
    ) -> BusinessAPIResult:
        self.attempt_count += 1
        self.idempotency_keys.append(idempotency_key)
        raise BusinessAPITransportError(
            "connection dropped after request submission",
            endpoint=f"/unlock-account/{pdv_code}",
            attempts=1,
        )


class RejectingBusinessAPI(MockBusinessAPI):
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
        return BusinessAPIResult(
            action=OperationAction.ACCOUNT_UNBLOCK,
            success=False,
            dry_run=True,
            idempotency_key=idempotency_key,
            endpoint=f"/unlock-account/{pdv_code}",
            status_code=409,
            response_body={"success": False, "message": "account requires manual review"},
            attempts=1,
        )


def _build_processor(
    tmp_path: Path,
    business_api: BusinessAPI,
) -> tuple[SessionFactory, InboundProcessor, ExecutionService]:
    database_path = tmp_path / "execution-failure.sqlite3"
    engine, session_factory = create_engine_and_session(f"sqlite+pysqlite:///{database_path}")
    create_schema(engine)
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{database_path}",
        authorized_senders="animateur.alpha@example.invalid",
        dry_run=True,
        store_raw_eml=False,
        smtp_from_address="snoc-agent@example.invalid",
        escalation_recipient="human-support@example.invalid",
        execution_correction_grace_seconds=0,
    )
    analyzer = EmailAnalyzer(
        MockLLMBackend([_model_output("scenario_a/analyzer_01_complete_unblock.json")]),
        GenerationConfig(model="accepted-analyzer", temperature=0.0),
    )
    verifier = SemanticVerifier(
        MockLLMBackend([_model_output("scenario_a/verifier_01_complete_unblock_op1.json")]),
        GenerationConfig(model="accepted-verifier", temperature=0.0),
    )
    execution_service = ExecutionService(session_factory, business_api)
    processor = InboundProcessor(
        session_factory=session_factory,
        settings=settings,
        analyzer=analyzer,
        verifier=verifier,
        authorizer=StaticSenderAuthorizer(settings.authorized_sender_set),
        decision_engine=HybridDecisionEngine(),
        execution_service=execution_service,
    )
    return session_factory, processor, execution_service


def _assert_dry_run_has_no_escalation_outbox(
    session_factory: SessionFactory,
) -> None:
    with session_factory() as session:
        outboxes = list(session.scalars(select(OutboxMessage)))

        # Finalization still queues the ordinary terminal summary for the requester.
        assert len(outboxes) == 1
        assert outboxes[0].recipient == "animateur.alpha@example.invalid"
        assert "X-SNOC-Escalation-ID" not in outboxes[0].headers


def test_transport_failure_is_durably_unknown_escalated_and_never_retried(
    tmp_path: Path,
) -> None:
    business_api = TransportFailingBusinessAPI()
    session_factory, processor, execution_service = _build_processor(tmp_path, business_api)

    first = processor.process_raw(RAW_UNBLOCK)

    assert first.status == ProcessingStatus.PROCESSED.value
    assert first.decisions == [
        FinalDecision.AUTO_EXECUTE.value,
        FinalDecision.ESCALATE.value,
    ]
    assert business_api.attempt_count == 1
    assert len(business_api.idempotency_keys) == 1

    with session_factory() as session:
        request = session.scalars(select(BusinessRequest)).one()
        operation = session.scalars(select(Operation)).one()
        execution = session.scalars(select(Execution)).one()
        escalation = session.scalars(select(Escalation)).one()
        inbound = session.get_one(EmailMessage, first.email_message_id)
        operation_id = operation.id

        assert inbound.processing_status == ProcessingStatus.PROCESSED.value
        assert operation.status == OperationStatus.ESCALATED.value
        assert operation.execution_eligible is False
        assert request.status == RequestStatus.ESCALATED.value
        assert execution.status == ExecutionStatus.UNKNOWN.value
        assert execution.attempt_count == 1
        assert execution.response_body == {
            "error": "connection dropped after request submission",
            "exception_type": "BusinessAPITransportError",
        }
        assert escalation.request_id == request.id
        assert escalation.email_message_id == inbound.id
        assert escalation.reason_code == "business_api_unknown_outcome"
        assert escalation.status == "open"
        assert escalation.evidence["operation_id"] == str(operation.id)
        assert escalation.evidence["execution_id"] == str(execution.id)
        assert escalation.evidence["execution_status"] == ExecutionStatus.UNKNOWN.value
        assert "before any retry" in escalation.evidence["recommended_action"]

    replayed_outcome = execution_service.execute(operation_id)
    assert replayed_outcome.status == ExecutionStatus.UNKNOWN
    assert replayed_outcome.detail == "identical operation revision was already recorded"
    assert business_api.attempt_count == 1

    duplicate = processor.process_raw(RAW_UNBLOCK)
    assert duplicate.status == ProcessingStatus.DUPLICATE.value
    assert duplicate.duplicate_of_id == first.email_message_id
    assert business_api.attempt_count == 1
    with session_factory() as session:
        assert len(list(session.scalars(select(Execution)))) == 1
        assert len(list(session.scalars(select(Escalation)))) == 1
    _assert_dry_run_has_no_escalation_outbox(session_factory)


def test_known_unsuccessful_response_uses_business_api_failure_reason(
    tmp_path: Path,
) -> None:
    business_api = RejectingBusinessAPI()
    session_factory, processor, _execution_service = _build_processor(tmp_path, business_api)

    result = processor.process_raw(RAW_UNBLOCK)

    assert result.status == ProcessingStatus.PROCESSED.value
    assert business_api.attempt_count == 1
    with session_factory() as session:
        request = session.scalars(select(BusinessRequest)).one()
        operation = session.scalars(select(Operation)).one()
        execution = session.scalars(select(Execution)).one()
        escalation = session.scalars(select(Escalation)).one()

        assert operation.status == OperationStatus.ESCALATED.value
        assert request.status == RequestStatus.ESCALATED.value
        assert execution.status == ExecutionStatus.FAILED.value
        assert execution.response_status == 409
        assert execution.response_body == {
            "success": False,
            "message": "account requires manual review",
        }
        assert escalation.reason_code == "business_api_failure"
        assert escalation.evidence["execution_status"] == ExecutionStatus.FAILED.value
        assert "complete the operation manually" in escalation.evidence["recommended_action"]
    _assert_dry_run_has_no_escalation_outbox(session_factory)
