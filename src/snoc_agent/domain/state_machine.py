"""Explicit request and operation transition rules."""

from __future__ import annotations

from collections.abc import Iterable

from snoc_agent.domain.enums import OperationStatus, RequestStatus
from snoc_agent.domain.errors import InvalidStateTransition

OPERATION_TRANSITIONS: dict[OperationStatus, set[OperationStatus]] = {
    OperationStatus.NEW: {
        OperationStatus.NEEDS_INFORMATION,
        OperationStatus.READY_FOR_VALIDATION,
        OperationStatus.ESCALATED,
        OperationStatus.CANCELLED,
    },
    OperationStatus.NEEDS_INFORMATION: {
        OperationStatus.READY_FOR_VALIDATION,
        OperationStatus.ESCALATED,
        OperationStatus.CANCELLED,
    },
    OperationStatus.READY_FOR_VALIDATION: {
        OperationStatus.EXECUTING,
        OperationStatus.NEEDS_INFORMATION,
        OperationStatus.ESCALATED,
        OperationStatus.CANCELLED,
    },
    OperationStatus.EXECUTING: {
        OperationStatus.COMPLETED,
        OperationStatus.FAILED,
        OperationStatus.ESCALATED,
    },
    OperationStatus.ESCALATED: {OperationStatus.CANCELLED},
    OperationStatus.COMPLETED: set(),
    OperationStatus.FAILED: set(),
    OperationStatus.CANCELLED: set(),
}


REQUEST_TRANSITIONS: dict[RequestStatus, set[RequestStatus]] = {
    RequestStatus.NEW: {RequestStatus.ANALYZING, RequestStatus.CANCELLED},
    RequestStatus.ANALYZING: {
        RequestStatus.ACTIVE,
        RequestStatus.READY_FOR_VALIDATION,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.PARTIALLY_COMPLETED,
        RequestStatus.ESCALATED,
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
    },
    RequestStatus.ACTIVE: {
        RequestStatus.READY_FOR_VALIDATION,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.PARTIALLY_COMPLETED,
        RequestStatus.ESCALATED,
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
        RequestStatus.CANCELLED,
        RequestStatus.EXPIRED,
    },
    RequestStatus.READY_FOR_VALIDATION: {
        RequestStatus.ACTIVE,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.PARTIALLY_COMPLETED,
        RequestStatus.ESCALATED,
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
    },
    RequestStatus.NEEDS_INFORMATION: {
        RequestStatus.READY_FOR_VALIDATION,
        RequestStatus.PARTIALLY_COMPLETED,
        RequestStatus.ESCALATED,
        RequestStatus.COMPLETED,
        RequestStatus.CANCELLED,
        RequestStatus.EXPIRED,
    },
    RequestStatus.PARTIALLY_COMPLETED: {
        RequestStatus.READY_FOR_VALIDATION,
        RequestStatus.NEEDS_INFORMATION,
        RequestStatus.ESCALATED,
        RequestStatus.COMPLETED,
        RequestStatus.FAILED,
    },
    RequestStatus.ESCALATED: {RequestStatus.CANCELLED, RequestStatus.COMPLETED},
    RequestStatus.COMPLETED: set(),
    RequestStatus.FAILED: set(),
    RequestStatus.CANCELLED: set(),
    RequestStatus.EXPIRED: set(),
}


def assert_operation_transition(current: OperationStatus, target: OperationStatus) -> None:
    if target != current and target not in OPERATION_TRANSITIONS[current]:
        raise InvalidStateTransition(f"operation {current} -> {target} is forbidden")


def assert_request_transition(current: RequestStatus, target: RequestStatus) -> None:
    if target != current and target not in REQUEST_TRANSITIONS[current]:
        raise InvalidStateTransition(f"request {current} -> {target} is forbidden")


def derive_request_status(statuses: Iterable[OperationStatus]) -> RequestStatus:
    values = list(statuses)
    if not values:
        return RequestStatus.ACTIVE
    if all(status == OperationStatus.COMPLETED for status in values):
        return RequestStatus.COMPLETED
    if any(status == OperationStatus.NEEDS_INFORMATION for status in values):
        if any(status == OperationStatus.COMPLETED for status in values):
            return RequestStatus.PARTIALLY_COMPLETED
        return RequestStatus.NEEDS_INFORMATION
    if any(status == OperationStatus.EXECUTING for status in values):
        return RequestStatus.ACTIVE
    if any(status == OperationStatus.READY_FOR_VALIDATION for status in values):
        return RequestStatus.READY_FOR_VALIDATION
    if any(status == OperationStatus.ESCALATED for status in values):
        if any(status == OperationStatus.COMPLETED for status in values):
            return RequestStatus.PARTIALLY_COMPLETED
        return RequestStatus.ESCALATED
    if any(status == OperationStatus.FAILED for status in values):
        return RequestStatus.FAILED
    return RequestStatus.ACTIVE
