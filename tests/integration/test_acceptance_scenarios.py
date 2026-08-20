from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import select

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import GenerationConfig
from snoc_agent.ai.mock_backend import MockLLMBackend
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.business_api import MockBusinessAPI
from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    Clarification,
    EmailMessage,
    Escalation,
    Execution,
    MailAccount,
    ModelRun,
    Operation,
    OutboxMessage,
)
from snoc_agent.db.session import (
    SessionFactory,
    create_engine_and_session,
    create_schema,
)
from snoc_agent.domain.enums import (
    ClarificationStatus,
    Direction,
    ExecutionStatus,
    FinalDecision,
    OperationStatus,
    OutboxStatus,
    ProcessingStatus,
    RequestStatus,
)
from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox, FakeSMTPTransport
from snoc_agent.mail.headers import normalize_message_id
from snoc_agent.mail.interfaces import MailboxMessage
from snoc_agent.workflow.authorizer import StaticSenderAuthorizer
from snoc_agent.workflow.decision_engine import HybridDecisionEngine
from snoc_agent.workflow.execution_service import ExecutionService
from snoc_agent.workflow.inbound_processor import InboundProcessor
from snoc_agent.workflow.orchestrator import MailOrchestrator
from snoc_agent.workflow.outbox_service import OutboxService

FIXTURES = Path(__file__).parents[1] / "fixtures"
EMAILS = FIXTURES / "emails"
MODEL_OUTPUTS = FIXTURES / "model_outputs"


@dataclass(slots=True)
class Harness:
    session_factory: SessionFactory
    processor: InboundProcessor
    outbox: OutboxService
    smtp: FakeSMTPTransport
    business_api: MockBusinessAPI
    analyzer_backend: MockLLMBackend
    verifier_backend: MockLLMBackend


def _json_outputs(*relative_paths: str) -> list[dict[str, object]]:
    return [
        json.loads((MODEL_OUTPUTS / relative_path).read_text(encoding="utf-8"))
        for relative_path in relative_paths
    ]


def _eml(relative_path: str) -> bytes:
    return (EMAILS / relative_path).read_bytes()


def _build_harness(
    tmp_path: Path,
    *,
    sender: str,
    analyzer_outputs: list[dict[str, object]],
    verifier_outputs: list[dict[str, object]],
) -> Harness:
    database_path = tmp_path / "acceptance.sqlite3"
    engine, session_factory = create_engine_and_session(f"sqlite+pysqlite:///{database_path}")
    create_schema(engine)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+pysqlite:///{database_path}",
        authorized_senders=sender,
        dry_run=True,
        store_raw_eml=False,
        smtp_from_address="snoc-agent@example.invalid",
        escalation_recipient="human-support@example.invalid",
        max_clarification_rounds=1,
        execution_correction_grace_seconds=0,
    )
    analyzer_backend = MockLLMBackend(analyzer_outputs)
    verifier_backend = MockLLMBackend(verifier_outputs)
    analyzer = EmailAnalyzer(
        analyzer_backend,
        GenerationConfig(model="fixture-analyzer", temperature=0.0),
    )
    verifier = SemanticVerifier(
        verifier_backend,
        GenerationConfig(model="fixture-verifier", temperature=0.0),
    )
    business_api = MockBusinessAPI()
    processor = InboundProcessor(
        session_factory=session_factory,
        settings=settings,
        analyzer=analyzer,
        verifier=verifier,
        authorizer=StaticSenderAuthorizer(settings.authorized_sender_set),
        decision_engine=HybridDecisionEngine(),
        execution_service=ExecutionService(session_factory, business_api),
    )
    smtp = FakeSMTPTransport()
    outbox = OutboxService(
        session_factory,
        smtp,
        sender=settings.smtp_from_address,
    )
    return Harness(
        session_factory=session_factory,
        processor=processor,
        outbox=outbox,
        smtp=smtp,
        business_api=business_api,
        analyzer_backend=analyzer_backend,
        verifier_backend=verifier_backend,
    )


def _one(session_factory: SessionFactory, model: type[object]):
    with session_factory() as session:
        return session.scalars(select(model)).one()


