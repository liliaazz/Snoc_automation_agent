"""Validated commands and results for the business API boundary.

The remote telecom API is intentionally kept behind these schemas.  Model output
must be converted to one of the commands below before an adapter can send it.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from snoc_agent.domain.enums import OperationAction


def _reject_header_controls(value: str, *, field_name: str) -> str:
    """Reject values that could escape an HTTP header or path component."""

    value = value.strip()
    if not value:
        raise ValueError(f"{field_name} must not be empty")
    if any(character in value for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field_name} contains a forbidden control character")
    return value


class _BusinessCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    pdv_code: str = Field(pattern=r"^\d{8}$")
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("idempotency_key")
    @classmethod
    def valid_idempotency_key(cls, value: str) -> str:
        value = _reject_header_controls(value, field_name="idempotency_key")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", value):
            raise ValueError("idempotency_key must contain only safe visible ASCII characters")
        return value


class VPNAccessCommand(_BusinessCommand):
    """Fields currently available for a VPN-access request.

    Extra endpoint-specific values may be supplied by a trusted workflow or a
    later clarification.  They cannot replace the two core fields.
    """

    phone: str = Field(min_length=1, max_length=64)
    additional_payload: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("phone")
    @classmethod
    def valid_phone(cls, value: str) -> str:
        # Phone formatting is deployment-configurable, so this boundary only
        # rejects empty/control-character values.  The decision engine applies
        # the configured PHONE_PATTERN before this adapter is called.
        return _reject_header_controls(value, field_name="phone")

    @field_validator("additional_payload")
    @classmethod
    def core_fields_cannot_be_overridden(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        reserved = {"pdv_code", "phone", "idempotency_key"}
        conflicts = reserved.intersection(value)
        if conflicts:
            joined = ", ".join(sorted(conflicts))
            raise ValueError(f"additional_payload cannot override: {joined}")
        return value


class OTPNumberChangeCommand(_BusinessCommand):
    new_phone: str = Field(min_length=1, max_length=64)

    @field_validator("new_phone")
    @classmethod
    def valid_phone(cls, value: str) -> str:
        return _reject_header_controls(value, field_name="new_phone")


class PDVOnlyCommand(_BusinessCommand):
    """Validated command shared by unlock and password-reset endpoints."""


class BusinessAPIEndpointPaths(BaseModel):
    """Configurable relative paths for the four supported operations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    vpn_access: str = "/create-account"
    otp_number_change: str = "/update-otp/{pdv_code}/{new_phone}"
    account_unblock: str = "/unlock-account/{pdv_code}"
    password_reset: str = "/reset-password/{pdv_code}"

    @field_validator("vpn_access", "otp_number_change", "account_unblock", "password_reset")
    @classmethod
    def relative_safe_path(cls, value: str) -> str:
        value = _reject_header_controls(value, field_name="endpoint path")
        if "://" in value or not value.startswith("/"):
            raise ValueError("endpoint paths must be absolute-path references beginning with '/'")
        return value


class BusinessAPIResponsePayload(BaseModel):
    """Minimum trusted response contract from a production endpoint.

    An HTTP 2xx alone is insufficient evidence that the operation completed.
    The API must explicitly return a boolean ``success`` field.  Additional
    fields are preserved for execution auditing.
    """

    model_config = ConfigDict(extra="allow", strict=True)

    success: bool
    message: str | None = None
    reference: str | None = None
    data: dict[str, Any] | None = None


class BusinessAPIResult(BaseModel):
    """Normalized successful result recorded by the execution service."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: OperationAction
    success: bool = True
    dry_run: bool
    idempotency_key: str
    endpoint: str
    status_code: int | None = None
    response_body: dict[str, Any] = Field(default_factory=dict)
    attempts: int = Field(default=1, ge=1)


class RecordedBusinessAPICall(BaseModel):
    """One unique invocation accepted by the in-memory mock adapter."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: OperationAction
    idempotency_key: str
    endpoint: str
    payload: dict[str, JsonValue]
