from __future__ import annotations

from sqlalchemy.orm import Session

from snoc_agent.db.models import ModelRun


class ModelRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, run: ModelRun) -> ModelRun:
        self.session.add(run)
        self.session.flush()
        return run