def _replace_reply_target(
    raw: bytes,
    *,
    fixture_message_id: str,
    actual_message_id: str,
    fixture_reference: str,
    actual_reference: str,
) -> bytes:
    replaced = raw.replace(fixture_message_id.encode(), actual_message_id.encode())
    replaced = replaced.replace(fixture_reference.encode(), actual_reference.encode())
    assert replaced != raw
    return replaced


def _clarification_linkage(
    session_factory: SessionFactory,
) -> tuple[Clarification, BusinessRequest, EmailMessage]:
    with session_factory() as session:
        clarification = session.scalars(select(Clarification)).one()
        request = session.get(BusinessRequest, clarification.request_id)
        outbound = session.get(EmailMessage, clarification.outbound_email_id)
        assert request is not None and outbound is not None and outbound.rfc_message_id is not None
        session.expunge(clarification)
        session.expunge(request)
        session.expunge(outbound)
        return clarification, request, outbound


def test_scenario_a_complete_unblock_executes_once_and_sends_summary(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="animateur.alpha@example.invalid",
        analyzer_outputs=_json_outputs("scenario_a/analyzer_01_complete_unblock.json"),
        verifier_outputs=_json_outputs("scenario_a/verifier_01_complete_unblock_op1.json"),
    )

    result = harness.processor.process_raw(
        _eml("scenario_a_complete_unblock/01_complete_unblock.eml")
    )

    assert result.status == ProcessingStatus.PROCESSED.value
    assert result.decisions == [FinalDecision.AUTO_EXECUTE.value]
    with harness.session_factory() as session:
        request = session.scalars(select(BusinessRequest)).one()
        operation = session.scalars(select(Operation)).one()
        execution = session.scalars(select(Execution)).one()
        outbox = session.scalars(select(OutboxMessage)).one()
        assert request.status == RequestStatus.COMPLETED.value
        assert request.latest_completion_marker is not None
        assert operation.action == "account_unblock"
        assert operation.pdv_code == "12000001"
        assert operation.status == OperationStatus.COMPLETED.value
        assert execution.status == ExecutionStatus.SUCCEEDED.value
        assert execution.dry_run is True
        assert outbox.status == OutboxStatus.PENDING.value
        assert "SNOC_REQUEST_CLOSED" not in outbox.body
        assert "OP-01" not in outbox.body
        assert operation.pdv_code not in outbox.body
        assert "se terminant par 0001" in outbox.body
        assert outbox.headers["X-SNOC-Reply-Type"] == "completion"
        assert "X-SNOC-Operation-IDs" not in outbox.headers
    assert len(harness.business_api.calls) == 1


def test_fake_imap_orchestrator_persists_uid_identity_and_safe_rediscovery(
    tmp_path: Path,
) -> None:
    harness = _build_harness(
        tmp_path,
        sender="animateur.alpha@example.invalid",
        analyzer_outputs=_json_outputs("scenario_a/analyzer_01_complete_unblock.json"),
        verifier_outputs=_json_outputs("scenario_a/verifier_01_complete_unblock_op1.json"),
    )
    with harness.session_factory() as session:
        account = MailAccount(name="fixture-imap", mailbox="INBOX")
        session.add(account)
        session.commit()
        account_id = account.id
    mailbox = FakeIMAPMailbox(
        [
            MailboxMessage(
                mailbox="INBOX",
                uidvalidity=42,
                uid=101,
                raw_message=_eml("scenario_a_complete_unblock/01_complete_unblock.eml"),
                flags=("\\Recent",),
            )
        ]
    )
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=harness.processor,
        mail_account_id=account_id,
    )

    first = orchestrator.poll_once()
    second = orchestrator.poll_once()

    assert first[0].decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert second[0].status == ProcessingStatus.DUPLICATE.value
    assert second[0].duplicate_of_id == first[0].email_message_id
    assert len(harness.business_api.calls) == 1
    with harness.session_factory() as session:
        account = session.get_one(MailAccount, account_id)
        inbound = session.get_one(EmailMessage, first[0].email_message_id)
        assert account.last_uidvalidity == 42
        assert account.polling_checkpoint == 101
        assert inbound.imap_uid == 101
        assert inbound.uidvalidity == 42
        assert inbound.flags_json == ["\\Recent"]


