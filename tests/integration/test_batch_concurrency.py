from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage as RFCEmailMessage

from sqlalchemy import func, select

from snoc_agent.cli.runtime import build_runtime
from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    Conversation,
    EmailMessage,
    Execution,
    Operation,
    OutboxMessage,
)
from snoc_agent.db.session import session_scope
from snoc_agent.domain.enums import ProcessingStatus


def _automatic_notification(index: int) -> bytes:
    message = RFCEmailMessage()
    message["Message-ID"] = f"<batch-{index}@example.test>"
    message["From"] = "notifications@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = f"Automated status {index}"
    message["Auto-Submitted"] = "auto-generated"
    message.set_content(f"Automated status notification {index}; no action is requested.")
    return message.as_bytes()


def test_50_sequential_and_20_concurrent_emails_are_transactionally_isolated(tmp_path) -> None:
    settings = Settings(
        llm_provider="demo",
        workflow_engine="legacy",
        database_url=f"sqlite:///{tmp_path / 'batch.db'}",
        raw_eml_directory=tmp_path / "raw",
        dry_run=True,
    )
    runtime = build_runtime(settings, initialize_schema=True)

    sequential = [
        runtime.processor.process_raw(_automatic_notification(index)) for index in range(50)
    ]
    with ThreadPoolExecutor(max_workers=10) as executor:
        concurrent = list(
            executor.map(
                lambda index: runtime.processor.process_raw(_automatic_notification(index)),
                range(50, 70),
            )
        )

    results = sequential + concurrent
    assert len(results) == 70
    assert len({result.email_message_id for result in results}) == 70
    assert all(result.status == ProcessingStatus.IGNORED.value for result in results)

    with session_scope(runtime.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 70
        assert (
            session.scalar(
                select(func.count(func.distinct(EmailMessage.normalized_message_id))).select_from(
                    EmailMessage
                )
            )
            == 70
        )
        assert session.scalar(select(func.count()).select_from(Conversation)) == 0
        assert session.scalar(select(func.count()).select_from(BusinessRequest)) == 0
        assert session.scalar(select(func.count()).select_from(Operation)) == 0
        assert session.scalar(select(func.count()).select_from(Execution)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxMessage)) == 0
