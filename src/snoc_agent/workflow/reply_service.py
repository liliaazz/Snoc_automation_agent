"""Outbound logical-message construction and thread headers."""

from __future__ import annotations

import hashlib
from email.utils import make_msgid

from sqlalchemy.orm import Session

from snoc_agent.db.models import BusinessRequest, EmailMessage, OutboxMessage
from snoc_agent.domain.enums import Direction, OutboxStatus, ProcessingStatus
from snoc_agent.domain.value_objects import reject_header_injection
from snoc_agent.mail.headers import (
    build_wire_references,
    normalize_message_id,
    normalize_subject,
    reply_subject,
    wire_message_id,
)


def create_outbound_message(
    session: Session,
    *,
    request: BusinessRequest,
    source_email: EmailMessage,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    extra_headers: dict[str, str],
    clarification_id: object | None = None,
) -> tuple[EmailMessage, OutboxMessage]:
    sender = reject_header_injection(sender)
    recipient = reject_header_injection(recipient)
    fallback_subject = reject_header_injection(subject)
    subject = reject_header_injection(
        reply_subject(source_email.subject, fallback=fallback_subject)
    )
    domain = sender.rsplit("@", 1)[-1] if "@" in sender else None
    rfc_message_id = make_msgid(domain=domain)
    references = build_wire_references(source_email.references_json, source_email.rfc_message_id)
    headers = {
        "Message-ID": rfc_message_id,
        "In-Reply-To": wire_message_id(source_email.rfc_message_id) or "",
        "References": " ".join(references),
        "X-SNOC-Agent-Generated": "true",
        "X-SNOC-Request-ID": request.public_reference,
        **{name: reject_header_injection(value) for name, value in extra_headers.items()},
    }
    outbound = EmailMessage(
        conversation_id=request.conversation_id,
        direction=Direction.OUTBOUND.value,
        rfc_message_id=rfc_message_id,
        normalized_message_id=normalize_message_id(rfc_message_id),
        in_reply_to=headers["In-Reply-To"] or None,
        references_json=references,
        sender=sender,
        recipients_json=[recipient],
        subject=subject,
        normalized_subject=normalize_subject(subject),
        raw_text=body,
        raw_sha256=hashlib.sha256(body.encode("utf-8")).hexdigest(),
        latest_user_message=body,
        quoted_text="",
        mime_type="text/plain",
        processing_status=ProcessingStatus.STORED.value,
        parsing_warnings=[],
    )
    session.add(outbound)
    session.flush()
    outbox = OutboxMessage(
        related_request_id=request.id,
        related_clarification_id=clarification_id,
        outbound_email_id=outbound.id,
        recipient=recipient,
        subject=subject,
        body=body,
        headers=headers,
        status=OutboxStatus.PENDING.value,
    )
    session.add(outbox)
    session.flush()
    return outbound, outbox
