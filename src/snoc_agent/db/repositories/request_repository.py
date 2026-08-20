from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.db.models import BusinessRequest
from snoc_agent.domain.enums import RequestStatus

OPEN_REQUEST_STATUSES = {
    RequestStatus.NEW.value,
    RequestStatus.ANALYZING.value,
    RequestStatus.ACTIVE.value,
    RequestStatus.PARTIALLY_COMPLETED.value,
    RequestStatus.NEEDS_INFORMATION.value,
    RequestStatus.READY_FOR_VALIDATION.value,
}


class RequestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, request: BusinessRequest) -> BusinessRequest:
        self.session.add(request)
        self.session.flush()
        return request

    def get(self, request_id: uuid.UUID) -> BusinessRequest | None:
        return self.session.get(BusinessRequest, request_id)

    def by_public_reference(self, reference: str) -> BusinessRequest | None:
        return self.session.scalar(
            select(BusinessRequest).where(BusinessRequest.public_reference == reference)
        )

    def open_for_conversation(self, conversation_id: uuid.UUID) -> list[BusinessRequest]:
        return list(
            self.session.scalars(
                select(BusinessRequest)
                .where(
                    BusinessRequest.conversation_id == conversation_id,
                    BusinessRequest.status.in_(OPEN_REQUEST_STATUSES),
                )
                .order_by(BusinessRequest.last_active_at.desc())
            )
        )
