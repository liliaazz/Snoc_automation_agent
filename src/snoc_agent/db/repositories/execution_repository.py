from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import Execution


class ExecutionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, execution: Execution) -> Execution:
        self.session.add(execution)
        self.session.flush()
        return execution

    def by_idempotency_key(self, key: str) -> Execution | None:
        return self.session.scalar(select(Execution).where(Execution.idempotency_key == key))
