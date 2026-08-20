"""Create one structured clarification and its transactional outbox row."""

from __future__ import annotations

from sqlalchemy.orm import Session

from snoc_agent.db.models import BusinessRequest, Clarification, EmailMessage, Operation
from snoc_agent.db.repositories import ClarificationRepository
from snoc_agent.domain.enums import ClarificationStatus
from snoc_agent.mail.templates import OperationMailView, clarification_email
from snoc_agent.workflow.reply_service import create_outbound_message


def ensure_clarification(
    session: Session,
    *,
    request: BusinessRequest,
    source_email: EmailMessage,
    operations: list[Operation],
    sender_address: str,
    recipient: str,
) -> Clarification:
    open_clarifications = ClarificationRepository(session).open_for_request(request.id)
    if open_clarifications:
        return open_clarifications[0]
    views = [
        OperationMailView(
            sequence_number=operation.sequence_number,
            action=operation.action,
            pdv_code=operation.pdv_code,
            missing_fields=tuple(operation.missing_fields),
        )
        for operation in operations
    ]
    subject, body = clarification_email(request.public_reference, views)
    clarification = Clarification(
        request_id=request.id,
        source_inbound_email_id=source_email.id,
        target_operation_ids=[str(operation.id) for operation in operations],
        requested_fields={
            str(operation.id): list(operation.missing_fields) for operation in operations
        },
        question_text=body,
        status=ClarificationStatus.PENDING_SEND.value,
        round_number=len(request.clarifications) + 1,
    )
    session.add(clarification)
    session.flush()
    outbound, outbox = create_outbound_message(
        session,
        request=request,
        source_email=source_email,
        sender=sender_address,
        recipient=recipient,
        subject=subject,
        body=body,
        extra_headers={"X-SNOC-Reply-Type": "clarification"},
        clarification_id=clarification.id,
    )
    clarification.outbound_email_id = outbound.id
    outbox.related_clarification_id = clarification.id
    return clarification
