"""Request aggregate helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session

from snoc_agent.db.base import utc_now
from snoc_agent.db.models import BusinessRequest
from snoc_agent.domain.enums import OperationStatus, RequestStatus
from snoc_agent.domain.state_machine import assert_request_transition, derive_request_status


def refresh_request_status(session: Session, request: BusinessRequest) -> RequestStatus:
    session.refresh(request, attribute_names=["operations"])
    status = derive_request_status(
        OperationStatus(operation.status) for operation in request.operations
    )
    current = RequestStatus(request.status)
    assert_request_transition(current, status)
    request.last_active_at = utc_now()
    if status != current:
        request.status = status.value
        request.version += 1
    return status
