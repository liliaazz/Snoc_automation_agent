from __future__ import annotations

import pytest

from snoc_agent.mail.templates import (
    OperationMailView,
    clarification_email,
    completion_email,
    pending_execution_email,
)

REFERENCE = "SNOC-REQ-A84F91C274D2"
OPERATION = OperationMailView(
    sequence_number=1,
    action="password_reset",
    pdv_code="81000605",
    missing_fields=("phone",),
    status_label="traitée avec succès",
)


@pytest.mark.parametrize(
    ("render", "expected_phrase"),
    [
        (
            lambda: clarification_email(REFERENCE, [OPERATION]),
            "Information nécessaire : numéro de téléphone",
        ),
        (
            lambda: completion_email(REFERENCE, [OPERATION]),
            "traitée avec succès",
        ),
        (
            lambda: pending_execution_email(REFERENCE, [OPERATION], grace_seconds=30),
            "traitement automatique commencera dans environ 30 secondes",
        ),
    ],
)
def test_customer_templates_are_friendly_and_hide_internal_identifiers(
    render,
    expected_phrase: str,
) -> None:
    subject, body = render()

    assert REFERENCE not in subject
    assert "OP-01" not in body
    assert "81000605" not in body
    assert "se terminant par 0605" in body
    assert "SNOC_REQUEST_CLOSED" not in body
    assert "[[" not in body
    assert expected_phrase in body
    assert f"Référence de suivi : {REFERENCE}" in body


def test_short_pdv_identifier_is_never_exposed() -> None:
    operation = OperationMailView(
        sequence_number=7,
        action="account_unblock",
        pdv_code="123",
        status_label="traitée avec succès",
    )

    _subject, body = completion_email(REFERENCE, [operation])

    assert "123" not in body
    assert "PDV concerné" in body
