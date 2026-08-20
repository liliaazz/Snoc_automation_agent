from __future__ import annotations

from email import policy
from email.parser import BytesParser
from pathlib import Path
from uuid import UUID

from sqlalchemy import select

from snoc_agent.db.models import (
    BusinessRequest,
    Conversation,
    EmailMessage,
    Escalation,
    OutboxMessage,
)
from snoc_agent.db.session import SessionFactory, create_engine_and_session, create_schema
from snoc_agent.domain.enums import Direction, OutboxStatus, ProcessingStatus
from snoc_agent.workflow.escalation_service import create_escalation


def _database(tmp_path: Path) -> SessionFactory:
    engine, session_factory = create_engine_and_session(
        f"sqlite:///{tmp_path / 'escalations.sqlite3'}"
    )
    create_schema(engine)
    return session_factory


def _seed_inbound_request(session_factory: SessionFactory) -> tuple[UUID, UUID]:
    with session_factory() as session:
        conversation = Conversation(
            normalized_subject="incident otp",
            primary_sender="agent@example.test",
        )
        session.add(conversation)
        session.flush()

        inbound = EmailMessage(
            conversation_id=conversation.id,
            direction=Direction.INBOUND.value,
            rfc_message_id="<inbound-42@example.test>",
            normalized_message_id="<inbound-42@example.test>",
            references_json=["<root-1@example.test>"],
            sender="Agent Réseau <agent@example.test>",
            recipients_json=["snoc@example.test"],
            cc_json=[],
            subject="Incident OTP",
            normalized_subject="incident otp",
            raw_text="Le numéro OTP est ambigu.",
            latest_user_message="Le numéro OTP est ambigu.",
            quoted_text="",
            signature_text="",
            raw_sha256="a" * 64,
            raw_eml_blob=(
                b"From: Agent Reseau <agent@example.test>\r\n"
                b"To: snoc@example.test\r\n"
                b"Subject: Incident OTP\r\n"
                b"Message-ID: <inbound-42@example.test>\r\n"
                b"\r\n"
                b"Le numero OTP est ambigu.\r\n"
                b"\r\n"
                b"Le mar. 21 juil. 2026, le SNOC a ecrit :\r\n"
                b"> Merci de confirmer le numero OTP.\r\n"
            ),
            mime_type="text/plain",
            attachment_metadata=[],
            flags_json=[],
            processing_status=ProcessingStatus.STORED.value,
            parsing_warnings=[],
            correlation_details={},
        )
        session.add(inbound)
        session.flush()

        request = BusinessRequest(
            public_reference="SNOC-ESC-TEST-001",
            conversation_id=conversation.id,
            initiating_email_id=inbound.id,
        )
        session.add(request)
        session.commit()
        return inbound.id, request.id


def test_default_escalation_persists_audit_only(tmp_path: Path) -> None:
    session_factory = _database(tmp_path)
    inbound_id, request_id = _seed_inbound_request(session_factory)
    evidence = {"correlation_conflict": True, "candidate_request_count": 2}

    with session_factory() as session:
        escalation = create_escalation(
            session,
            email=session.get_one(EmailMessage, inbound_id),
            request=session.get_one(BusinessRequest, request_id),
            recipient="supervisor@example.test",
            reason_code="correlation_conflict",
            summary="Plusieurs demandes ouvertes correspondent à cette réponse.",
            evidence=evidence,
        )
        escalation_id = escalation.id
        session.commit()

    with session_factory() as session:
        escalation = session.get_one(Escalation, escalation_id)
        request = session.get_one(BusinessRequest, request_id)

        assert escalation.request_id == request_id
        assert escalation.email_message_id == inbound_id
        assert escalation.recipient == "supervisor@example.test"
        assert escalation.reason_code == "correlation_conflict"
        assert escalation.summary == ("Plusieurs demandes ouvertes correspondent à cette réponse.")
        assert {key: escalation.evidence[key] for key in evidence} == evidence
        assert escalation.evidence["email_context"]["internal_email_id"] == str(inbound_id)
        assert escalation.evidence["stored_request_state"]["request_id"] == str(request_id)
        assert escalation.status == "open"
        assert request.escalation_reason == escalation.summary
        assert (
            session.scalars(
                select(EmailMessage).where(EmailMessage.direction == Direction.OUTBOUND.value)
            ).all()
            == []
        )
        assert session.scalars(select(OutboxMessage)).all() == []


