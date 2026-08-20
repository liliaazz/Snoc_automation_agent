"""Numeric discovery with exact section provenance and offsets.

Regex only proposes candidates; attribution and semantic support remain model responsibilities.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from snoc_agent.ai.preprocessing import normalize_unicode
from snoc_agent.domain.value_objects import normalize_numeric

# Do not start a numeric candidate at the trailing digit of an alphanumeric
# word such as the Algerian-Darija token ``ta3``. The prior digit-only
# look-behind turned ``ta3 81000013`` into the invented value ``381000013``.
# A real identifier may still follow that word after whitespace, so the engine
# naturally retries at the first digit of ``81000013``.
NUMERIC_GROUP_RE = re.compile(r"(?<!\w)(\+?\d(?:[ \t()./\-]*\d){7,14})(?![ \t()./\-]*\d)")


class NumericCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    raw_value: str
    kind_hint: Literal["pdv_or_unknown", "phone_or_unknown", "numeric_unknown"]
    section: Literal[
        "latest_user_message", "relevant_thread_context", "quoted_closed_history", "signature"
    ]
    start: int
    end: int
    context: str


def extract_numeric_candidates(
    text: str,
    *,
    section: Literal[
        "latest_user_message", "relevant_thread_context", "quoted_closed_history", "signature"
    ] = "latest_user_message",
) -> list[NumericCandidate]:
    source = normalize_unicode(text)
    candidates: list[NumericCandidate] = []
    for match in NUMERIC_GROUP_RE.finditer(source):
        raw = match.group(1)
        normalized = normalize_numeric(raw, keep_leading_plus=True)
        if normalized is None:
            continue
        digit_count = len(normalized.lstrip("+"))
        hint: Literal["pdv_or_unknown", "phone_or_unknown", "numeric_unknown"]
        if digit_count == 8 and not normalized.startswith("+"):
            hint = "pdv_or_unknown"
        elif 9 <= digit_count <= 15:
            hint = "phone_or_unknown"
        else:
            hint = "numeric_unknown"
        context_start = max(0, match.start() - 60)
        context_end = min(len(source), match.end() + 60)
        candidates.append(
            NumericCandidate(
                value=normalized,
                raw_value=raw,
                kind_hint=hint,
                section=section,
                start=match.start(),
                end=match.end(),
                context=source[context_start:context_end],
            )
        )
    return candidates