def test_reply_to_cannot_redirect_request_output_to_another_address(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="animateur.alpha@example.invalid",
        analyzer_outputs=_json_outputs("scenario_a/analyzer_01_complete_unblock.json"),
        verifier_outputs=_json_outputs("scenario_a/verifier_01_complete_unblock_op1.json"),
    )
    raw = _eml("scenario_a_complete_unblock/01_complete_unblock.eml").replace(
        b"To: Agent SNOC <snoc-agent@example.invalid>\n",
        b"To: Agent SNOC <snoc-agent@example.invalid>\nReply-To: exfil@example.invalid\n",
    )

    result = harness.processor.process_raw(raw)

    assert result.decisions == [FinalDecision.AUTO_EXECUTE.value]
    with harness.session_factory() as session:
        inbound = session.get(EmailMessage, result.email_message_id)
        outbox = session.scalars(select(OutboxMessage)).one()
        assert inbound is not None
        assert "reply_to_sender_mismatch_ignored" in inbound.parsing_warnings
        assert outbox.recipient == "animateur.alpha@example.invalid"


def test_authorized_sender_cannot_reuse_another_senders_thread_headers(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender=("animateur.alpha@example.invalid,authorized.attacker@example.invalid"),
        analyzer_outputs=_json_outputs("scenario_a/analyzer_01_complete_unblock.json"),
        verifier_outputs=_json_outputs("scenario_a/verifier_01_complete_unblock_op1.json"),
    )
    first = harness.processor.process_raw(
        _eml("scenario_a_complete_unblock/01_complete_unblock.eml")
    )
    attacker_reply = b"""From: Authorized Attacker <authorized.attacker@example.invalid>
To: Agent SNOC <snoc-agent@example.invalid>
Message-ID: <cross-sender-reply@example.invalid>
In-Reply-To: <a-complete-unblock-001@fixtures.snoc.invalid>
Subject: Re: Demande de deblocage du compte PDV
Content-Type: text/plain; charset=UTF-8

Merci de relancer cette demande.
"""

    second = harness.processor.process_raw(attacker_reply)

    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert second.decisions == [FinalDecision.ESCALATE.value]
    assert second.detail == "correlation conflict"
    assert len(harness.business_api.calls) == 1
    with harness.session_factory() as session:
        attacker_email = session.get(EmailMessage, second.email_message_id)
        escalation = session.scalars(
            select(Escalation).where(Escalation.email_message_id == second.email_message_id)
        ).one()
        assert attacker_email is not None
        assert attacker_email.correlation_details["conflicts"] == ["header_sender_mismatch"]
        assert escalation.reason_code == "request_correlation_conflict"
    assert harness.outbox.send_once() == (1, 0)
    assert len(harness.smtp.sent) == 1


def test_scenario_b_correlated_phone_reply_resolves_clarification(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="superviseur.beta@example.invalid",
        analyzer_outputs=_json_outputs(
            "scenario_b/analyzer_01_incomplete_otp.json",
            "scenario_b/analyzer_03_reply_phone_only.json",
        ),
        verifier_outputs=_json_outputs(
            "scenario_b/verifier_01_incomplete_otp_op1.json",
            "scenario_b/verifier_03_reply_phone_only_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_b_otp_clarification/01_incomplete_otp.eml")
    )

    assert first.decisions == [FinalDecision.ASK_FOR_INFORMATION.value]
    clarification, request, outbound = _clarification_linkage(harness.session_factory)
    assert outbound.subject == "Re: Changement numero OTP PDV 22000001"
    assert outbound.in_reply_to == "<b-incomplete-otp-001@fixtures.snoc.invalid>"
    assert outbound.references_json[-1] == "<b-incomplete-otp-001@fixtures.snoc.invalid>"
    assert "OP-01" not in outbound.raw_text
    assert "22000001" not in outbound.raw_text
    assert "se terminant par 0001" in outbound.raw_text
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        clarification_outbox = session.scalars(select(OutboxMessage)).one()
        assert operation.status == OperationStatus.NEEDS_INFORMATION.value
        assert operation.missing_fields == ["new_phone"]
        assert operation.pdv_code == "22000001"
        assert operation.phone is None
        assert clarification.requested_fields[str(operation.id)] == ["new_phone"]
        assert clarification_outbox.headers["X-SNOC-Reply-Type"] == "clarification"
        assert "X-SNOC-Clarification-ID" not in clarification_outbox.headers
        assert "X-SNOC-Operation-IDs" not in clarification_outbox.headers
    assert harness.outbox.send_once() == (1, 0)

    reply = _replace_reply_target(
        _eml("scenario_b_otp_clarification/03_reply_phone_only.eml"),
        fixture_message_id="<clarification-b-002@snoc-agent.invalid>",
        actual_message_id=outbound.rfc_message_id or "",
        fixture_reference="SNOC-REQ-B00000000001",
        actual_reference=request.public_reference,
    )
    second = harness.processor.process_raw(reply)

    assert second.request_ids == first.request_ids
    assert second.decisions == [FinalDecision.AUTO_EXECUTE.value]
    with harness.session_factory() as session:
        stored_request = session.get(BusinessRequest, first.request_ids[0])
        operation = session.scalars(select(Operation)).one()
        stored_clarification = session.get(Clarification, clarification.id)
        reply_email = session.get(EmailMessage, second.email_message_id)
        executions = list(session.scalars(select(Execution)))
        assert stored_request is not None and stored_request.status == RequestStatus.COMPLETED.value
        assert operation.status == OperationStatus.COMPLETED.value
        assert operation.phone == "0770000001"
        assert operation.current_revision == 2
        assert len(executions) == 1
        assert stored_clarification is not None
        assert stored_clarification.status == ClarificationStatus.RESOLVED.value
        assert stored_clarification.reply_email_id == second.email_message_id
        assert reply_email is not None
        assert reply_email.in_reply_to == normalize_message_id(outbound.rfc_message_id)
    assert len(harness.business_api.calls) == 1
    assert harness.outbox.send_once() == (1, 0)
    assert len(harness.smtp.sent) == 2


