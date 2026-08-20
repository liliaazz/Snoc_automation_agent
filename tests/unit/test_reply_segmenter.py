from __future__ import annotations

from snoc_agent.mail.reply_segmenter import segment_reply


def test_segment_reply_separates_latest_message_signature_and_french_quote() -> None:
    result = segment_reply(
        "Le nouveau numéro est 777888999.\n\n"
        "Cordialement,\nAlice\n\n"
        "Le vendredi 17 juillet, Support SNOC a écrit :\n"
        "> Merci de préciser le nouveau numéro."
    )

    assert result.latest_message_candidate == "Le nouveau numéro est 777888999."
    assert result.signature_candidate == "Cordialement,\nAlice"
    assert result.quoted_thread_candidate.startswith("Le vendredi 17 juillet")
    assert result.segmentation_confidence == 0.95
    assert result.segmentation_warnings == ()


def test_segment_reply_uses_quoted_prefix_as_lower_confidence_fallback() -> None:
    result = segment_reply("Voici la correction.\n> Ancienne valeur : 700000000")

    assert result.latest_message_candidate == "Voici la correction."
    assert result.quoted_thread_candidate == "> Ancienne valeur : 700000000"
    assert result.segmentation_confidence == 0.75
    assert result.segmentation_warnings == ("quote_detected_from_prefix_only",)


def test_segment_reply_marks_quote_only_and_empty_messages() -> None:
    quote_only = segment_reply("> Demande historique")
    empty = segment_reply(" \r\n ")

    assert quote_only.latest_message_candidate == ""
    assert quote_only.segmentation_confidence == 0.45
    assert set(quote_only.segmentation_warnings) == {
        "quote_detected_from_prefix_only",
        "no_unquoted_text",
    }
    assert empty.latest_message_candidate == ""
    assert empty.segmentation_confidence == 1.0
    assert empty.segmentation_warnings == ("empty_body",)


def test_early_signature_marker_is_not_allowed_to_erase_the_message() -> None:
    result = segment_reply("Cordialement\nCette ligne contient encore la demande 12345678.")

    assert result.latest_message_candidate.startswith("Cordialement")
    assert result.signature_candidate == ""
    assert result.segmentation_warnings == ("early_signature_marker_ignored",)


def test_forwarded_message_is_separated_from_the_current_sender_text() -> None:
    result = segment_reply(
        "Bonjour, pouvez-vous examiner la demande transférée ci-dessous ?\n\n"
        "---------- Forwarded message ----------\n"
        "From: unknown.person@example.test\n"
        "Sent: Monday, July 27, 2026 09:00\n"
        "Subject: Demande VPN\n\n"
        "Activez le VPN du PDV 81000015, téléphone 0550123415."
    )

    assert result.latest_message_candidate == (
        "Bonjour, pouvez-vous examiner la demande transférée ci-dessous ?"
    )
    assert result.quoted_thread_candidate.startswith("---------- Forwarded message ----------")
    assert result.forwarded_thread_candidate == result.quoted_thread_candidate
    assert result.segmentation_confidence == 0.95
    assert result.segmentation_warnings == ("forwarded_content_detected",)


def test_french_forwarded_message_preserves_forwarded_provenance() -> None:
    result = segment_reply(
        "Merci de vérifier le transfert.\n\n"
        "---------- Message transféré ----------\n"
        "De : tiers@example.test\n"
        "Envoyé : lundi 27 juillet 2026\n"
        "Objet : Réinitialisation\n\n"
        "Réinitialisez le mot de passe du PDV 81000014."
    )

    assert result.latest_message_candidate == "Merci de vérifier le transfert."
    assert result.forwarded_thread_candidate.startswith("---------- Message transféré ----------")
    assert "81000014" not in result.latest_message_candidate
    assert "81000014" in result.quoted_thread_candidate
    assert "forwarded_content_detected" in result.segmentation_warnings


def test_forwarded_header_block_without_banner_is_detected() -> None:
    result = segment_reply(
        "Pour revue uniquement.\n\n"
        "From: third.party@example.test\n"
        "Sent: Monday, July 27, 2026 09:00\n"
        "To: operator@example.test\n"
        "Subject: OTP\n\n"
        "Change OTP for PDV 81000009 to 0550123409."
    )

    assert result.latest_message_candidate == "Pour revue uniquement."
    assert result.forwarded_thread_candidate.startswith("From: third.party@example.test")
    assert result.quoted_thread_candidate == result.forwarded_thread_candidate
    assert result.segmentation_warnings == ("forwarded_content_detected",)
