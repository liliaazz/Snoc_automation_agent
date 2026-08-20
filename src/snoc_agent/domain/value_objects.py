"""Canonicalization and deterministic hard invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass

from snoc_agent.domain.enums import LEGACY_ACTION_MAPPING, OperationAction


def canonical_action(value: str | OperationAction) -> OperationAction:
    if isinstance(value, OperationAction):
        return value
    normalized = value.strip().casefold()
    if normalized in LEGACY_ACTION_MAPPING:
        return LEGACY_ACTION_MAPPING[normalized]
    try:
        return OperationAction(normalized)
    except ValueError:
        return OperationAction.UNKNOWN


def required_fields(action: OperationAction) -> tuple[str, ...]:
    if action == OperationAction.VPN_ACCESS:
        return ("pdv_code", "phone")
    if action == OperationAction.OTP_NUMBER_CHANGE:
        return ("pdv_code", "new_phone")
    if action in {OperationAction.ACCOUNT_UNBLOCK, OperationAction.PASSWORD_RESET}:
        return ("pdv_code",)
    return ()


def normalize_numeric(value: str | None, *, keep_leading_plus: bool = False) -> str | None:
    if value is None:
        return None
    text = value.strip()
    plus = keep_leading_plus and text.startswith("+")
    digits = "".join(character for character in text if character.isdigit())
    if not digits:
        return None
    return f"+{digits}" if plus else digits


@dataclass(frozen=True, slots=True)
class InvariantResult:
    passed: bool
    reasons: tuple[str, ...]


def validate_operation_fields(
    *,
    action: OperationAction,
    pdv_code: str | None,
    phone: str | None,
    pdv_pattern: str = r"^\d{8}$",
    phone_pattern: str = r"^\+?\d{9,15}$",
) -> InvariantResult:
    reasons: list[str] = []
    fields = {"pdv_code": pdv_code, "phone": phone, "new_phone": phone}
    for field in required_fields(action):
        if not fields[field]:
            reasons.append(f"missing_required_field:{field}")
    if pdv_code is not None and re.fullmatch(pdv_pattern, pdv_code) is None:
        reasons.append("invalid_pdv_format")
    if phone is not None and re.fullmatch(phone_pattern, phone) is None:
        reasons.append("invalid_phone_format")
    if action == OperationAction.UNKNOWN:
        reasons.append("unsupported_action")
    return InvariantResult(not reasons, tuple(reasons))


def reject_header_injection(value: str) -> str:
    if "\r" in value or "\n" in value:
        raise ValueError("email header values cannot contain CR or LF")
    return value