def test_correlation_overrides_false_correction_labels_during_clarification(
    tmp_path: Path,
) -> None:
    first_analysis, reply_analysis = _json_outputs(
        "scenario_b/analyzer_01_incomplete_otp.json",
        "scenario_b/analyzer_03_reply_phone_only.json",
    )
    first_analysis["message_kind"] = "correction"
    reply_analysis["message_kind"] = "correction"
    reply_operations = reply_analysis["operations"]
    assert isinstance(reply_operations, list)
    reply_evidence = reply_operations[0]["evidence"]
    assert isinstance(reply_evidence, list)
    reply_evidence[0]["source"] = "previous_agent_question"
    harness = _build_harness(
        tmp_path,
        sender="superviseur.beta@example.invalid",
        analyzer_outputs=[first_analysis, reply_analysis],
        verifier_outputs=_json_outputs(
            "scenario_b/verifier_01_incomplete_otp_op1.json",
            "scenario_b/verifier_03_reply_phone_only_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_b_otp_clarification/01_incomplete_otp.eml")
    )

    assert first.decisions == [FinalDecision.ASK_FOR_INFORMATION.value]
    clarification, request, outbound = _clarification_linkage(harness.session_factory)
    with harness.session_factory() as session:
        stored_request = session.get_one(BusinessRequest, request.id)
        assert stored_request.request_kind == "new"
    assert harness.outbox.send_once() == (1, 0)
    reply = _replace_reply_target(
        _eml("scenario_b_otp_clarification/03_reply_phone_only.eml"),
        fixture_message_id="<clarification-b-002@snoc-agent.invalid>",
        actual_message_id=outbound.rfc_message_id or "",
        fixture_reference="SNOC-REQ-B00000000001",
        actual_reference=request.public_reference,
    )

    second = harness.processor.process_raw(reply)

    assert second.request_ids == first.request_ids
    assert second.decisions == [FinalDecision.AUTO_EXECUTE.value]
    with harness.session_factory() as session:
        operation = session.scalars(select(Operation)).one()
        stored_clarification = session.get_one(Clarification, clarification.id)
        executions = list(session.scalars(select(Execution)))
        assert operation.status == OperationStatus.COMPLETED.value
        assert operation.pdv_code == "22000001"
        assert operation.phone == "0770000001"
        assert operation.field_provenance == {
            "pdv_code": "stored_request_state",
            "new_phone": "latest_user_message",
        }
        assert stored_clarification.status == ClarificationStatus.RESOLVED.value
        assert len(executions) == 1
    assert len(harness.business_api.calls) == 1


