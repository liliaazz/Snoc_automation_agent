"""Polling orchestrator that processes each fetched message independently."""

from __future__ import annotations

import logging
import uuid

from snoc_agent.domain.enums import ProcessingStatus
from snoc_agent.mail.interfaces import IMAPMailbox
from snoc_agent.workflow.inbound_processor import (
    InboundIdentity,
    InboundProcessor,
    ProcessingResult,
)

LOGGER = logging.getLogger(__name__)
# These statuses mean that the message and its outcome are already stored
# durably in the database.
#
# FAILED and QUARANTINED messages are retried from their retained raw MIME
# using the application's retry command, rather than by repeatedly polling
# the same Gmail UID.
_ACKNOWLEDGED_STATUSES = frozenset(
    {
        ProcessingStatus.PROCESSED.value,
        ProcessingStatus.DUPLICATE.value,
        ProcessingStatus.IGNORED.value,
        ProcessingStatus.QUARANTINED.value,
        ProcessingStatus.FAILED.value,
    }
)


class MailOrchestrator:
    def __init__(
        self,
        *,
        mailbox: IMAPMailbox,
        processor: InboundProcessor,
        mail_account_id: uuid.UUID,
    ) -> None:
        self.mailbox = mailbox
        self.processor = processor
        self.mail_account_id = mail_account_id

    def poll_once(self) -> list[ProcessingResult]:
        results: list[ProcessingResult] = []

        for message in self.mailbox.fetch_candidates():
            try:
                result = self.processor.process_raw(
                    message.raw_message,
                    identity=InboundIdentity(
                        account_id=self.mail_account_id,
                        mailbox=message.mailbox,
                        uidvalidity=message.uidvalidity,
                        uid=message.uid,
                        internal_date=message.internal_date,
                        flags=message.flags,
                        provider_metadata=message.provider_metadata,
                    ),
                )
            except Exception:
                LOGGER.exception(
                    "inbound IMAP message processing failed",
                    extra={
                        "imap_uid": message.uid,
                        "uidvalidity": message.uidvalidity,
                        "mailbox": message.mailbox,
                    },
                )
                continue

            results.append(result)
            if result.status not in _ACKNOWLEDGED_STATUSES:
                continue

            try:
                self.mailbox.mark_seen(
                    message.uid,
                    uidvalidity=message.uidvalidity,
                )
            except Exception:
                # The processing result is already durable. The UID remains
                # unread and will be detected as a duplicate on the next poll
                # until acknowledgement eventually succeeds.
                LOGGER.exception(
                    "durably handled IMAP message could not be marked as seen",
                    extra={
                        "imap_uid": message.uid,
                        "uidvalidity": message.uidvalidity,
                        "mailbox": message.mailbox,
                        "processing_status": result.status,
                        "email_message_id": str(result.email_message_id),
                    },
                )

        return results
