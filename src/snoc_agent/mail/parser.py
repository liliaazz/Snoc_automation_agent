"""Parse raw RFC messages into a typed, storage-ready representation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Any

from snoc_agent.mail.headers import (
    bare_address,
    decode_header_value,
    decoded_addresses,
    normalize_message_id,
    normalize_subject,
    parse_references,
)
from snoc_agent.mail.mime import ContentLimits, extract_content
from snoc_agent.mail.reply_segmenter import SegmentedReply, segment_reply


@dataclass(frozen=True, slots=True)
class ParsedEmail:
    rfc_message_id: str | None
    normalized_message_id: str | None
    in_reply_to: str | None
    references: list[str]
    sender: str
    sender_address: str
    reply_to: str | None
    recipients: list[str]
    cc: list[str]
    subject: str
    normalized_subject: str
    message_date: datetime | None
    text_body: str
    html_body: str | None
    mime_type: str
    attachment_metadata: list[dict[str, Any]]
    segmentation: SegmentedReply
    automated_classification: str | None
    parsing_warnings: list[str]


def _message_date(message: Message, warnings: list[str]) -> datetime | None:
    value = message.get("Date")
    if not value:
        return None
    try:
        result = parsedate_to_datetime(value)
        if result.tzinfo is None:
            warnings.append("naive_message_date_assumed_utc")
            return result.replace(tzinfo=UTC)
        return result.astimezone(UTC)
    except (TypeError, ValueError, OverflowError):
        warnings.append("invalid_date_header")
        return None


def classify_automated(message: Message, text: str, *, system_address: str = "") -> str | None:
    sender = bare_address(str(message.get("From", "")))
    auto_submitted = str(message.get("Auto-Submitted", "")).strip().casefold()
    precedence = str(message.get("Precedence", "")).strip().casefold()
    subject = decode_header_value(message.get("Subject")).casefold()
    current = text[:2000].casefold()

    if str(message.get("X-SNOC-Agent-Generated", "")).strip().casefold() == "true":
        return "system_self_message"
    if system_address and sender == system_address.casefold():
        return "system_self_message"
    if (
        message.get_content_type() == "multipart/report"
        or "delivery-status" in str(message.get("Content-Type", "")).casefold()
    ):
        return "delivery_failure"
    if "mailer-daemon" in sender or "postmaster" in sender or "undeliver" in subject:
        return "delivery_failure"
    spam_flag = str(message.get("X-Spam-Flag", "")).strip().casefold()
    spam_status = str(message.get("X-Spam-Status", "")).strip().casefold()
    if (
        spam_flag in {"yes", "true", "1"}
        or spam_status.startswith("yes")
        or subject.startswith(("***spam***", "[spam]"))
    ):
        return "obvious_spam"
    if auto_submitted and auto_submitted != "no":
        if "vacation" in auto_submitted or any(
            token in subject for token in ("out of office", "absence du bureau")
        ):
            return "out_of_office"
        return "automated"
    if precedence in {"bulk", "list", "junk", "auto_reply"}:
        return "automated"
    if any(
        token in subject or token in current for token in ("out of office", "absence du bureau")
    ):
        return "out_of_office"
    if any(
        token in subject
        for token in ("automatic reply", "réponse automatique", "accusé de réception")
    ):
        return "automatic_acknowledgement"
    return None


def parse_email(
    raw_message: bytes,
    *,
    system_address: str = "",
    content_limits: ContentLimits | None = None,
) -> ParsedEmail:
    message = BytesParser(policy=policy.default).parsebytes(raw_message)
    warnings: list[str] = []
    body, html_body, attachments, content_warnings = extract_content(message, limits=content_limits)
    warnings.extend(content_warnings)
    segmented = segment_reply(body)
    warnings.extend(segmented.segmentation_warnings)

    raw_message_id = str(message.get("Message-ID", "")).strip() or None
    normalized_message_id = normalize_message_id(raw_message_id)
    if raw_message_id and normalized_message_id is None:
        warnings.append("invalid_message_id")
    if not raw_message_id:
        warnings.append("missing_message_id")

    raw_in_reply_to = str(message.get("In-Reply-To", "")).strip() or None
    in_reply_to = normalize_message_id(raw_in_reply_to)
    if raw_in_reply_to and in_reply_to is None:
        warnings.append("invalid_in_reply_to")

    from_value = decode_header_value(message.get("From"))
    sender_address = bare_address(from_value)
    if not sender_address:
        warnings.append("missing_or_invalid_sender")

    return ParsedEmail(
        rfc_message_id=raw_message_id,
        normalized_message_id=normalized_message_id,
        in_reply_to=in_reply_to,
        references=parse_references(str(message.get("References", ""))),
        sender=from_value,
        sender_address=sender_address,
        reply_to=decode_header_value(message.get("Reply-To")) or None,
        recipients=decoded_addresses(message.get_all("To", [])),
        cc=decoded_addresses(message.get_all("Cc", [])),
        subject=decode_header_value(message.get("Subject")),
        normalized_subject=normalize_subject(message.get("Subject")),
        message_date=_message_date(message, warnings),
        text_body=body,
        html_body=html_body,
        mime_type=message.get_content_type(),
        attachment_metadata=attachments,
        segmentation=segmented,
        automated_classification=classify_automated(message, body, system_address=system_address),
        parsing_warnings=warnings,
    )
