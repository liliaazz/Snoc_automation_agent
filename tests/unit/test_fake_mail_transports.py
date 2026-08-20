from __future__ import annotations

from datetime import UTC, datetime

from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox, FakeSMTPTransport
from snoc_agent.mail.interfaces import MailboxMessage, OutboundEnvelope


def test_fake_imap_rediscovery_is_non_destructive_and_add_preserves_order() -> None:
    first = MailboxMessage(
        mailbox="INBOX",
        uidvalidity=42,
        uid=100,
        raw_message=b"first",
        internal_date=datetime(2026, 7, 18, tzinfo=UTC),
        flags=("\\Seen",),
    )
    second = MailboxMessage(
        mailbox="INBOX",
        uidvalidity=42,
        uid=101,
        raw_message=b"second",
    )
    mailbox = FakeIMAPMailbox([first])
    mailbox.add(second)
    mailbox.mark_seen(100)

    assert mailbox.fetch_candidates() == [first, second]
    assert mailbox.seen_uids == {100}
    assert mailbox.fetch_candidates() == [first, second]


def test_fake_smtp_injects_transient_failures_then_records_one_success() -> None:
    transport = FakeSMTPTransport(failures_before_success=2)
    envelope = OutboundEnvelope(
        sender="snoc@example.com",
        recipients=("manager@example.com",),
        raw_message=b"Subject: Result\r\n\r\nDone",
        message_id="<outbound@example.com>",
        metadata={"request_id": "request-1"},
    )

    first = transport.send(envelope)
    second = transport.send(envelope)
    third = transport.send(envelope)

    assert first.accepted is False and first.transient_failure is True
    assert second.accepted is False and second.transient_failure is True
    assert third.accepted is True and third.transient_failure is False
    assert transport.attempts == 3
    assert transport.sent == [envelope]
