from __future__ import annotations

import pytest

from snoc_agent.domain.enums import OperationAction, OperationStatus, RequestStatus
from snoc_agent.domain.errors import InvalidStateTransition
from snoc_agent.domain.state_machine import (
    assert_operation_transition,
    assert_request_transition,
    derive_request_status,
)
from snoc_agent.domain.value_objects import (
    canonical_action,
    normalize_numeric,
    reject_header_injection,
    required_fields,
    validate_operation_fields,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vpn", OperationAction.VPN_ACCESS),
        (" OTP ", OperationAction.OTP_NUMBER_CHANGE),
        ("locked", OperationAction.ACCOUNT_UNBLOCK),
        ("reset", OperationAction.PASSWORD_RESET),
        ("password_reset", OperationAction.PASSWORD_RESET),
        ("not-supported", OperationAction.UNKNOWN),
        (OperationAction.ACCOUNT_UNBLOCK, OperationAction.ACCOUNT_UNBLOCK),
    ],
)
def test_canonical_action_maps_legacy_and_unknown_values(
    raw: str | OperationAction, expected: OperationAction
) -> None:
    assert canonical_action(raw) is expected


def test_required_fields_are_action_specific() -> None:
    assert required_fields(OperationAction.VPN_ACCESS) == ("pdv_code", "phone")
    assert required_fields(OperationAction.OTP_NUMBER_CHANGE) == ("pdv_code", "new_phone")
    assert required_fields(OperationAction.ACCOUNT_UNBLOCK) == ("pdv_code",)
    assert required_fields(OperationAction.PASSWORD_RESET) == ("pdv_code",)
    assert required_fields(OperationAction.UNKNOWN) == ()


def test_numeric_normalization_is_explicit_about_leading_plus() -> None:
    assert normalize_numeric(" +213 (777) 888-999 ") == "213777888999"
    assert normalize_numeric(" +213 (777) 888-999 ", keep_leading_plus=True) == ("+213777888999")
    assert normalize_numeric("no digits") is None
    assert normalize_numeric(None) is None


def test_operation_field_invariants_report_missing_format_and_unknown_action() -> None:
    complete = validate_operation_fields(
        action=OperationAction.VPN_ACCESS,
        pdv_code="12345678",
        phone="+213777888999",
    )
    missing = validate_operation_fields(
        action=OperationAction.OTP_NUMBER_CHANGE,
        pdv_code="12345678",
        phone=None,
    )
    malformed = validate_operation_fields(
        action=OperationAction.VPN_ACCESS,
        pdv_code="1234",
        phone="abc",
    )
    unsupported = validate_operation_fields(
        action=OperationAction.UNKNOWN,
        pdv_code=None,
        phone=None,
    )

    assert complete.passed is True and complete.reasons == ()
    assert missing.reasons == ("missing_required_field:new_phone",)
    assert malformed.reasons == ("invalid_pdv_format", "invalid_phone_format")
    assert unsupported.reasons == ("unsupported_action",)


def test_header_injection_is_rejected_without_modifying_safe_values() -> None:
    assert reject_header_injection("[SNOC-REQ-A84F91C274D2] Résultat") == (
        "[SNOC-REQ-A84F91C274D2] Résultat"
    )
    with pytest.raises(ValueError, match="CR or LF"):
        reject_header_injection("safe\r\nBcc: attacker@example.com")


def test_state_transition_guards_allow_idempotence_and_declared_edges() -> None:
    assert_operation_transition(OperationStatus.NEW, OperationStatus.NEW)
    assert_operation_transition(OperationStatus.NEW, OperationStatus.NEEDS_INFORMATION)
    assert_operation_transition(OperationStatus.READY_FOR_VALIDATION, OperationStatus.EXECUTING)
    assert_request_transition(RequestStatus.NEW, RequestStatus.ANALYZING)
    assert_request_transition(RequestStatus.NEEDS_INFORMATION, RequestStatus.READY_FOR_VALIDATION)


def test_terminal_state_transition_guards_fail_closed() -> None:
    with pytest.raises(InvalidStateTransition, match="operation COMPLETED -> EXECUTING"):
        assert_operation_transition(OperationStatus.COMPLETED, OperationStatus.EXECUTING)
    with pytest.raises(InvalidStateTransition, match="request COMPLETED -> ACTIVE"):
        assert_request_transition(RequestStatus.COMPLETED, RequestStatus.ACTIVE)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        ([], RequestStatus.ACTIVE),
        ([OperationStatus.COMPLETED], RequestStatus.COMPLETED),
        (
            [OperationStatus.COMPLETED, OperationStatus.NEEDS_INFORMATION],
            RequestStatus.PARTIALLY_COMPLETED,
        ),
        ([OperationStatus.NEEDS_INFORMATION], RequestStatus.NEEDS_INFORMATION),
        ([OperationStatus.EXECUTING, OperationStatus.FAILED], RequestStatus.ACTIVE),
        (
            [OperationStatus.READY_FOR_VALIDATION, OperationStatus.ESCALATED],
            RequestStatus.READY_FOR_VALIDATION,
        ),
        (
            [OperationStatus.COMPLETED, OperationStatus.ESCALATED],
            RequestStatus.PARTIALLY_COMPLETED,
        ),
        ([OperationStatus.FAILED], RequestStatus.FAILED),
    ],
)
def test_request_status_is_derived_from_independent_operation_states(
    statuses: list[OperationStatus], expected: RequestStatus
) -> None:
    assert derive_request_status(statuses) is expected