def test_queue_email_creates_structured_outbound_and_pending_outbox(
    tmp_path: Path,
) -> None:
    session_factory = _database(tmp_path)
    inbound_id, request_id = _seed_inbound_request(session_factory)
    evidence = {
        "authorized_sender": True,
        "decision": "ESCALATE",
        "reasons": ["semantic_conflict", "missing_evidence"],
    }

    with session_factory() as session:
        escalation = create_escalation(
            session,
            email=session.get_one(EmailMessage, inbound_id),
            request=session.get_one(BusinessRequest, request_id),
            recipient="supervisor@example.test",
            reason_code="semantic_conflict",
            summary="Le modèle et le vérificateur sont en désaccord.",
            evidence=evidence,
            queue_email=True,
            sender_address="snoc@example.test",
        )
        escalation_id = escalation.id
        session.commit()

    with session_factory() as session:
        escalation = session.get_one(Escalation, escalation_id)
        inbound = session.get_one(EmailMessage, inbound_id)
        outbound = session.scalars(
            select(EmailMessage).where(EmailMessage.direction == Direction.OUTBOUND.value)
        ).one()
        outbox = session.scalars(select(OutboxMessage)).one()

        assert outbound.conversation_id == inbound.conversation_id
        assert outbound.sender == "snoc@example.test"
        assert outbound.recipients_json == ["supervisor@example.test"]
        assert outbound.subject == "Fwd: Incident OTP"
        assert outbound.in_reply_to == "<inbound-42@example.test>"
        assert outbound.references_json == [
            "<root-1@example.test>",
            "<inbound-42@example.test>",
        ]
        assert outbound.latest_user_message == outbound.raw_text
        assert "Motif : semantic_conflict" in outbound.raw_text
        assert "---------- Message transféré ----------" in outbound.raw_text
        assert "De : Agent Reseau <agent@example.test>" in outbound.raw_text
        assert "Le numero OTP est ambigu." in outbound.raw_text
        assert "> Merci de confirmer le numero OTP." in outbound.raw_text
        assert '"authorized_sender": true' not in outbound.raw_text
        assert outbound.raw_eml_blob is not None
        forwarded = BytesParser(policy=policy.default).parsebytes(outbound.raw_eml_blob)
        assert forwarded["Subject"] == "Fwd: Incident OTP"
        assert list(forwarded.iter_attachments()) == []
        assert "---------- Message transféré ----------" in forwarded.get_content()
        assert "> Merci de confirmer le numero OTP." in forwarded.get_content()
        assert outbound.attachment_metadata == []
        assert outbound.processing_status == ProcessingStatus.STORED.value

        assert outbox.related_request_id == request_id
        assert outbox.outbound_email_id == outbound.id
        assert outbox.recipient == "supervisor@example.test"
        assert outbox.subject == "Fwd: Incident OTP"
        assert outbox.body == outbound.raw_text
        assert outbox.status == OutboxStatus.PENDING.value
        assert outbox.headers["Message-ID"] == outbound.rfc_message_id
        assert outbox.headers["In-Reply-To"] == "<inbound-42@example.test>"
        assert outbox.headers["References"] == ("<root-1@example.test> <inbound-42@example.test>")
        assert outbox.headers["X-SNOC-Escalation-ID"] == str(escalation_id)
        assert outbox.headers["X-SNOC-Request-ID"] == "SNOC-ESC-TEST-001"
