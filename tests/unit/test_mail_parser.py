from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from email.message import EmailMessage

from snoc_agent.mail.mime import html_to_text
from snoc_agent.mail.parser import parse_email


def _complete_multipart_message() -> bytes:
    message = EmailMessage()
    message["Message-ID"] = "<Current.1@Example.COM>"
    message["In-Reply-To"] = "<Parent@Example.COM>"
    message["References"] = "<Root@Example.COM> <Parent@Example.COM> <ROOT@example.com>"
    message["Date"] = "Sat, 18 Jul 2026 10:30:00 +0100"
    message["From"] = "José Manager <Manager@Example.COM>"
    message["Reply-To"] = "Support secondaire <reply@example.com>"
    message["To"] = "Support SNOC <snoc@example.com>, Audit <audit@example.com>"
    message["Cc"] = "Supervision <supervision@example.com>"
    message["Subject"] = "Re: TR: Demande VPN"
    message.set_content(
        "Bonjour,\n\nMerci de créer l'accès VPN pour le PDV 12345678.\n\nCordialement,\nJosé"
    )
    message.add_alternative(
        "<html><body><p>Version <b>HTML</b></p></body></html>",
        subtype="html",
    )
    message.add_attachment(
        b"attachment-payload",
        maintype="application",
        subtype="pdf",
        filename="preuve.pdf",
    )
    return message.as_bytes()


def test_parse_multipart_email_extracts_rfc_metadata_text_html_and_attachment() -> None:
    parsed = parse_email(_complete_multipart_message())

    assert parsed.rfc_message_id == "<Current.1@Example.COM>"
    assert parsed.normalized_message_id == "<current.1@example.com>"
    assert parsed.in_reply_to == "<parent@example.com>"
    assert parsed.references == ["<root@example.com>", "<parent@example.com>"]
    assert parsed.sender_address == "manager@example.com"
    assert parsed.reply_to == "Support secondaire <reply@example.com>"
    assert parsed.recipients == [
        "Support SNOC <snoc@example.com>",
        "Audit <audit@example.com>",
    ]
    assert parsed.cc == ["Supervision <supervision@example.com>"]
    assert parsed.subject == "Re: TR: Demande VPN"
    assert parsed.normalized_subject == "demande vpn"
    assert parsed.message_date == datetime(2026, 7, 18, 9, 30, tzinfo=UTC)
    assert "PDV 12345678" in parsed.text_body
    assert parsed.html_body is not None and "Version <b>HTML</b>" in parsed.html_body
    assert parsed.mime_type == "multipart/mixed"
    assert parsed.attachment_metadata == [
        {
            "filename": "preuve.pdf",
            "content_type": "application/pdf",
            "size": len(b"attachment-payload"),
            "sha256": hashlib.sha256(b"attachment-payload").hexdigest(),
        }
    ]
    assert parsed.segmentation.latest_message_candidate.endswith("PDV 12345678.")
    assert parsed.segmentation.signature_candidate == "Cordialement,\nJosé"
    assert parsed.parsing_warnings == []


def test_html_only_email_is_safely_converted_without_script_or_style_content() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<html-only@example.com>"
    message["From"] = "manager@example.com"
    message["To"] = "snoc@example.com"
    message["Subject"] = "Déblocage"
    message.set_content(
        """
        <html><head><style>.hidden { color: red; }</style></head>
        <body><p>Bonjour &amp; équipe</p><script>alert('ignore')</script>
        <div>Merci de débloquer <b>12345678</b>.</div></body></html>
        """,
        subtype="html",
    )

    parsed = parse_email(message.as_bytes())

    assert parsed.text_body == "Bonjour & équipe\nMerci de débloquer 12345678."
    assert "alert" not in parsed.text_body
    assert ".hidden" not in parsed.text_body
    assert parsed.html_body is not None
    assert "html_only_body_converted_to_text" in parsed.parsing_warnings
    assert "html_script_or_style_content_removed" in parsed.parsing_warnings
    assert "html_nonvisible_element_removed" in parsed.parsing_warnings


