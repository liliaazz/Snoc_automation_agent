"""Conservative high-confidence cleanup adapted from semantic_clean_csv.py."""

from __future__ import annotations

import html
import re
import unicodedata

ANONYMIZATION_TAG_RE = re.compile(r"<(?:full\s+name|probably_name|sender|emiter|cc)>", re.I)
MAIL_METADATA_RE = re.compile(r"<(?:mailto|tel):[^>]*>", re.I)
OUTLOOK_LINK_RE = re.compile(r"<?https?://aka\.ms/[A-Za-z0-9_-]+>?", re.I)
EXTERNAL_BANNER_RE = re.compile(
    r"\bcaution\s+this\s+email\s+was\s+sent\s+from\s+an\s+external\s+source\s+"
    r"think\s+before\s+clicking\s+links\s+or\s+opening\s+attachments\b",
    re.I,
)
MOBILE_FOOTER_RE = re.compile(
    r"(?im)^\s*(?:sent from outlook(?: for android)?|obtenir outlook pour android|"
    r"envoy[ée] depuis mon (?:appareil|t[ée]l[ée]phone).*)\s*$"
)


def normalize_unicode(value: object) -> str:
    if value is None:
        return ""
    return html.unescape(unicodedata.normalize("NFKC", str(value)))


def clean_high_confidence_artifacts(value: str) -> tuple[str, list[str]]:
    text = normalize_unicode(value)
    applied: list[str] = []
    for name, pattern in (
        ("anonymization_tags_removed", ANONYMIZATION_TAG_RE),
        ("mail_link_metadata_removed", MAIL_METADATA_RE),
        ("outlook_shortlink_removed", OUTLOOK_LINK_RE),
        ("external_email_banner_removed", EXTERNAL_BANNER_RE),
        ("mobile_footer_removed", MOBILE_FOOTER_RE),
    ):
        updated = pattern.sub(" ", text)
        if updated != text:
            applied.append(name)
        text = updated
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line), applied
