"""Retryable delivery of already-persisted logical outbox messages."""

from __future__ import annotations

from email.message import EmailMessage as MIMEEmailMessage

from snoc_agent.db.models import Clarification, EmailMessage, OutboxMessage
from snoc_agent.db.repositories import OutboxRepository
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.domain.enums import ClarificationStatus, OutboxStatus, ProcessingStatus
from snoc_agent.domain.value_objects import reject_header_injection
from snoc_agent.mail.interfaces import OutboundEnvelope, SMTPTransport


class OutboxService:
    def __init__(
        self,
        session_factory: SessionFactory,
        transport: SMTPTransport,
        *,
        sender: str,
        max_retries: int = 3,
    ) -> None:
        self.session_factory = session_factory
        self.transport = transport
        self.sender = reject_header_injection(sender)
        self.max_retries = max_retries

    def send_once(self, limit: int = 100) -> tuple[int, int]:
        with session_scope(self.session_factory) as session:
            ids = [message.id for message in OutboxRepository(session).pending(limit)]
        sent = 0
        failed = 0
        for outbox_id in ids:
            with session_scope(self.session_factory) as session:
                outbox = session.get(OutboxMessage, outbox_id)
                if outbox is None or outbox.status != OutboxStatus.PENDING.value:
                    continue
                outbound = session.get(EmailMessage, outbox.outbound_email_id)
                if outbound is None:
                    outbox.status = OutboxStatus.FAILED.value
                    outbox.last_error = "outbound email record is missing"
                    failed += 1
                    continue
                raw_message = outbound.raw_eml_blob
                if raw_message is None:
                    message = MIMEEmailMessage()
                    message["From"] = self.sender
                    message["To"] = reject_header_injection(outbox.recipient)
                    message["Subject"] = reject_header_injection(outbox.subject)
                    for name, value in outbox.headers.items():
                        if value and name not in message:
                            message[name] = reject_header_injection(str(value))
                    message.set_content(outbox.body)
                    raw_message = message.as_bytes()
                envelope = OutboundEnvelope(
                    sender=self.sender,
                    recipients=(outbox.recipient,),
                    raw_message=raw_message,
                    message_id=outbound.rfc_message_id or "",
                    metadata={"outbox_id": str(outbox.id)},
                )
                result = self.transport.send(envelope)
                if result.accepted:
                    outbox.status = OutboxStatus.SENT.value
                    from snoc_agent.db.base import utc_now

                    outbox.sent_at = utc_now()
                    outbound.processing_status = ProcessingStatus.PROCESSED.value
                    if outbox.related_clarification_id:
                        clarification = session.get(Clarification, outbox.related_clarification_id)
                        if clarification:
                            clarification.status = ClarificationStatus.OPEN.value
                    sent += 1
                else:
                    outbox.retry_count += 1
                    outbox.last_error = result.detail
                    if not result.transient_failure or outbox.retry_count >= self.max_retries:
                        outbox.status = OutboxStatus.FAILED.value
                    failed += 1
        return sent, failed
