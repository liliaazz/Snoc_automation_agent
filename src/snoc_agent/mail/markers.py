"""Visible request references and trusted completion boundary helpers."""

from __future__ import annotations

import re
import secrets

REQUEST_REFERENCE_RE = re.compile(r"\bSNOC-REQ-[A-F0-9]{12}\b", re.IGNORECASE)
COMPLETION_MARKER_RE = re.compile(
    r"\[\[SNOC_REQUEST_CLOSED:(SNOC-REQ-[A-F0-9]{12})\]\]", re.IGNORECASE
)


def generate_request_reference() -> str:
    return f"SNOC-REQ-{secrets.token_hex(6).upper()}"


def parse_request_references(*values: str | None) -> list[str]:
    found: list[str] = []
    for value in values:
        if value:
            found.extend(match.upper() for match in REQUEST_REFERENCE_RE.findall(value))
    return list(dict.fromkeys(found))


def completion_marker(reference: str) -> str:
    if REQUEST_REFERENCE_RE.fullmatch(reference) is None:
        raise ValueError("invalid public request reference")
    return f"[[SNOC_REQUEST_CLOSED:{reference.upper()}]]"


def parse_completion_markers(value: str) -> list[str]:
    return list(dict.fromkeys(match.upper() for match in COMPLETION_MARKER_RE.findall(value)))