def test_clarification_with_mismatched_action_cannot_mutate_or_execute_target(
    tmp_path: Path,
) -> None:
    mismatched_reply = _json_outputs("scenario_b/analyzer_03_reply_phone_only.json")[0]
    reply_operations = mismatched_reply["operations"]
    assert isinstance(reply_operations, list)
    reply_proposal = reply_operations[0]
    assert isinstance(reply_proposal, dict)
    reply_proposal["action"] = "vpn_access"
    harness = _build_harness(
        tmp_path,
        sender="superviseur.beta@example.invalid",
        analyzer_outputs=[
            *_json_outputs("scenario_b/analyzer_01_incomplete_otp.json"),
            mismatched_reply,
        ],
        verifier_outputs=_json_outputs(
            "scenario_b/verifier_01_incomplete_otp_op1.json",
            "scenario_b/verifier_03_reply_phone_only_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_b_otp_clarification/01_incomplete_otp.eml")
    )
    assert first.decisions == [FinalDecision.ASK_FOR_INFORMATION.value]
    _clarification, request, outbound = _clarification_linkage(harness.session_factory)
    assert harness.outbox.send_once() == (1, 0)
    reply = _replace_reply_target(
        _eml("scenario_b_otp_clarification/03_reply_phone_only.eml"),
        fixture_message_id="<clarification-b-002@snoc-agent.invalid>",
        actual_message_id=outbound.rfc_message_id or "",
        fixture_reference="SNOC-REQ-B00000000001",
        actual_reference=request.public_reference,
    )

    second = harness.processor.process_raw(reply)

    assert second.request_ids == first.request_ids
    assert second.decisions == [FinalDecision.ESCALATE.value]
    assert harness.business_api.calls == []
    with harness.session_factory() as session:
        stored_request = session.get_one(BusinessRequest, request.id)
        operation = session.scalars(select(Operation)).one()
        escalation = session.scalars(
            select(Escalation).where(Escalation.email_message_id == second.email_message_id)
        ).one()

        assert operation.action == "otp_number_change"
        assert operation.pdv_code == "22000001"
        assert operation.phone is None
        assert operation.additional_payload == {}
        assert operation.current_revision == 1
        assert operation.status == OperationStatus.ESCALATED.value
        assert operation.final_decision == FinalDecision.ESCALATE.value
        assert operation.contradiction_data == {
            "type": "action_changed_in_clarification",
            "proposed_action": "vpn_access",
        }
        assert stored_request.status == RequestStatus.ESCALATED.value
        assert session.scalars(select(Execution)).all() == []
        assert escalation.reason_code == FinalDecision.ESCALATE.value.casefold()
        assert "proposal_action_differs_from_stored_operation" in escalation.evidence["reasons"]


def test_scenario_c_partial_completion_then_targeted_operation_only(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="animateur.gamma@example.invalid",
        analyzer_outputs=_json_outputs(
            "scenario_c/analyzer_01_three_operations_one_incomplete.json",
            "scenario_c/analyzer_02_targeted_phone_reply.json",
        ),
        verifier_outputs=_json_outputs(
            "scenario_c/verifier_01_unblock_op1.json",
            "scenario_c/verifier_01_reset_op2.json",
            "scenario_c/verifier_01_vpn_op3.json",
            "scenario_c/verifier_02_targeted_phone_reply_op3.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_c_multi_operation/01_three_operations_one_incomplete.eml")
    )

    assert first.decisions == [
        FinalDecision.AUTO_EXECUTE.value,
        FinalDecision.AUTO_EXECUTE.value,
        FinalDecision.ASK_FOR_INFORMATION.value,
    ]
    clarification, request, outbound = _clarification_linkage(harness.session_factory)
    with harness.session_factory() as session:
        operations = list(session.scalars(select(Operation).order_by(Operation.sequence_number)))
        assert [operation.status for operation in operations] == [
            OperationStatus.COMPLETED.value,
            OperationStatus.COMPLETED.value,
            OperationStatus.NEEDS_INFORMATION.value,
        ]
        assert (
            session.get(BusinessRequest, request.id).status
            == RequestStatus.PARTIALLY_COMPLETED.value
        )
        assert clarification.target_operation_ids == [str(operations[2].id)]
        assert len(list(session.scalars(select(Execution)))) == 2
    assert len(harness.business_api.calls) == 2
    assert harness.outbox.send_once() == (1, 0)

    reply = _replace_reply_target(
        _eml("scenario_c_multi_operation/02_targeted_phone_reply.eml"),
        fixture_message_id="<clarification-c-001@snoc-agent.invalid>",
        actual_message_id=outbound.rfc_message_id or "",
        fixture_reference="SNOC-REQ-C00000000001",
        actual_reference=request.public_reference,
    )
    second = harness.processor.process_raw(reply)

    assert second.request_ids == first.request_ids
    assert second.decisions == [FinalDecision.AUTO_EXECUTE.value]
    with harness.session_factory() as session:
        operations = list(session.scalars(select(Operation).order_by(Operation.sequence_number)))
        stored_request = session.get(BusinessRequest, request.id)
        stored_clarification = session.get(Clarification, clarification.id)
        assert [operation.status for operation in operations] == [
            OperationStatus.COMPLETED.value,
            OperationStatus.COMPLETED.value,
            OperationStatus.COMPLETED.value,
        ]
        assert [operation.current_revision for operation in operations] == [1, 1, 2]
        assert operations[2].phone == "0770000003"
        assert stored_request is not None and stored_request.status == RequestStatus.COMPLETED.value
        assert stored_clarification is not None
        assert stored_clarification.status == ClarificationStatus.RESOLVED.value
        assert len(list(session.scalars(select(Execution)))) == 3
    assert len(harness.business_api.calls) == 3
    assert harness.outbox.send_once() == (1, 0)


