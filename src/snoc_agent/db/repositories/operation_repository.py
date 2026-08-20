from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import Operation


class OperationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, operation: Operation) -> Operation:
        self.session.add(operation)
        self.session.flush()
        return operation

    def get(self, operation_id: uuid.UUID) -> Operation | None:
        return self.session.get(Operation, operation_id)

    def for_request(self, request_id: uuid.UUID) -> list[Operation]:
        return list(
            self.session.scalars(
                select(Operation)
                .where(Operation.request_id == request_id)
                .order_by(Operation.sequence_number)
            )
        )
