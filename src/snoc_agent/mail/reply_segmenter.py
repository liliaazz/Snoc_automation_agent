"""Non-authoritative latest/quote/signature candidate segmentation."""

from __future__ import annotations

import re
from dataclasses import dataclass

QUOTE_SEPARATOR_RE = re.compile(
    r"(?im)^(?:-{2,}\s*(?:message d['\u2019]?origine|original message).*|"
    r"_{5,}\s*|on .{0,180} wrote:\s*|le .{0,180} a [ée]crit\s*:\s*|"
    r"(?:de|from)\s*:\s*[^\n]+\n(?:envoy[ée]|sent)\s*:.*)$"
)
FORWARDED_SEPARATOR_RE = re.compile(
    r"(?im)^(?:"
    r"-{2,}\s*(?:forwarded message|message (?:transf[ée]r[ée]|r[ée]exp[ée]di[ée])|"
    r"الرسالة المعاد توجيهها)\s*-{2,}\s*|"
    r"begin forwarded message\s*:?\s*|"
    r"d[ée]but du message transf[ée]r[ée]\s*:?\s*|"
    r"(?:from|de)\s*:\s*[^\n]+\n(?:sent|envoy[ée])\s*:\s*[^\n]+"
    r"(?:\n(?:to|[àa])\s*:\s*[^\n]+)?"
    r"(?:\n(?:subject|objet)\s*:\s*[^\n]+)?"
    r")$"
)
SIGNATURE_RE = re.compile(
    r"(?im)^(?:--\s*$|cordialement[,\s]*$|bien cordialement[,\s]*$|"
    r"salutations[,\s]*$|cdlt[,\s]*$|sent from (?:my|outlook).*$)"
)
QUOTED_LINE_RE = re.compile(r"(?m)^>+")


@dataclass(frozen=True, slots=True)
class SegmentedReply:
    latest_message_candidate: str
    quoted_thread_candidate: str
    signature_candidate: str
    segmentation_confidence: float
    segmentation_warnings: tuple[str, ...]
    forwarded_thread_candidate: str = ""


def segment_reply(text: str) -> SegmentedReply:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return SegmentedReply("", "", "", 1.0, ("empty_body",))

    warnings: list[str] = []
    quote_start: int | None = None
    quote_separator = QUOTE_SEPARATOR_RE.search(normalized)
    forwarded_separator = FORWARDED_SEPARATOR_RE.search(normalized)
    forwarded = normalized[forwarded_separator.start() :].strip() if forwarded_separator else ""
    if forwarded_separator:
        warnings.append("forwarded_content_detected")

    separators = [match for match in (quote_separator, forwarded_separator) if match is not None]
    separator = min(separators, key=lambda match: match.start()) if separators else None
    if separator is not None:
        quote_start = separator.start()
    else:
        quoted_line = QUOTED_LINE_RE.search(normalized)
        if quoted_line:
            quote_start = quoted_line.start()
            warnings.append("quote_detected_from_prefix_only")

    current = normalized[:quote_start].rstrip() if quote_start is not None else normalized
    quoted = normalized[quote_start:].strip() if quote_start is not None else ""

    signature = ""
    signature_matches = list(SIGNATURE_RE.finditer(current))
    if signature_matches:
        candidate = signature_matches[-1]
        # Only split a plausible trailing signature, not a greeting in a long message.
        if candidate.start() >= len(current) // 3:
            signature = current[candidate.start() :].strip()
            current = current[: candidate.start()].rstrip()
        else:
            warnings.append("early_signature_marker_ignored")

    confidence = 0.95 if separator else 0.75 if quote_start is not None else 0.8
    if not current and quoted:
        warnings.append("no_unquoted_text")
        confidence = min(confidence, 0.45)
    return SegmentedReply(
        current,
        quoted,
        signature,
        confidence,
        tuple(warnings),
        forwarded_thread_candidate=forwarded,
    )