def test_scenario_d_reused_completed_chain_creates_isolated_new_request(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="superviseur.delta@example.invalid",
        analyzer_outputs=_json_outputs(
            "scenario_d/analyzer_01_original_complete_vpn.json",
            "scenario_d/analyzer_02_reused_chain_new_reset.json",
        ),
        verifier_outputs=_json_outputs(
            "scenario_d/verifier_01_original_complete_vpn_op1.json",
            "scenario_d/verifier_02_reused_chain_new_reset_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_d_reused_chain/01_original_complete_vpn.eml")
    )
    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert harness.outbox.send_once() == (1, 0)
    second = harness.processor.process_raw(
        _eml("scenario_d_reused_chain/02_reused_chain_new_reset.eml")
    )

    assert second.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert second.conversation_id == first.conversation_id
    assert second.request_ids != first.request_ids
    with harness.session_factory() as session:
        requests = list(
            session.scalars(select(BusinessRequest).order_by(BusinessRequest.created_at))
        )
        operations = list(session.scalars(select(Operation).order_by(Operation.created_at)))
        analysis_runs = list(
            session.scalars(
                select(ModelRun).where(ModelRun.stage == "analysis").order_by(ModelRun.created_at)
            )
        )
        assert len(requests) == 2
        assert {request.conversation_id for request in requests} == {first.conversation_id}
        assert [request.status for request in requests] == [
            RequestStatus.COMPLETED.value,
            RequestStatus.COMPLETED.value,
        ]
        assert [
            (operation.action, operation.pdv_code, operation.phone) for operation in operations
        ] == [
            ("vpn_access", "42000001", "0770000010"),
            ("password_reset", "42000002", None),
        ]
        assert len(analysis_runs) == 2
        latest_context = analysis_runs[1].input_context
        assert "42000002" in str(latest_context["latest_user_message"])
        assert "42000001" not in str(latest_context["latest_user_message"])
        assert "0770000010" not in str(latest_context["latest_user_message"])
        assert [item["value"] for item in latest_context["numeric_candidates"]] == ["42000002"]
        assert latest_context["closed_history_summary"] is None
    assert len(harness.business_api.calls) == 2
    assert harness.outbox.send_once() == (1, 0)
    assert len(harness.smtp.sent) == 2


