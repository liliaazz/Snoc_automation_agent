"""Seed the SNOC database with demo data for development/testing.

Usage::

    python -m snoc_agent.seed
    # or
    snoc-seed
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from snoc_agent.config import load_settings
from snoc_agent.db.models import (
    BusinessRequest,
    Conversation,
    EmailMessage,
    Escalation,
    Operation,
    OutboxMessage,
    WorkflowEvent,
    WorkflowRun,
)
from snoc_agent.db.session import create_engine_and_session, create_schema
from snoc_agent.domain.enums import (
    ConversationStatus,
    OperationStatus,
    OutboxStatus,
    ProcessingStatus,
    RequestKind,
    RequestStatus,
)

logger = logging.getLogger("snoc_agent.seed")

NOW = datetime.now(UTC)


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _ago(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def seed_demo_data(
    session: Session,
    *,
    agent_address: str = "snoc-agent@example.test",
    escalation_recipient: str = "human-support@example.test",
) -> None:
    """Insert realistic demo data: conversations, emails, requests, escalations, workflow runs."""
    agent_address = agent_address.strip() or "snoc-agent@example.test"
    escalation_recipient = escalation_recipient.strip() or "human-support@example.test"
    demo_senders = [
        "techsupport@example.test",
        "amina.bouzid@example.com",
        "sofiane.hamidi@example.com",
        "amina.bouzid@example.com",
        "malik.cherif@example.com",
    ]

    # ── Conversations (required FK for BusinessRequest) ───────────────────
    convos = []
    for sender, subject in zip(
        demo_senders,
        [
            "Activation VPN - PDV 12345678",
            "Reset mot de passe - PDV 87654321",
            "Deblocage compte - PDV 11223344",
            "Changement forfait - PDV 12345678",
            "Suspension ligne - PDV 87654321",
        ],
        strict=True,
    ):
        c = Conversation(
            id=_uuid(),
            normalized_subject=subject,
            primary_sender=sender,
            status=ConversationStatus.OPEN.value,
            created_at=_ago(2),
            last_message_at=_ago(1),
        )
        session.add(c)
        convos.append(c)
    session.flush()

    # ── Email messages ───────────────────────────────────────────────────
    emails = []
    email_specs = [
        (
            demo_senders[0],
            "Activation VPN",
            "Bonjour, je souhaite activer mon VPN. Mon PDV est 12345678.",
            ProcessingStatus.PROCESSED.value,
        ),
        (
            "amina.bouzid@example.com",
            "Reset mot de passe",
            "J'ai oublie mon mot de passe. PDV: 87654321.",
            ProcessingStatus.PROCESSED.value,
        ),
        (
            "sofiane.hamidi@example.com",
            "Deblocage compte",
            "Mon compte est bloque. PDV 11223344 merci.",
            ProcessingStatus.STORED.value,
        ),
        (
            "amina.bouzid@example.com",
            "Changement forfait",
            "Je veux passer au forfait 2000 DA. PDV 12345678.",
            ProcessingStatus.STORED.value,
        ),
        (
            "malik.cherif@example.com",
            "Suspension ligne",
            "Je veux suspendre ma ligne temporairement. PDV 87654321.",
            ProcessingStatus.STORED.value,
        ),
    ]
    for i, (sender, subject, body, status) in enumerate(email_specs):
        body_hash = _sha256(body)
        em = EmailMessage(
            id=_uuid(),
            rfc_message_id=f"<{uuid.uuid4()}@snoc.demo>",
            normalized_message_id=body_hash,
            direction="inbound",
            sender=sender,
            recipients_json=[agent_address],
            subject=subject,
            normalized_subject=subject.lower(),
            raw_text=body,
            latest_user_message=body,
            raw_sha256=body_hash,
            processing_status=status,
            conversation_id=convos[i].id,
        )
        session.add(em)
        emails.append(em)
    session.flush()

    # ── Business requests ────────────────────────────────────────────────
    requests = []
    req_specs = [
        (convos[0], emails[0], RequestStatus.COMPLETED.value),
        (convos[1], emails[1], RequestStatus.NEW.value),
        (convos[2], emails[2], RequestStatus.NEW.value),
        (convos[3], emails[3], RequestStatus.NEW.value),
        (convos[4], emails[4], RequestStatus.NEW.value),
    ]
    for convo, email, status in req_specs:
        br = BusinessRequest(
            id=_uuid(),
            public_reference=f"REQ-{str(_uuid())[:8].upper()}",
            conversation_id=convo.id,
            initiating_email_id=email.id,
            status=status,
            request_kind=RequestKind.NEW.value,
        )
        session.add(br)
        requests.append(br)
    session.flush()

    # ── Operations ───────────────────────────────────────────────────────
    seq_counter: dict[str, int] = {}
    ops_specs = [
        (requests[0], "vpn_access", OperationStatus.COMPLETED.value, 0.92, "12345678"),
        (requests[0], "send_reply", OperationStatus.COMPLETED.value, None, None),
        (requests[1], "password_reset", OperationStatus.NEW.value, 0.0, "87654321"),
        (requests[2], "account_unblock", OperationStatus.ESCALATED.value, 0.0, "11223344"),
    ]
    for req, action, status, conf, pdv in ops_specs:
        seq = seq_counter.setdefault(str(req.id), 0) + 1
        seq_counter[str(req.id)] = seq
        analyzer = {}
        if conf is not None:
            analyzer = {"raw_model_confidence": conf}
        op = Operation(
            id=_uuid(),
            request_id=req.id,
            sequence_number=seq,
            action=action,
            status=status,
            pdv_code=pdv,
            analyzer_confidence=analyzer,
        )
        session.add(op)

    # ── Escalation ───────────────────────────────────────────────────────
    esc = Escalation(
        id=_uuid(),
        request_id=requests[2].id,
        email_message_id=emails[2].id,
        recipient=escalation_recipient,
        reason_code="LOW_CONFIDENCE",
        summary="Cannot determine correct unblock procedure - PDV format mismatch",
        status="open",
        created_at=_ago(1),
    )
    session.add(esc)

    # ── Workflow run + events (for pipeline view) ────────────────────────
    wf_run = WorkflowRun(
        id=_uuid(),
        inbound_email_id=emails[0].id,
        graph_version="1.0.0",
        engine="langgraph",
        status="completed",
        current_agent="fulfilment",
        started_at=_ago(2),
        completed_at=_ago(1.5),
    )
    session.add(wf_run)

    cumulative = 0
    for seq, (agent, dur_s) in enumerate(
        [
            ("ingress", 120),
            ("security", 30),
            ("nlu", 450),
            ("policy", 80),
            ("fulfilment", 900),
        ],
        start=1,
    ):
        started = wf_run.started_at + timedelta(seconds=cumulative)
        cumulative += dur_s
        completed = wf_run.started_at + timedelta(seconds=cumulative)
        evt = WorkflowEvent(
            id=_uuid(),
            workflow_run_id=wf_run.id,
            sequence=seq,
            agent=agent,
            status="completed",
            started_at=started,
            completed_at=completed,
        )
        session.add(evt)

    # ── Outbox (sent reply) ──────────────────────────────────────────────
    out = OutboxMessage(
        id=_uuid(),
        related_request_id=requests[0].id,
        outbound_email_id=emails[0].id,
        recipient=emails[0].sender,
        subject="Re: Activation VPN",
        body="Votre VPN a ete active.",
        status=OutboxStatus.SENT.value,
    )
    session.add(out)

    session.commit()
    logger.info(
        "Seeded demo data: 5 conversations, 5 emails, 5 requests, 1 escalation, 1 workflow run"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed SNOC database with demo data")
    parser.add_argument(
        "--drop", action="store_true", help="Drop and recreate all tables before seeding"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s")

    settings = load_settings()
    engine, session_factory = create_engine_and_session(settings.database_url)

    if args.drop:
        from snoc_agent.db.models import Base

        Base.metadata.drop_all(engine)
        logger.info("Dropped all tables")

    create_schema(engine)
    logger.info("Tables ensured")

    session = session_factory()
    try:
        from snoc_agent.db.models import EmailMessage

        existing = session.query(EmailMessage).count()
        if existing > 0:
            logger.info("Database already contains %d emails - skipping seed", existing)
            return
        seed_demo_data(
            session,
            agent_address=settings.imap_username or settings.smtp_from_address,
            escalation_recipient=settings.escalation_recipient,
        )
    finally:
        session.close()

    logger.info("Done. Database ready at %s", settings.database_url)


if __name__ == "__main__":
    main()
