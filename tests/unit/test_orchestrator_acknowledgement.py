from __future__ import annotations

import uuid

import pytest

from snoc_agent.domain.enums import ProcessingStatus
from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox
from snoc_agent.mail.interfaces import MailboxMessage
from snoc_agent.workflow.inbound_processor import ProcessingResult
from snoc_agent.workflow.orchestrator import MailOrchestrator


class _ResultProcessor:
    def __init__(self, status: str) -> None:
        self.status = status

    def process_raw(self, *_args: object, **_kwargs: object) -> ProcessingResult:
        return ProcessingResult(uuid.uuid4(), self.status)


class _FailingProcessor:
    def process_raw(self, *_args: object, **_kwargs: object) -> ProcessingResult:
        raise RuntimeError("temporary processing failure")


def _message(uid: int = 123) -> MailboxMessage:
    return MailboxMessage(
        mailbox="INBOX",
        uidvalidity=42,
        uid=uid,
        raw_message=b"Subject: test\r\n\r\nmessage",
    )


@pytest.mark.parametrize(
    "status",
    [
        ProcessingStatus.PROCESSED.value,
        ProcessingStatus.DUPLICATE.value,
        ProcessingStatus.IGNORED.value,
        ProcessingStatus.QUARANTINED.value,
    ],
)
def test_safe_terminal_result_is_marked_seen(status: str) -> None:
    mailbox = FakeIMAPMailbox([_message()])
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=_ResultProcessor(status),  # type: ignore[arg-type]
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert [result.status for result in results] == [status]
    assert mailbox.seen_uids == {123}


@pytest.mark.parametrize(
    "status",
    [
        ProcessingStatus.STORED.value,
        ProcessingStatus.PROCESSING.value,
    ],
)
def test_non_terminal_result_is_not_marked_seen(status: str) -> None:
    mailbox = FakeIMAPMailbox([_message(456)])
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=_ResultProcessor(status),  # type: ignore[arg-type]
        mail_account_id=uuid.uuid4(),
    )

    orchestrator.poll_once()

    assert mailbox.seen_uids == set()


def test_processing_exception_is_not_marked_seen() -> None:
    mailbox = FakeIMAPMailbox([_message(789)])
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=_FailingProcessor(),  # type: ignore[arg-type]
        mail_account_id=uuid.uuid4(),
    )

    assert orchestrator.poll_once() == []
    assert mailbox.seen_uids == set()


def test_acknowledgement_failure_keeps_durable_result() -> None:
    class _FailingAcknowledgementMailbox(FakeIMAPMailbox):
        def mark_seen(
            self,
            uid: int,
            *,
            uidvalidity: int | None = None,
        ) -> None:
            del uidvalidity
            raise RuntimeError(f"cannot acknowledge {uid}")

    mailbox = _FailingAcknowledgementMailbox([_message(999)])
    orchestrator = MailOrchestrator(
        mailbox=mailbox,
        processor=_ResultProcessor(ProcessingStatus.DUPLICATE.value),  # type: ignore[arg-type]
        mail_account_id=uuid.uuid4(),
    )

    results = orchestrator.poll_once()

    assert [result.status for result in results] == [ProcessingStatus.DUPLICATE.value]
