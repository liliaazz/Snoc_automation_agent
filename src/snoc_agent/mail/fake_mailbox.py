"""In-memory transports for deterministic tests and replay."""

from __future__ import annotations

from collections import deque

from snoc_agent.mail.interfaces import (
    MailboxMessage,
    OutboundEnvelope,
    SendResult,
)


class FakeIMAPMailbox:
    def __init__(
        self,
        messages: list[MailboxMessage] | None = None,
    ) -> None:
        self._messages = deque(messages or [])
        self.seen_uids: set[int] = set()

    def add(self, message: MailboxMessage) -> None:
        self._messages.append(message)

    def fetch_candidates(self) -> list[MailboxMessage]:
        # Keep deterministic rediscovery behavior for existing tests.
        # Tests can inspect seen_uids to verify acknowledgement.
        return list(self._messages)

    def mark_seen(
        self,
        uid: int,
        *,
        uidvalidity: int | None = None,
    ) -> None:
        del uidvalidity
        self.seen_uids.add(uid)


class FakeSMTPTransport:
    def __init__(
        self,
        failures_before_success: int = 0,
    ) -> None:
        self.failures_before_success = failures_before_success
        self.attempts = 0
        self.sent: list[OutboundEnvelope] = []

    def send(
        self,
        envelope: OutboundEnvelope,
    ) -> SendResult:
        self.attempts += 1

        if self.attempts <= self.failures_before_success:
            return SendResult(
                False,
                transient_failure=True,
                detail="injected transient failure",
            )

        self.sent.append(envelope)

        return SendResult(
            True,
            detail="accepted by fake SMTP",
        )