def test_explicit_new_request_in_completed_thread_survives_correction_mislabel(
    tmp_path: Path,
) -> None:
    original_analysis, reused_analysis = _json_outputs(
        "scenario_d/analyzer_01_original_complete_vpn.json",
        "scenario_d/analyzer_02_reused_chain_new_reset.json",
    )
    reused_analysis["message_kind"] = "correction"
    harness = _build_harness(
        tmp_path,
        sender="superviseur.delta@example.invalid",
        analyzer_outputs=[original_analysis, reused_analysis],
        verifier_outputs=_json_outputs(
            "scenario_d/verifier_01_original_complete_vpn_op1.json",
            "scenario_d/verifier_02_reused_chain_new_reset_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_d_reused_chain/01_original_complete_vpn.eml")
    )
    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert harness.outbox.send_once() == (1, 0)
    second = harness.processor.process_raw(
        _eml("scenario_d_reused_chain/02_reused_chain_new_reset.eml")
    )

    assert second.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert second.conversation_id == first.conversation_id
    assert second.request_ids != first.request_ids
    with harness.session_factory() as session:
        requests = list(
            session.scalars(select(BusinessRequest).order_by(BusinessRequest.created_at))
        )
        operations = list(session.scalars(select(Operation).order_by(Operation.created_at)))
        assert len(requests) == 2
        assert [request.request_kind for request in requests] == ["new", "new"]
        assert [
            (operation.action, operation.pdv_code, operation.current_revision)
            for operation in operations
        ] == [
            ("vpn_access", "42000001", 1),
            ("password_reset", "42000002", 1),
        ]
        assert len(list(session.scalars(select(Execution)))) == 2
    assert len(harness.business_api.calls) == 2


def test_scenario_e_two_open_requests_make_uncorrelated_reply_escalate(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="animateur.epsilon@example.invalid",
        analyzer_outputs=_json_outputs(
            "scenario_e/analyzer_01_unresolved_otp.json",
            "scenario_e/analyzer_02_second_unresolved_vpn.json",
        ),
        verifier_outputs=_json_outputs(
            "scenario_e/verifier_01_unresolved_otp_op1.json",
            "scenario_e/verifier_02_second_unresolved_vpn_op1.json",
        ),
    )

    first = harness.processor.process_raw(
        _eml("scenario_e_uncorrelated_reply/01_unresolved_otp.eml")
    )
    assert first.decisions == [FinalDecision.ASK_FOR_INFORMATION.value]
    assert harness.outbox.send_once() == (1, 0)
    second = harness.processor.process_raw(
        _eml("scenario_e_uncorrelated_reply/02_second_unresolved_vpn_same_chain.eml")
    )
    assert second.decisions == [FinalDecision.ASK_FOR_INFORMATION.value]
    assert second.conversation_id == first.conversation_id
    assert second.request_ids != first.request_ids
    assert harness.outbox.send_once() == (1, 0)

    third = harness.processor.process_raw(
        _eml("scenario_e_uncorrelated_reply/03_uncorrelated_phone_reply.eml")
    )

    assert third.status == ProcessingStatus.PROCESSED.value
    assert third.decisions == [FinalDecision.ESCALATE.value]
    assert third.detail == "correlation conflict"
    with harness.session_factory() as session:
        requests = list(session.scalars(select(BusinessRequest)))
        operations = list(session.scalars(select(Operation)))
        escalations = list(session.scalars(select(Escalation)))
        email = session.get(EmailMessage, third.email_message_id)
        assert len(requests) == 2
        assert {request.status for request in requests} == {RequestStatus.NEEDS_INFORMATION.value}
        assert len(operations) == 2
        assert all(
            operation.status == OperationStatus.NEEDS_INFORMATION.value for operation in operations
        )
        assert len(escalations) == 1
        assert escalations[0].reason_code == "request_correlation_conflict"
        assert email is not None
        assert email.correlation_details["conflicts"] == ["multiple_open_requests"]
    assert len(harness.business_api.calls) == 0
    assert len(harness.analyzer_backend.calls) == 2
    assert harness.outbox.send_once() == (0, 0)


