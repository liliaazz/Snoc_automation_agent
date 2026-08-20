from __future__ import annotations

import re
from email.message import EmailMessage

import pytest

from snoc_agent.mail.markers import (
    completion_marker,
    generate_request_reference,
    parse_completion_markers,
    parse_request_references,
)
from snoc_agent.mail.parser import classify_automated, parse_email


def test_request_reference_generation_parsing_and_deduplication() -> None:
    generated = generate_request_reference()

    assert re.fullmatch(r"SNOC-REQ-[A-F0-9]{12}", generated)
    assert parse_request_references(
        f"Objet [{generated.lower()}] Informations",
        f"Référence : {generated}",
    ) == [generated]


def test_completion_marker_validates_and_round_trips_reference() -> None:
    reference = "SNOC-REQ-A84F91C274D2"
    marker = completion_marker(reference.lower())

    assert marker == "[[SNOC_REQUEST_CLOSED:SNOC-REQ-A84F91C274D2]]"
    assert parse_completion_markers(f"{marker}\n{marker.lower()}") == [reference]
    with pytest.raises(ValueError, match="invalid public request reference"):
        completion_marker("SNOC-REQ-not-valid")


@pytest.mark.parametrize(
    ("headers", "text", "system_address", "expected"),
    [
        (
            {
                "From": "rewritten-sender@example.com",
                "Subject": "Réponse SNOC",
                "X-SNOC-Agent-Generated": "true",
            },
            "contenu",
            "different-agent@example.com",
            "system_self_message",
        ),
        (
            {"From": "agent@example.com", "Subject": "Demande"},
            "contenu",
            "agent@example.com",
            "system_self_message",
        ),
        (
            {"From": "MAILER-DAEMON@example.com", "Subject": "Undelivered mail"},
            "contenu",
            "",
            "delivery_failure",
        ),
        (
            {
                "From": "manager@example.com",
                "Subject": "Out of office",
                "Auto-Submitted": "auto-replied; vacation",
            },
            "contenu",
            "",
            "out_of_office",
        ),
        (
            {"From": "list@example.com", "Subject": "Bulletin", "Precedence": "bulk"},
            "contenu",
            "",
            "automated",
        ),
        (
            {
                "From": "unknown@example.com",
                "Subject": "Offre",
                "X-Spam-Flag": "YES",
            },
            "contenu",
            "",
            "obvious_spam",
        ),
        (
            {"From": "manager@example.com", "Subject": "Réponse automatique"},
            "contenu",
            "",
            "automatic_acknowledgement",
        ),
        (
            {"From": "manager@example.com", "Subject": "Demande de déblocage"},
            "Merci de débloquer le PDV.",
            "",
            None,
        ),
    ],
)
def test_automated_message_filtering(
    headers: dict[str, str], text: str, system_address: str, expected: str | None
) -> None:
    message = EmailMessage()
    for name, value in headers.items():
        message[name] = value
    message.set_content(text)

    assert classify_automated(message, text, system_address=system_address) == expected


def test_parser_stores_but_classifies_automated_message() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<vacation@example.com>"
    message["From"] = "manager@example.com"
    message["To"] = "snoc@example.com"
    message["Subject"] = "Absence du bureau"
    message["Auto-Submitted"] = "auto-replied"
    message.set_content("Je suis absent cette semaine.")

    parsed = parse_email(message.as_bytes())

    assert parsed.normalized_message_id == "<vacation@example.com>"
    assert parsed.text_body == "Je suis absent cette semaine."
    assert parsed.automated_classification == "out_of_office"
