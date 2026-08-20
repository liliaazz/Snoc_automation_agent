from __future__ import annotations

import uuid
from unittest.mock import Mock

from snoc_agent.domain.enums import ProcessingStatus
from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox
from snoc_agent.mail.interfaces import MailboxMessage
from snoc_agent.workflow.inbound_processor import ProcessingResult
from snoc_agent.workflow.orchestrator import MailOrchestrator


def mailbox_message(
    *,
    uid: int = 123,
) -> MailboxMessage:
    return MailboxMessage(
        mailbox="INBOX",
        uidvalidity=999,
        uid=uid,
        raw_message=b"From: test@example.com\r\n\r\nTest",
    )


def test_duplicate_is_marked_seen() -> None:
    message = mailbox_message(uid=123)
    mailbox = FakeIMAPMailbox([message])

    processor = Mock()
    processor.process_raw.return_value = ProcessingResult(
        email_message_id=uuid.uuid4(),
        status=ProcessingStatus.DUPLICATE.value,
    )

    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=processor,
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert len(results) == 1
    assert results[0].status == ProcessingStatus.DUPLICATE.value
    assert mailbox.seen_uids == {123}


def test_processed_is_marked_seen() -> None:
    message = mailbox_message(uid=456)
    mailbox = FakeIMAPMailbox([message])

    processor = Mock()
    processor.process_raw.return_value = ProcessingResult(
        email_message_id=uuid.uuid4(),
        status=ProcessingStatus.PROCESSED.value,
    )

    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=processor,
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert len(results) == 1
    assert mailbox.seen_uids == {456}


def test_processing_exception_is_not_marked_seen() -> None:
    message = mailbox_message(uid=789)
    mailbox = FakeIMAPMailbox([message])

    processor = Mock()
    processor.process_raw.side_effect = RuntimeError("temporary processing failure")

    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=processor,
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert results == []
    assert mailbox.seen_uids == set()


def test_failed_durable_result_is_marked_seen() -> None:
    message = mailbox_message(uid=900)
    mailbox = FakeIMAPMailbox([message])

    processor = Mock()
    processor.process_raw.return_value = ProcessingResult(
        email_message_id=uuid.uuid4(),
        status=ProcessingStatus.FAILED.value,
        detail="stored failure",
    )

    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=processor,
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert len(results) == 1
    assert results[0].status == ProcessingStatus.FAILED.value
    assert mailbox.seen_uids == {900}