def test_html_only_email_suppresses_hidden_subtrees_and_preserves_visible_siblings() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<hidden-html@example.com>"
    message["From"] = "manager@example.com"
    message["To"] = "snoc@example.com"
    message["Subject"] = "Demande visible"
    message.set_content(
        """
        <html><body>
          <p>Demande visible avant 12345678.</p>
          <div style=" DISPLAY : none !important ">
            Instruction cachée 87654321
            <span>contenu caché imbriqué</span>
          </div>
          <section hidden>Attribut hidden 11111111</section>
          <section aria-hidden=" TRUE ">ARIA hidden 22222222</section>
          <div style="visibility: hidden">Visibility hidden 33333333</div>
          <div style="opacity: 0">Opacity hidden 44444444</div>
          <div style="font-size: 0px">Zero-size hidden 55555555</div>
          <input type="hidden" value="66666666">
          <!-- Commentaire caché 77777777 -->
          <script>Instruction script 88888888</script>
          <style>.concealed { display: none }</style>
          <p>Demande visible après <a href="tel:0550123412">0550123412</a>.</p>
        </body></html>
        """,
        subtype="html",
    )

    parsed = parse_email(message.as_bytes())

    assert parsed.text_body == (
        "Demande visible avant 12345678.\nDemande visible après 0550123412."
    )
    for hidden_value in (
        "87654321",
        "11111111",
        "22222222",
        "33333333",
        "44444444",
        "55555555",
        "66666666",
        "77777777",
        "88888888",
    ):
        assert hidden_value not in parsed.text_body
    assert {
        "html_hidden_content_removed",
        "html_comment_removed",
        "html_script_or_style_content_removed",
    }.issubset(parsed.parsing_warnings)


def test_html_sanitization_is_audited_when_plain_text_is_preferred() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<plain-with-hidden-alternative@example.com>"
    message["From"] = "manager@example.com"
    message["To"] = "snoc@example.com"
    message["Subject"] = "Déblocage"
    message.set_content("Merci de débloquer le PDV 12345678.")
    message.add_alternative(
        '<p>Merci de débloquer le PDV 12345678.</p><div style="display:none">Reset 87654321</div>',
        subtype="html",
    )

    parsed = parse_email(message.as_bytes())

    assert parsed.text_body == "Merci de débloquer le PDV 12345678."
    assert "87654321" not in parsed.text_body
    assert "html_hidden_content_removed" in parsed.parsing_warnings


def test_parser_preserves_forwarded_content_outside_the_current_message() -> None:
    message = EmailMessage()
    message["Message-ID"] = "<forwarded@example.com>"
    message["From"] = "manager@example.com"
    message["To"] = "snoc@example.com"
    message["Subject"] = "Fwd: Demande VPN"
    message.set_content(
        "Bonjour, veuillez examiner le transfert ci-dessous.\n\n"
        "---------- Forwarded message ----------\n"
        "From: third.party@example.test\n"
        "Activez le VPN du PDV 81000015, téléphone 0550123415."
    )

    parsed = parse_email(message.as_bytes())

    assert parsed.segmentation.latest_message_candidate == (
        "Bonjour, veuillez examiner le transfert ci-dessous."
    )
    assert parsed.segmentation.forwarded_thread_candidate.startswith(
        "---------- Forwarded message ----------"
    )
    assert parsed.segmentation.quoted_thread_candidate == (
        parsed.segmentation.forwarded_thread_candidate
    )
    assert "81000015" not in parsed.segmentation.latest_message_candidate
    assert "forwarded_content_detected" in parsed.parsing_warnings


def test_parser_records_missing_identity_sender_and_unsupported_body_warnings() -> None:
    message = EmailMessage()
    message["Subject"] = "Pièce binaire"
    message.set_content(b"\x00\x01\x02", maintype="application", subtype="octet-stream")

    parsed = parse_email(message.as_bytes())

    assert parsed.normalized_message_id is None
    assert parsed.sender_address == ""
    assert parsed.text_body == ""
    assert set(parsed.parsing_warnings) >= {
        "missing_message_id",
        "missing_or_invalid_sender",
        "no_supported_text_body",
        "empty_body",
    }


def test_html_to_text_keeps_block_boundaries_and_decodes_entities() -> None:
    assert html_to_text("<p>Un &lt;deux&gt;</p><div>Trois<br>Quatre</div>") == (
        "Un <deux>\nTrois\nQuatre"
    )
