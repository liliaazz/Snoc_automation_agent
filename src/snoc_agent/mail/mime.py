"""MIME body and attachment extraction helpers."""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from typing import Any, ClassVar


@dataclass(frozen=True, slots=True)
class ContentLimits:
    max_text_part_bytes: int
    max_html_part_bytes: int
    max_attachment_count: int
    max_attachment_bytes: int


class _TextExtractor(HTMLParser):
    BLOCK_TAGS: ClassVar[set[str]] = {
        "p",
        "div",
        "br",
        "li",
        "tr",
        "h1",
        "h2",
        "h3",
        "blockquote",
    }
    VOID_TAGS: ClassVar[set[str]] = {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
    NON_VISIBLE_TAGS: ClassVar[set[str]] = {
        "head",
        "noscript",
        "script",
        "style",
        "template",
        "title",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.element_stack: list[tuple[str, bool]] = []
        self.warnings: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        inherited_suppression = self._suppressed
        suppression_warning = _suppression_warning(tag, attrs)
        suppressed = inherited_suppression or suppression_warning is not None
        if suppression_warning is not None and suppression_warning not in self.warnings:
            self.warnings.append(suppression_warning)
        if tag not in self.VOID_TAGS:
            self.element_stack.append((tag, suppressed))
        if not suppressed and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        matching_index = next(
            (
                index
                for index in range(len(self.element_stack) - 1, -1, -1)
                if self.element_stack[index][0] == tag
            ),
            None,
        )
        if matching_index is None:
            if not self._suppressed and tag in self.BLOCK_TAGS:
                self.parts.append("\n")
            return
        _matched_tag, suppressed = self.element_stack[matching_index]
        del self.element_stack[matching_index:]
        if not suppressed and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.casefold()
        suppression_warning = _suppression_warning(tag, attrs)
        if suppression_warning is not None and suppression_warning not in self.warnings:
            self.warnings.append(suppression_warning)
        if not self._suppressed and suppression_warning is None and tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._suppressed:
            self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        del data
        if "html_comment_removed" not in self.warnings:
            self.warnings.append("html_comment_removed")

    @property
    def _suppressed(self) -> bool:
        return bool(self.element_stack and self.element_stack[-1][1])


def _css_zero(value: str) -> bool:
    candidate = value.strip().casefold()
    candidate = re.sub(r"\s*!important\s*$", "", candidate).strip()
    return (
        re.fullmatch(r"(?:0+(?:\.0+)?|\.0+)(?:px|pt|pc|em|rem|ex|ch|%|vh|vw)?", candidate)
        is not None
    )


def _style_hides_content(value: str) -> bool:
    declarations: dict[str, str] = {}
    cleaned = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    for declaration in cleaned.split(";"):
        name, separator, raw_value = declaration.partition(":")
        if not separator:
            continue
        declarations[name.strip().casefold()] = re.sub(
            r"\s*!important\s*$", "", raw_value.strip().casefold()
        ).strip()

    if declarations.get("display") == "none":
        return True
    if declarations.get("visibility") in {"hidden", "collapse"}:
        return True
    if declarations.get("content-visibility") == "hidden":
        return True
    opacity = declarations.get("opacity")
    if opacity is not None:
        try:
            if float(opacity) == 0:
                return True
        except ValueError:
            pass
    if (font_size := declarations.get("font-size")) is not None and _css_zero(font_size):
        return True

    width_values = [declarations[name] for name in ("width", "max-width") if name in declarations]
    height_values = [
        declarations[name] for name in ("height", "max-height") if name in declarations
    ]
    if (
        width_values
        and height_values
        and all(_css_zero(item) for item in [*width_values, *height_values])
    ):
        return True
    return declarations.get("overflow") == "hidden" and any(
        _css_zero(item) for item in [*width_values, *height_values]
    )


def _suppression_warning(tag: str, attrs: list[tuple[str, str | None]]) -> str | None:
    if tag in {"script", "style"}:
        return "html_script_or_style_content_removed"
    if tag in _TextExtractor.NON_VISIBLE_TAGS:
        return "html_nonvisible_element_removed"

    normalized_attrs = {name.casefold(): (value or "").strip() for name, value in attrs if name}
    if "hidden" in normalized_attrs:
        return "html_hidden_content_removed"
    if normalized_attrs.get("aria-hidden", "").casefold() in {"true", "1", "yes"}:
        return "html_hidden_content_removed"
    if tag == "input" and normalized_attrs.get("type", "").casefold() == "hidden":
        return "html_hidden_content_removed"
    style = normalized_attrs.get("style")
    if style and _style_hides_content(style):
        return "html_hidden_content_removed"
    return None


def _visible_html_text(value: str) -> tuple[str, list[str]]:
    parser = _TextExtractor()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:
        return "", ["html_sanitization_failed"]
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in html.unescape(text).splitlines()]
    return "\n".join(line for line in lines if line), parser.warnings


def html_to_text(value: str) -> str:
    text, _warnings = _visible_html_text(value)
    return text


def decode_part(part: Message, *, max_bytes: int | None = None) -> tuple[str, bool]:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        text = raw if isinstance(raw, str) else ""
        if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
            return text.encode("utf-8")[:max_bytes].decode("utf-8", errors="replace"), True
        return text, False
    if not isinstance(payload, bytes):
        return str(payload), False
    truncated = max_bytes is not None and len(payload) > max_bytes
    if truncated:
        payload = payload[:max_bytes]
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace"), truncated
    except LookupError:
        return payload.decode("utf-8", errors="replace"), truncated


def extract_content(
    message: Message, *, limits: ContentLimits | None = None
) -> tuple[str, str | None, list[dict[str, Any]], list[str]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[dict[str, Any]] = []
    warnings: list[str] = []

    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = part.get_content_disposition()
        filename = part.get_filename()
        if disposition == "attachment" or filename:
            if limits is not None and len(attachments) >= limits.max_attachment_count:
                if "attachment_count_limit_exceeded" not in warnings:
                    warnings.append("attachment_count_limit_exceeded")
                continue
            decoded_payload = part.get_payload(decode=True)
            payload = decoded_payload if isinstance(decoded_payload, bytes) else b""
            oversized = limits is not None and len(payload) > limits.max_attachment_bytes
            metadata = {
                "filename": filename or "unnamed",
                "content_type": content_type,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
            if oversized:
                metadata["exceeds_size_limit"] = True
            attachments.append(metadata)
            if oversized and "attachment_size_limit_exceeded" not in warnings:
                warnings.append("attachment_size_limit_exceeded")
            continue
        if content_type == "text/plain":
            decoded, truncated = decode_part(
                part,
                max_bytes=limits.max_text_part_bytes if limits is not None else None,
            )
            plain_parts.append(decoded)
            if truncated and "text_part_limit_exceeded" not in warnings:
                warnings.append("text_part_limit_exceeded")
        elif content_type == "text/html":
            decoded, truncated = decode_part(
                part,
                max_bytes=limits.max_html_part_bytes if limits is not None else None,
            )
            html_parts.append(decoded)
            if truncated and "html_part_limit_exceeded" not in warnings:
                warnings.append("html_part_limit_exceeded")

    html_body = "\n".join(html_parts).strip() or None
    visible_html_parts: list[str] = []
    for html_part in html_parts:
        visible_text, sanitization_warnings = _visible_html_text(html_part)
        if visible_text:
            visible_html_parts.append(visible_text)
        for warning in sanitization_warnings:
            if warning not in warnings:
                warnings.append(warning)
    if plain_parts:
        plain = "\n".join(plain_parts).strip()
    elif html_body:
        plain = "\n".join(visible_html_parts).strip()
        warnings.append("html_only_body_converted_to_text")
    else:
        plain = ""
        warnings.append("no_supported_text_body")
    return plain, html_body, attachments, warnings
