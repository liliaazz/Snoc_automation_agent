"""Protocol and failures for telecom business-operation adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from snoc_agent.business_api.schemas import BusinessAPIResult


class BusinessAPIError(RuntimeError):
    """Base class for failures at the business API boundary."""


class BusinessAPITransportError(BusinessAPIError):
    """The adapter could not obtain an authoritative API response."""

    def __init__(self, message: str, *, endpoint: str, attempts: int) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.attempts = attempts


class BusinessAPIResponseError(BusinessAPIError):
    """The API returned an unsuccessful, malformed, or unsafe response."""

    def __init__(
        self,
        message: str,
        *,
        endpoint: str,
        attempts: int,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.attempts = attempts
        self.status_code = status_code


class IdempotencyConflictError(BusinessAPIError):
    """An idempotency key was reused for a different logical operation."""


@runtime_checkable
class BusinessAPI(Protocol):
    """Only the workflow calls this protocol; models never receive an adapter."""

    def create_vpn_access(
        self,
        *,
        pdv_code: str,
        phone: str,
        idempotency_key: str,
        additional_payload: Mapping[str, JsonValue] | None = None,
    ) -> BusinessAPIResult: ...

    def update_otp(
        self,
        *,
        pdv_code: str,
        new_phone: str,
        idempotency_key: str,
    ) -> BusinessAPIResult: ...

    def unlock_account(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult: ...

    def reset_password(self, *, pdv_code: str, idempotency_key: str) -> BusinessAPIResult: ...
