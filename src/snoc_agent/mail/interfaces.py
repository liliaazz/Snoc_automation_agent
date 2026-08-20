"""Transport protocols and immutable mailbox payloads."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True, slots=True)
class MailboxMessage:
    mailbox: str
    uidvalidity: int
    uid: int
    raw_message: bytes
    internal_date: datetime | None = None
    flags: tuple[str, ...] = ()
    provider_metadata: dict[str, object] = field(default_factory=dict)


class IMAPMailbox(Protocol):
    def fetch_candidates(self) -> list[MailboxMessage]:
        """Fetch candidate messages without marking them as seen."""
        ...

    def mark_seen(
        self,
        uid: int,
        *,
        uidvalidity: int | None = None,
    ) -> None:
        """Acknowledge a durably handled IMAP message."""
        ...


@dataclass(frozen=True, slots=True)
class OutboundEnvelope:
    sender: str
    recipients: tuple[str, ...]
    raw_message: bytes
    message_id: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SendResult:
    accepted: bool
    transient_failure: bool = False
    detail: str = ""


class SMTPTransport(Protocol):
    def send(self, envelope: OutboundEnvelope) -> SendResult: ...
