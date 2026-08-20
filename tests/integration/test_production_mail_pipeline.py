"""High-value production-style coverage from IMAP metadata through decisions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import select

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import GenerationConfig
from snoc_agent.ai.mock_backend import MockLLMBackend
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.business_api import MockBusinessAPI
from snoc_agent.cli import commands
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
)
from snoc_agent.db.session import create_engine_and_session, create_schema
from snoc_agent.domain.enums import (
    Direction,
    FinalDecision,
    OperationStatus,
    ProcessingStatus,
    RequestStatus,
)
from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox
from snoc_agent.mail.interfaces import MailboxMessage
from snoc_agent.workflow.authorizer import StaticSenderAuthorizer
from snoc_agent.workflow.decision_engine import HybridDecisionEngine
from snoc_agent.workflow.execution_service import ExecutionService
from snoc_agent.workflow.inbound_processor import InboundProcessor
from snoc_agent.workflow.orchestrator import MailOrchestrator

FIXTURES = Path(__file__).parents[1] / "fixtures"
EMAILS = FIXTURES / "emails"
MODEL_OUTPUTS = FIXTURES / "model_outputs"


def _fixture(path: str) -> bytes:
    return (EMAILS / path).read_bytes()


def _outputs(*paths: str) -> list[dict[str, Any] | BaseModel]:
    return [json.loads((MODEL_OUTPUTS / path).read_text(encoding="utf-8")) for path in paths]


def _unauthorized_request() -> bytes:
    return b"""From: Unknown Sender <unknown@example.invalid>
To: Agent SNOC <snoc-agent@example.invalid>
Date: Wed, 01 Jul 2026 08:17:00 +0000
Message-ID: <unauthorized-reset-production@example.invalid>
Subject: Reset password PDV 99000001
MIME-Version: 1.0
Content-Type: text/plain; charset=UTF-8