def test_scenario_f_duplicate_replay_records_one_execution_and_api_call(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="superviseur.zeta@example.invalid",
        analyzer_outputs=_json_outputs("scenario_f/analyzer_01_password_reset.json"),
        verifier_outputs=_json_outputs("scenario_f/verifier_01_password_reset_op1.json"),
    )
    raw = _eml("scenario_f_idempotency/01_password_reset_replay_twice.eml")

    first = harness.processor.process_raw(raw)
    duplicate = harness.processor.process_raw(raw)

    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert duplicate.status == ProcessingStatus.DUPLICATE.value
    assert duplicate.duplicate_of_id == first.email_message_id
    with harness.session_factory() as session:
        requests = list(session.scalars(select(BusinessRequest)))
        operations = list(session.scalars(select(Operation)))
        executions = list(session.scalars(select(Execution)))
        inbound = list(
            session.scalars(
                select(EmailMessage).where(EmailMessage.direction == Direction.INBOUND.value)
            )
        )
        assert len(requests) == 1
        assert len(operations) == 1
        assert operations[0].status == OperationStatus.COMPLETED.value
        assert len(executions) == 1
        assert executions[0].status == ExecutionStatus.SUCCEEDED.value
        assert len(inbound) == 2
        assert sum(message.duplicate_of_id is not None for message in inbound) == 1
    assert len(harness.business_api.calls) == 1
    assert len(harness.analyzer_backend.calls) == 1
    assert len(harness.verifier_backend.calls) == 1
    assert harness.outbox.send_once() == (1, 0)


def test_same_logical_body_with_new_message_id_is_duplicate(tmp_path: Path) -> None:
    harness = _build_harness(
        tmp_path,
        sender="superviseur.zeta@example.invalid",
        analyzer_outputs=_json_outputs("scenario_f/analyzer_01_password_reset.json"),
        verifier_outputs=_json_outputs("scenario_f/verifier_01_password_reset_op1.json"),
    )
    raw = _eml("scenario_f_idempotency/01_password_reset_replay_twice.eml")
    replay = raw.replace(
        b"<f-idempotent-reset-001@fixtures.snoc.invalid>",
        b"<f-idempotent-reset-resend@fixtures.snoc.invalid>",
    )
    assert replay != raw

    first = harness.processor.process_raw(raw)
    duplicate = harness.processor.process_raw(replay)

    assert first.decisions == [FinalDecision.AUTO_EXECUTE.value]
    assert duplicate.status == ProcessingStatus.DUPLICATE.value
    assert duplicate.duplicate_of_id == first.email_message_id
    assert len(harness.business_api.calls) == 1
    assert len(harness.analyzer_backend.calls) == 1


@pytest.mark.parametrize(
    ("action", "body"),
    [
        ("otp_number_change", "This is not an OTP request. PDV 62000001, 0770000000."),
        ("password_reset", "Do not reset the password for PDV 62000001."),
        ("vpn_access", "No VPN access is required for PDV 62000001."),
        ("account_unblock", "The account is not locked. PDV 62000001."),
    ],
)
def test_model_false_positive_in_negated_email_creates_no_request(
    tmp_path: Path,
    action: str,
    body: str,
) -> None:
    analyzer_output = {
        "message_kind": "new_request",
        "referenced_existing_operation_ids": [],
        "operations": [
            {
                "local_operation_id": "OP-1",
                "action": action,
                "pdv_code": "62000001",
                "phone": "0770000000" if action in {"otp_number_change", "vpn_access"} else None,
                "additional_fields": {},
                "missing_fields": [],
                "evidence": [],
                "ambiguity_reasons": [],
                "raw_action_confidence": 0.99,
                "raw_field_confidence": {},
            }
        ],
        "new_request_present": True,
        "contradiction_with_stored_state": False,
        "contradiction_details": [],
        "unresolved_ambiguities": [],
    }
    harness = _build_harness(
        tmp_path,
        sender="superviseur.zeta@example.invalid",
        analyzer_outputs=[analyzer_output],
        verifier_outputs=[],
    )
    raw = (
        "From: Superviseur Zeta <superviseur.zeta@example.invalid>\n"
        "To: snoc-agent@example.invalid\n"
        f"Message-ID: <negation-{action}@example.invalid>\n"
        "Subject: Reporting issue\n"
        "Content-Type: text/plain; charset=UTF-8\n"
        "\n"
        f"{body}\n"
    ).encode()

    result = harness.processor.process_raw(raw)

    assert result.status == ProcessingStatus.IGNORED.value
    assert result.decisions == [FinalDecision.IGNORE.value]
    assert result.request_ids == []
    assert result.operation_ids == []
    with harness.session_factory() as session:
        assert session.query(BusinessRequest).count() == 0
        assert session.query(Operation).count() == 0
        email = session.get_one(EmailMessage, result.email_message_id)
        assert "intent_safety:negated_operation" in email.parsing_warnings
    assert harness.business_api.calls == []
    assert harness.verifier_backend.calls == []
