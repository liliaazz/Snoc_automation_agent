from __future__ import annotations

from snoc_agent.mail.headers import (
    bare_address,
    build_references,
    build_wire_references,
    decode_header_value,
    decoded_addresses,
    normalize_message_id,
    normalize_subject,
    parse_references,
    reply_subject,
    wire_message_id,
)


def test_normalize_message_id_is_case_insensitive_and_preserves_rfc_shape() -> None:
    assert normalize_message_id("  < Example.42@Mail.EXAMPLE >  ") == "<example.42@mail.example>"
    assert normalize_message_id("Example.42@Mail.EXAMPLE") == "<example.42@mail.example>"
    assert normalize_message_id("<bad id@example.test>") is None
    assert normalize_message_id(None) is None


def test_parse_references_preserves_order_and_deduplicates_normalized_ids() -> None:
    value = "<Root@Example.test> <parent@example.test> <ROOT@example.test>"

    assert parse_references(value) == ["<root@example.test>", "<parent@example.test>"]
    assert parse_references("first@example.test second@example.test") == [
        "<first@example.test>",
        "<second@example.test>",
    ]


def test_build_references_appends_only_a_valid_unique_incoming_id() -> None:
    previous = ["<root@example.test>", "<parent@example.test>"]

    assert build_references(previous, "<PARENT@example.test>") == previous
    assert build_references(previous, "<latest@example.test>") == [
        *previous,
        "<latest@example.test>",
    ]
    assert build_references(previous, "<invalid id>") == previous


def test_subject_normalization_decodes_and_removes_repeated_reply_prefixes() -> None:
    encoded = "=?utf-8?q?RE=3A_Tr=3A_FW=3A_Demande_VPN_=C3=89quipe?="

    assert decode_header_value(encoded) == "RE: Tr: FW: Demande VPN Équipe"
    assert normalize_subject(encoded) == "demande vpn équipe"
    assert normalize_subject("  Re -   RE:  Demande   OTP  ") == "demande otp"
    assert reply_subject("RE: Tr: Demande VPN Équipe") == "Re: Demande VPN Équipe"


def test_wire_reply_headers_preserve_the_exact_parent_message_id() -> None:
    parent = "<CaseSensitive.Parent@Gmail.Example>"

    assert wire_message_id(parent) == parent
    assert build_wire_references(["<root@example.test>"], parent) == [
        "<root@example.test>",
        parent,
    ]


def test_address_helpers_decode_names_and_return_canonical_mailbox() -> None:
    addresses = decoded_addresses(
        ["=?utf-8?q?Jos=C3=A9_Dupont?= <Manager@Example.COM>, support@example.com"]
    )

    assert addresses == ["José Dupont <Manager@Example.COM>", "support@example.com"]
    assert bare_address(addresses[0]) == "manager@example.com"
