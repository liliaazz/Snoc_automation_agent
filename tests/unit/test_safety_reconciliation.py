from types import SimpleNamespace
from typing import Any, cast

from snoc_agent.config import Settings
from snoc_agent.db.models import Operation
from snoc_agent.workflow.inbound_processor import (
    InboundProcessor,
    _has_explicit_cross_operation_ambiguity,
    _has_explicit_independent_request,
)


def test_explicit_cross_operation_ambiguity_is_detected() -> None:
    message = (
        "Les PDV sont 33000001 et 33000002, mais je ne sais plus quel numéro "
        "correspond à quelle demande."
    )

    assert _has_explicit_cross_operation_ambiguity(message) is True


def test_one_phone_candidate_reconciles_one_open_clarification_target() -> None:
    processor = cast(Any, object.__new__(InboundProcessor))
    processor.settings = Settings(_env_file=None)
    operation = Operation(
        action="otp_number_change",
        status="NEEDS_INFORMATION",
        pdv_code="22000001",
        phone=None,
        missing_fields=["new_phone"],
    )
    prepared = SimpleNamespace(
        context={
            "numeric_candidates_from_latest_reply": [
                {
                    "value": "22000001",
                    "kind_hint": "pdv_or_unknown",
                },
                {
                    "value": "0770000001",
                    "kind_hint": "phone_or_unknown",
                },
            ]
        }
    )

    proposal = processor._single_field_clarification_proposal(prepared, operation)

    assert proposal is not None
    assert proposal.action == "otp_number_change"
    assert proposal.pdv_code == "22000001"
    assert proposal.phone == "0770000001"
    assert proposal.missing_fields == []
    assert {item.source for item in proposal.evidence} == {
        "latest_user_message",
        "stored_request_state",
    }


def test_multiple_phone_candidates_never_reconcile_automatically() -> None:
    processor = cast(Any, object.__new__(InboundProcessor))
    processor.settings = Settings(_env_file=None)
    operation = Operation(
        action="otp_number_change",
        status="NEEDS_INFORMATION",
        pdv_code="22000001",
        phone=None,
        missing_fields=["new_phone"],
    )
    prepared = SimpleNamespace(
        context={
            "numeric_candidates_from_latest_reply": [
                {"value": "0770000001", "kind_hint": "phone_or_unknown"},
                {"value": "0770000002", "kind_hint": "phone_or_unknown"},
            ]
        }
    )

    assert processor._single_field_clarification_proposal(prepared, operation) is None


def test_conflicting_pdv_cannot_be_hidden_by_phone_only_reconciliation() -> None:
    processor = cast(Any, object.__new__(InboundProcessor))
    processor.settings = Settings(_env_file=None)
    operation = Operation(
        action="vpn_access",
        status="NEEDS_INFORMATION",
        pdv_code="22000001",
        phone=None,
        missing_fields=["phone"],
    )
    prepared = SimpleNamespace(
        context={
            "numeric_candidates_from_latest_reply": [
                {"value": "22000002", "kind_hint": "pdv_or_unknown"},
                {"value": "0770000001", "kind_hint": "phone_or_unknown"},
            ]
        }
    )

    assert processor._single_field_clarification_proposal(prepared, operation) is None


def test_explicit_independent_request_boundary_is_multilingual() -> None:
    assert _has_explicit_independent_request("This is a separate request for another POS.")
    assert _has_explicit_independent_request("Nouvelle demande indépendante pour un autre PDV.")
    assert _has_explicit_independent_request("هذا طلب جديد مستقل لنقطة بيع أخرى.")
    assert not _has_explicit_independent_request("Correction du PDV déjà envoyé.")
