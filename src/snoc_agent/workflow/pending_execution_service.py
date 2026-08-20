"""Transactional acknowledgement for operations waiting in the durable grace queue."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import BusinessRequest, EmailMessage, Operation, OutboxMessage
from snoc_agent.mail.templates import OperationMailView, pending_execution_email
from snoc_agent.workflow.reply_service import create_outbound_message

PENDING_EXECUTION_HEADER = "X-SNOC-Pending-Execution"


def ensure_pending_execution_acknowledgement(
    session: Session,
    *,
    request: BusinessRequest,
    source_email: EmailMessage,
    operations: list[Operation],
    sender_address: str,
    recipient: str,
    grace_seconds: int,
) -> bool:
    """Create at most one correction-window acknowledgement per request."""

    existing = next(
        (
            message
            for message in session.scalars(
                select(OutboxMessage).where(OutboxMessage.related_request_id == request.id)
            )
            if message.headers.get(PENDING_EXECUTION_HEADER) == "true"
        ),
        None,
    )
    if existing is not None:
        return False
    views = [
        OperationMailView(
            sequence_number=operation.sequence_number,
            action=operation.action,
            pdv_code=operation.pdv_code,
        )
        for operation in operations
    ]
    subject, body = pending_execution_email(
        request.public_reference,
        views,
        grace_seconds=grace_seconds,
    )
    create_outbound_message(
        session,
        request=request,
        source_email=source_email,
        sender=sender_address,
        recipient=recipient,
        subject=subject,
        body=body,
        extra_headers={
            PENDING_EXECUTION_HEADER: "true",
            "X-SNOC-Reply-Type": "acknowledgement",
        },
    )
    return True