Merci de reinitialiser le mot de passe du PDV 99000001.
"""


def test_imap_batch_preserves_metadata_and_gates_model_and_execution(
    tmp_path: Path, capsys
) -> None:
    """One batch covers accepted, automated, unauthorized, and unresolved mail."""

    database = tmp_path / "production-pipeline.sqlite3"
    engine, session_factory = create_engine_and_session(f"sqlite+pysqlite:///{database}")
    create_schema(engine)
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=f"sqlite+pysqlite:///{database}",
        authorized_senders=("animateur.alpha@example.invalid,superviseur.beta@example.invalid"),
        dry_run=True,
        store_raw_eml=False,
        smtp_from_address="snoc-agent@example.invalid",
        escalation_recipient="human-support@example.invalid",
        execution_correction_grace_seconds=0,
    )
    analyzer_backend = MockLLMBackend(
        _outputs(
            "scenario_a/analyzer_01_complete_unblock.json",
            "scenario_b/analyzer_01_incomplete_otp.json",
        )
    )
    verifier_backend = MockLLMBackend(
        _outputs(
            "scenario_a/verifier_01_complete_unblock_op1.json",
            "scenario_b/verifier_01_incomplete_otp_op1.json",
        )
    )
    business_api = MockBusinessAPI()
    processor = InboundProcessor(
        session_factory=session_factory,
        settings=settings,
        analyzer=EmailAnalyzer(
            analyzer_backend,
            GenerationConfig(model="production-fixture-analyzer", temperature=0),
        ),
        verifier=SemanticVerifier(
            verifier_backend,
            GenerationConfig(model="production-fixture-verifier", temperature=0),
        ),
        authorizer=StaticSenderAuthorizer(settings.authorized_sender_set),
        decision_engine=HybridDecisionEngine(),
        execution_service=ExecutionService(session_factory, business_api),
    )
    with session_factory() as session:
        account = MailAccount(name="production-inbox", mailbox="INBOX")
        session.add(account)
        session.commit()
        account_id = account.id

    received_at = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
    mailbox = FakeIMAPMailbox(
        [
            MailboxMessage(
                mailbox="INBOX",
                uidvalidity=9001,
                uid=101,
                raw_message=_fixture("scenario_a_complete_unblock/01_complete_unblock.eml"),
                internal_date=received_at,
                flags=("\\Recent", "\\Flagged"),
            ),
            MailboxMessage(
                mailbox="INBOX",
                uidvalidity=9001,
                uid=102,
                raw_message=_fixture("edge_cases/16_out_of_office.eml"),
                internal_date=received_at + timedelta(minutes=1),
                flags=("\\Seen",),
            ),
            MailboxMessage(
                mailbox="INBOX",
                uidvalidity=9001,
                uid=103,
                raw_message=_unauthorized_request(),
                internal_date=received_at + timedelta(minutes=2),
                flags=(),
            ),
            MailboxMessage(
                mailbox="INBOX",
                uidvalidity=9001,
                uid=104,
                raw_message=_fixture("scenario_b_otp_clarification/01_incomplete_otp.eml"),
                internal_date=received_at + timedelta(minutes=3),
                flags=("\\Recent",),
            ),
        ]
    )
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=processor,
        mail_account_id=account_id,
    )

    first_poll = orchestrator.poll_once()

    assert [result.status for result in first_poll] == [
        ProcessingStatus.PROCESSED.value,
        ProcessingStatus.IGNORED.value,
        ProcessingStatus.PROCESSED.value,
        ProcessingStatus.PROCESSED.value,
    ]
    assert [result.decisions for result in first_poll] == [
        [FinalDecision.AUTO_EXECUTE.value],
        [],
        [FinalDecision.ESCALATE.value],
        [FinalDecision.ASK_FOR_INFORMATION.value],
    ]
    assert len(analyzer_backend.calls) == 2
    assert len(verifier_backend.calls) == 2
    assert len(business_api.calls) == 1

    commands.audit_list(settings, limit=10)
    listed = json.loads(capsys.readouterr().out)
    assert len(listed) == 4
    assert listed[0]["model_stages"] == ["analysis", "verification"]

    commands.audit_show(settings, first_poll[3].email_message_id)
    audit = json.loads(capsys.readouterr().out)
    assert audit["email"]["imap"]["uid"] == 104
    assert [run["stage"] for run in audit["model_runs"]] == ["analysis", "verification"]
    assert audit["validation_decisions"][0]["decision"] == FinalDecision.ASK_FOR_INFORMATION.value
    assert audit["clarifications"][0]["status"] == "pending_send"

    with session_factory() as session:
        persisted_account = session.get(MailAccount, account_id)
        emails = list(
            session.scalars(
                select(EmailMessage)
                .where(EmailMessage.direction == Direction.INBOUND.value)
                .order_by(EmailMessage.imap_uid)
            )
        )
        requests = list(
            session.scalars(select(BusinessRequest).order_by(BusinessRequest.created_at))
        )
        operations = list(session.scalars(select(Operation).order_by(Operation.created_at)))
        runs = list(session.scalars(select(ModelRun).order_by(ModelRun.created_at)))

        assert persisted_account is not None
        assert (persisted_account.last_uidvalidity, persisted_account.polling_checkpoint) == (
            9001,
            104,
        )
        assert [(email.imap_uid, email.uidvalidity, email.mailbox_name) for email in emails] == [
            (101, 9001, "INBOX"),
            (102, 9001, "INBOX"),
            (103, 9001, "INBOX"),
            (104, 9001, "INBOX"),
        ]
        assert emails[0].flags_json == ["\\Recent", "\\Flagged"]
        assert emails[1].processing_status == ProcessingStatus.IGNORED.value
        assert emails[1].automated_classification == "out_of_office"
        assert emails[2].authorization_allowed is False
        assert emails[2].authorization_reason == "sender_not_whitelisted"
        assert emails[2].processing_status == ProcessingStatus.PROCESSED.value
        assert emails[3].flags_json == ["\\Recent"]

        assert [request.status for request in requests] == [
            RequestStatus.COMPLETED.value,
            RequestStatus.NEEDS_INFORMATION.value,
        ]
        assert [(operation.action, operation.status) for operation in operations] == [
            ("account_unblock", OperationStatus.COMPLETED.value),
            ("otp_number_change", OperationStatus.NEEDS_INFORMATION.value),
        ]
        assert len(session.scalars(select(Execution)).all()) == 1
        assert len(session.scalars(select(Clarification)).all()) == 1
        escalations = list(session.scalars(select(Escalation)))
        assert len(escalations) == 1
        assert escalations[0].email_message_id == emails[2].id
        assert escalations[0].reason_code == "sender_not_whitelisted"
        assert [(run.stage, run.structured_output_valid) for run in runs] == [
            ("analysis", True),
            ("verification", True),
            ("analysis", True),
            ("verification", True),
        ]

    second_poll = orchestrator.poll_once()

    assert [result.status for result in second_poll] == [ProcessingStatus.DUPLICATE.value] * 4
    assert len(analyzer_backend.calls) == 2
    assert len(verifier_backend.calls) == 2
    assert len(business_api.calls) == 1
