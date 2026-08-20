from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import EmailMessage


class EmailRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, message: EmailMessage) -> EmailMessage:
        self.session.add(message)
        self.session.flush()
        return message

    def get(self, message_id: uuid.UUID) -> EmailMessage | None:
        return self.session.get(EmailMessage, message_id)

    def by_physical_locator(
        self, account_id: uuid.UUID, mailbox: str, uidvalidity: int, uid: int
    ) -> EmailMessage | None:
        return self.session.scalar(
            select(EmailMessage).where(
                EmailMessage.mail_account_id == account_id,
                EmailMessage.mailbox_name == mailbox,
                EmailMessage.uidvalidity == uidvalidity,
                EmailMessage.imap_uid == uid,
            )
        )

    def by_normalized_message_id(self, message_id: str) -> EmailMessage | None:
        return self.session.scalar(
            select(EmailMessage)
            .where(
                EmailMessage.normalized_message_id == message_id,
                EmailMessage.duplicate_of_id.is_(None),
            )
            .order_by(EmailMessage.created_at)
        )

    def by_raw_sha256(self, digest: str) -> EmailMessage | None:
        return self.session.scalar(
            select(EmailMessage)
            .where(EmailMessage.raw_sha256 == digest, EmailMessage.duplicate_of_id.is_(None))
            .order_by(EmailMessage.created_at)
        )

    def by_any_rfc_id(self, ids: list[str]) -> list[EmailMessage]:
        if not ids:
            return []
        return list(
            self.session.scalars(
                select(EmailMessage).where(EmailMessage.normalized_message_id.in_(ids))
            )
        )

    def failures(self) -> list[EmailMessage]:
        return list(
            self.session.scalars(
                select(EmailMessage).where(EmailMessage.processing_status == "failed")
            )
        )

    def quarantined(self) -> list[EmailMessage]:
        return list(
            self.session.scalars(
                select(EmailMessage)
                .where(EmailMessage.processing_status == "quarantined")
                .order_by(EmailMessage.quarantined_at, EmailMessage.created_at)
            )
        )
