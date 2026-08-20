from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import OutboxMessage
from snoc_agent.domain.enums import OutboxStatus


class OutboxRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, message: OutboxMessage) -> OutboxMessage:
        self.session.add(message)
        self.session.flush()
        return message

    def pending(self, limit: int = 100) -> list[OutboxMessage]:
        return list(
            self.session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.status == OutboxStatus.PENDING.value)
                .order_by(OutboxMessage.created_at)
                .limit(limit)
            )
        )
