from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import Clarification, EmailMessage
from snoc_agent.domain.enums import ClarificationStatus


class ClarificationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, clarification: Clarification) -> Clarification:
        self.session.add(clarification)
        self.session.flush()
        return clarification

    def get(self, clarification_id: uuid.UUID) -> Clarification | None:
        return self.session.get(Clarification, clarification_id)

    def by_outbound_rfc_id(self, normalized_message_id: str) -> Clarification | None:
        return self.session.scalar(
            select(Clarification)
            .join(EmailMessage, Clarification.outbound_email_id == EmailMessage.id)
            .where(
                EmailMessage.normalized_message_id == normalized_message_id,
                Clarification.status.in_(
                    [ClarificationStatus.PENDING_SEND.value, ClarificationStatus.OPEN.value]
                ),
            )
        )

    def open_for_request(self, request_id: uuid.UUID) -> list[Clarification]:
        return list(
            self.session.scalars(
                select(Clarification).where(
                    Clarification.request_id == request_id,
                    Clarification.status.in_(
                        [ClarificationStatus.PENDING_SEND.value, ClarificationStatus.OPEN.value]
                    ),
                )
            )
        )
