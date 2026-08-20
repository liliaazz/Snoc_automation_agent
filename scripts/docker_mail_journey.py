#!/usr/bin/env python3
"""Black-box email journey against the Docker worker and its PostgreSQL audit store."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from contextlib import suppress
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import make_msgid
from pathlib import Path
from typing import Any

from sqlalchemy import select

from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    Clarification,
    EmailMessage,
    Escalation,
    Execution,
    ModelRun,
    Operation,
    OutboxMessage,
    ValidationDecision,
    WorkflowEvent,
    WorkflowRun,
)
from snoc_agent.db.session import create_engine_and_session
from snoc_agent.domain.enums import RequestStatus
from snoc_agent.evaluation.journey_quality import journey_quality_failures
from snoc_agent.evaluation.mail_journeys import (
    MAIL_JOURNEY_SCENARIOS,
    MailJourneyScenario,
    build_journey_message,
)
from snoc_agent.mail.headers import normalize_message_id, reply_subject
from snoc_agent.mail.imap_client import RealIMAPMailbox
from snoc_agent.mail.interfaces import MailboxMessage, OutboundEnvelope
from snoc_agent.mail.parser import parse_email
from snoc_agent.mail.smtp_client import RealSMTPTransport


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _sender_credentials(settings: Settings) -> tuple[str, str]:
    explicit = os.environ.get("SENDER_USERNAME", "").strip()
    authorized = sorted(settings.authorized_sender_set)
    username = explicit or (authorized[0] if len(authorized) == 1 else "")
    password = os.environ.get("SENDER_PASSWORD", "")
    if not username or not password:
        raise ValueError(
            "SENDER_USERNAME/SENDER_PASSWORD are required; SENDER_USERNAME may be omitted "
            "when AUTHORIZED_SENDERS contains exactly one address"
        )
    if username.casefold() == settings.imap_username.casefold():
        raise ValueError("the external test sender must differ from the agent IMAP account")
    return username, password


def _send(
    transport: RealSMTPTransport,
    *,
    raw: bytes,
    sender: str,
    recipient: str,
    message_id: str,
) -> None:
    result = transport.send(
        OutboundEnvelope(
            sender=sender,
            recipients=(recipient,),
            raw_message=raw,
            message_id=message_id,
            metadata={"purpose": "docker_black_box_journey"},
        )
    )
    if not result.accepted:
        raise RuntimeError(f"personal SMTP delivery failed: {result.detail}")


def _header(raw_message: bytes, name: str) -> str | None:
    value = BytesParser(policy=policy.default).parsebytes(raw_message).get(name)
    return str(value).strip() if value else None


def _wait_for_personal_message(
    settings: Settings,
    *,
    username: str,
    password: str,
    timeout_seconds: float,
    message_id: str | None = None,
    in_reply_to: str | None = None,
    exclude_message_ids: tuple[str, ...] = (),
) -> MailboxMessage:
    if bool(message_id) == bool(in_reply_to):
        raise ValueError("select exactly one personal-message identity")
    deadline = time.monotonic() + timeout_seconds
    # Gmail can take time to index arbitrary RFC headers. Search the bounded
    # date range, then enforce the exact Message-ID link locally.
    since = datetime.now(UTC).strftime("%d-%b-%Y")
    criterion = f"SINCE {since}"
    normalized_target = normalize_message_id(message_id or in_reply_to)
    excluded = {
        normalized for value in exclude_message_ids if (normalized := normalize_message_id(value))
    }
    while time.monotonic() < deadline:
        try:
            candidates = RealIMAPMailbox(
                host=settings.imap_host,
                port=settings.imap_port,
                username=username,
                password=password,
                mailbox=settings.sender_imap_mailbox,
                use_ssl=settings.imap_ssl,
                search_criterion=criterion,
                timeout=90,
            ).fetch_candidates()
        except (OSError, RuntimeError, TimeoutError):
            time.sleep(3)
            continue
        for candidate in reversed(candidates):
            try:
                parsed = parse_email(candidate.raw_message)
            except (OSError, RuntimeError, ValueError):
                continue
            if parsed.normalized_message_id in excluded:
                continue
            candidate_value = parsed.normalized_message_id if message_id else parsed.in_reply_to
            if candidate_value == normalized_target:
                return candidate
        time.sleep(3)
    relation = "message" if message_id else "reply"
    raise TimeoutError(
        f"personal mailbox {relation} for {message_id or in_reply_to} was not observed"
    )


def _wait_for_personal_reply(
    settings: Settings,
    *,
    username: str,
    password: str,
    in_reply_to: str,
    timeout_seconds: float,
    exclude_message_ids: tuple[str, ...] = (),
) -> MailboxMessage:
    return _wait_for_personal_message(
        settings,
        username=username,
        password=password,
        in_reply_to=in_reply_to,
        timeout_seconds=timeout_seconds,
        exclude_message_ids=exclude_message_ids,
    )


def _wait_for_stored_email(
    session_factory: Any,
    message_id: str,
    timeout_seconds: float,
) -> EmailMessage:
    normalized = normalize_message_id(message_id)
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_factory() as session:
            email = session.scalar(
                select(EmailMessage).where(EmailMessage.normalized_message_id == normalized)
            )
            if email is not None and email.processing_status not in {"stored", "processing"}:
                session.expunge(email)
                return email
        time.sleep(2)
    raise TimeoutError(f"Docker worker did not finish processing {message_id}")


def _wait_for_terminal_request(
    session_factory: Any,
    *,
    initiating_email_id: Any,
    timeout_seconds: float,
) -> None:
    terminal = {
        RequestStatus.COMPLETED.value,
        RequestStatus.ESCALATED.value,
        RequestStatus.FAILED.value,
        RequestStatus.CANCELLED.value,
    }
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with session_factory() as session:
            request = session.scalar(
                select(BusinessRequest).where(
                    BusinessRequest.initiating_email_id == initiating_email_id
                )
            )
            if request is not None and request.status in terminal:
                return
        time.sleep(2)
    raise TimeoutError("Docker worker did not finish the scheduled request")


def _audit(session_factory: Any, inbound_ids: list[Any]) -> dict[str, Any]:
    with session_factory() as session:
        emails = [session.get_one(EmailMessage, email_id) for email_id in inbound_ids]
        conversation_ids = {email.conversation_id for email in emails if email.conversation_id}
        requests = (
            session.scalars(
                select(BusinessRequest).where(BusinessRequest.conversation_id.in_(conversation_ids))
            ).all()
            if conversation_ids
            else []
        )
        request_ids = [row.id for row in requests]
        operations = (
            session.scalars(select(Operation).where(Operation.request_id.in_(request_ids))).all()
            if request_ids
            else []
        )
        operation_ids = [row.id for row in operations]
        runs = session.scalars(
            select(ModelRun).where(ModelRun.email_message_id.in_(inbound_ids))
        ).all()
        decisions = (
            session.scalars(
                select(ValidationDecision).where(ValidationDecision.operation_id.in_(operation_ids))
            ).all()
            if operation_ids
            else []
        )
        clarifications = (
            session.scalars(
                select(Clarification).where(Clarification.request_id.in_(request_ids))
            ).all()
            if request_ids
            else []
        )
        executions = (
            session.scalars(
                select(Execution).where(Execution.operation_id.in_(operation_ids))
            ).all()
            if operation_ids
            else []
        )
        escalations = session.scalars(
            select(Escalation).where(Escalation.email_message_id.in_(inbound_ids))
        ).all()
        outbox = (
            session.scalars(
                select(OutboxMessage).where(OutboxMessage.related_request_id.in_(request_ids))
            ).all()
            if request_ids
            else []
        )
        workflow_runs = session.scalars(
            select(WorkflowRun)
            .where(WorkflowRun.inbound_email_id.in_(inbound_ids))
            .order_by(WorkflowRun.started_at)
        ).all()
        workflow_run_ids = [row.id for row in workflow_runs]
        workflow_events = (
            session.scalars(
                select(WorkflowEvent)
                .where(WorkflowEvent.workflow_run_id.in_(workflow_run_ids))
                .order_by(WorkflowEvent.workflow_run_id, WorkflowEvent.sequence)
            ).all()
            if workflow_run_ids
            else []
        )
        return {
            "emails": [
                {
                    "id": str(row.id),
                    "message_id": row.rfc_message_id,
                    "subject": row.subject,
                    "status": row.processing_status,
                    "imap": {
                        "uid": row.imap_uid,
                        "uidvalidity": row.uidvalidity,
                        "flags": row.flags_json,
                        "internal_date": row.internal_date,
                        "provider_metadata": row.provider_metadata_json,
                    },
                    "classification": row.automated_classification,
                    "authorized": row.authorization_allowed,
                    "correlation": row.correlation_details,
                }
                for row in emails
            ],
            "requests": [
                {"reference": row.public_reference, "status": row.status} for row in requests
            ],
            "operations": [
                {
                    "id": str(row.id),
                    "action": row.action,
                    "pdv_code": row.pdv_code,
                    "phone": row.phone,
                    "status": row.status,
                    "decision": row.final_decision,
                }
                for row in operations
            ],
            "model_runs": [
                {
                    "id": str(row.id),
                    "stage": row.stage,
                    "model": row.base_model_id,
                    "route": row.resolved_model_id,
                    "mode": row.structured_output_mode,
                    "valid": row.structured_output_valid,
                    "error": row.error_category,
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "cost_usd": row.total_cost_usd,
                }
                for row in runs
            ],
            "decisions": [{"decision": row.decision, "reasons": row.reasons} for row in decisions],
            "clarifications": [
                {
                    "status": row.status,
                    "requested_fields": row.requested_fields,
                    "reply_email_id": row.reply_email_id,
                }
                for row in clarifications
            ],
            "executions": [
                {
                    "action_endpoint": row.endpoint,
                    "status": row.status,
                    "dry_run": row.dry_run,
                }
                for row in executions
            ],
            "escalations": [
                {"reason": row.reason_code, "summary": row.summary} for row in escalations
            ],
            "outbox": [
                {
                    "subject": row.subject,
                    "status": row.status,
                    "sent_at": row.sent_at,
                    "headers": row.headers,
                }
                for row in outbox
            ],
            "workflow_runs": [
                {
                    "id": str(row.id),
                    "inbound_email_id": str(row.inbound_email_id),
                    "graph_version": row.graph_version,
                    "engine": row.engine,
                    "status": row.status,
                    "current_agent": row.current_agent,
                    "error_category": row.error_category,
                }
                for row in workflow_runs
            ],
            "workflow_events": [
                {
                    "workflow_run_id": str(row.workflow_run_id),
                    "sequence": row.sequence,
                    "agent": row.agent,
                    "status": row.status,
                    "input_summary": row.input_summary,
                    "output_summary": row.output_summary,
                    "error_category": row.error_category,
                }
                for row in workflow_events
            ],
        }


def _quality_checks(
    scenario: MailJourneyScenario,
    audit: dict[str, Any],
    replies: list[dict[str, Any]],
) -> list[str]:
    return journey_quality_failures(scenario.name, audit, replies)


def run(args: argparse.Namespace) -> dict[str, Any]:
    settings = Settings()
    sender, sender_password = _sender_credentials(settings)
    _engine, session_factory = create_engine_and_session(settings.database_url)
    sender_transport = RealSMTPTransport(
        host=settings.smtp_host,
        port=settings.smtp_port,
        username=sender,
        password=sender_password,
        use_ssl=settings.smtp_ssl,
        starttls=settings.smtp_starttls,
        timeout=30,
    )
    run_id = os.environ.get("SNOC_TEST_RUN_ID", "").strip() or datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    reports: list[dict[str, Any]] = []
    requested_scenarios = set(getattr(args, "scenario", ()) or ())
    scenarios = tuple(
        scenario
        for scenario in MAIL_JOURNEY_SCENARIOS
        if not requested_scenarios or scenario.name in requested_scenarios
    )
    if requested_scenarios and len(scenarios) != len(requested_scenarios):
        known = {scenario.name for scenario in MAIL_JOURNEY_SCENARIOS}
        unknown = ", ".join(sorted(requested_scenarios - known))
        raise ValueError(f"unknown journey scenario(s): {unknown}")

    for scenario in scenarios:
        subject_nonce = hashlib.sha256(f"{run_id}:{scenario.name}".encode()).hexdigest()[:6]
        # Production deduplication intentionally collapses same-sender emails
        # with identical normalized subjects and visible bodies. Give each
        # staged journey a harmless short ticket suffix so the harness remains
        # repeatable without weakening that protection.
        subject = f"{scenario.subject} — réf. {subject_nonce}"
        initial_id = make_msgid(domain=sender.partition("@")[2])
        _send(
            sender_transport,
            raw=build_journey_message(
                sender=sender,
                recipient=settings.imap_username,
                subject=subject,
                body=scenario.body,
                message_id=initial_id,
                test_run_id=run_id,
                test_case=scenario.name,
                automated=scenario.automated,
            ),
            sender=sender,
            recipient=settings.imap_username,
            message_id=initial_id,
        )
        first = _wait_for_stored_email(session_factory, initial_id, args.timeout_seconds)
        inbound_ids = [first.id]
        replies: list[dict[str, Any]] = []
        personal_chain: list[MailboxMessage] = []

        with suppress(TimeoutError):
            personal_chain.append(
                _wait_for_personal_message(
                    settings,
                    username=sender,
                    password=sender_password,
                    message_id=initial_id,
                    timeout_seconds=args.timeout_seconds,
                )
            )

        if not scenario.automated:
            try:
                agent_mail = _wait_for_personal_reply(
                    settings,
                    username=sender,
                    password=sender_password,
                    in_reply_to=initial_id,
                    timeout_seconds=args.timeout_seconds,
                )
                personal_chain.append(agent_mail)
                parsed_agent = parse_email(agent_mail.raw_message)
                replies.append(
                    {
                        "message_id": parsed_agent.rfc_message_id,
                        "in_reply_to": parsed_agent.in_reply_to,
                        "subject": parsed_agent.subject,
                        "body": parsed_agent.text_body,
                        "provider_metadata": agent_mail.provider_metadata,
                    }
                )
                if (
                    scenario.reply_body
                    and _header(agent_mail.raw_message, "X-SNOC-Reply-Type") == "clarification"
                ):
                    reply_id = make_msgid(domain=sender.partition("@")[2])
                    agent_id = parsed_agent.rfc_message_id or ""
                    quoted_reply = (
                        f"{scenario.reply_body}\n\n"
                        f"Le {datetime.now(UTC).strftime('%d/%m/%Y')}, Agent SNOC a écrit :\n"
                        + "\n".join(f"> {line}" for line in parsed_agent.text_body.splitlines())
                    )
                    _send(
                        sender_transport,
                        raw=build_journey_message(
                            sender=sender,
                            recipient=settings.imap_username,
                            subject=reply_subject(subject),
                            body=quoted_reply,
                            message_id=reply_id,
                            test_run_id=run_id,
                            test_case=scenario.name,
                            in_reply_to=agent_id,
                            references=(initial_id, agent_id),
                        ),
                        sender=sender,
                        recipient=settings.imap_username,
                        message_id=reply_id,
                    )
                    second = _wait_for_stored_email(session_factory, reply_id, args.timeout_seconds)
                    inbound_ids.append(second.id)
                    personal_chain.append(
                        _wait_for_personal_message(
                            settings,
                            username=sender,
                            password=sender_password,
                            message_id=reply_id,
                            timeout_seconds=args.timeout_seconds,
                        )
                    )
                    terminal_mail = _wait_for_personal_reply(
                        settings,
                        username=sender,
                        password=sender_password,
                        in_reply_to=reply_id,
                        timeout_seconds=args.timeout_seconds,
                    )
                    personal_chain.append(terminal_mail)
                    parsed_terminal = parse_email(terminal_mail.raw_message)
                    replies.append(
                        {
                            "message_id": parsed_terminal.rfc_message_id,
                            "in_reply_to": parsed_terminal.in_reply_to,
                            "subject": parsed_terminal.subject,
                            "body": parsed_terminal.text_body,
                            "provider_metadata": terminal_mail.provider_metadata,
                        }
                    )
                    if _header(terminal_mail.raw_message, "X-SNOC-Pending-Execution"):
                        _wait_for_terminal_request(
                            session_factory,
                            initiating_email_id=first.id,
                            timeout_seconds=args.timeout_seconds,
                        )
                        completion_mail = _wait_for_personal_reply(
                            settings,
                            username=sender,
                            password=sender_password,
                            in_reply_to=reply_id,
                            timeout_seconds=args.timeout_seconds,
                            exclude_message_ids=(parsed_terminal.rfc_message_id or "",),
                        )
                        personal_chain.append(completion_mail)
                        parsed_completion = parse_email(completion_mail.raw_message)
                        replies.append(
                            {
                                "message_id": parsed_completion.rfc_message_id,
                                "in_reply_to": parsed_completion.in_reply_to,
                                "subject": parsed_completion.subject,
                                "body": parsed_completion.text_body,
                                "provider_metadata": completion_mail.provider_metadata,
                            }
                        )
                elif _header(agent_mail.raw_message, "X-SNOC-Pending-Execution"):
                    _wait_for_terminal_request(
                        session_factory,
                        initiating_email_id=first.id,
                        timeout_seconds=args.timeout_seconds,
                    )
                    terminal_mail = _wait_for_personal_reply(
                        settings,
                        username=sender,
                        password=sender_password,
                        in_reply_to=initial_id,
                        timeout_seconds=args.timeout_seconds,
                        exclude_message_ids=(parsed_agent.rfc_message_id or "",),
                    )
                    personal_chain.append(terminal_mail)
                    parsed_terminal = parse_email(terminal_mail.raw_message)
                    replies.append(
                        {
                            "message_id": parsed_terminal.rfc_message_id,
                            "in_reply_to": parsed_terminal.in_reply_to,
                            "subject": parsed_terminal.subject,
                            "body": parsed_terminal.text_body,
                            "provider_metadata": terminal_mail.provider_metadata,
                        }
                    )
            except TimeoutError as exc:
                replies.append({"timeout": str(exc)})

        audit = _audit(session_factory, inbound_ids)
        failures = _quality_checks(scenario, audit, replies)
        gmail_thread_ids = [
            str(metadata["gmail_thread_id"])
            for message in personal_chain
            if (metadata := message.provider_metadata).get("gmail_thread_id")
        ]
        threading_ok = (
            len(personal_chain) >= (1 if scenario.automated else 2)
            and bool(gmail_thread_ids)
            and len(gmail_thread_ids) == len(personal_chain)
            and len(set(gmail_thread_ids)) == 1
        )
        if not threading_ok:
            failures.append("Gmail did not expose one shared X-GM-THRID for the full message chain")
        request_statuses = [str(row["status"]).upper() for row in audit["requests"]]
        terminal_state = (
            "IGNORED"
            if scenario.automated and audit["emails"][0]["status"] == "ignored"
            else request_statuses[-1]
            if request_statuses
            else audit["emails"][-1]["status"].upper()
        )
        if not scenario.automated and terminal_state not in {"COMPLETED", "ESCALATED"}:
            failures.append(f"conversation did not reach a terminal state: {terminal_state}")
        report = {
            "scenario": scenario.name,
            "subject": subject,
            "expected": scenario.expected,
            "passed": not failures,
            "quality_failures": failures,
            "agent_replies": replies,
            "terminal_state": terminal_state,
            "gmail_thread_id": gmail_thread_ids[0] if threading_ok else None,
            "gmail_thread_ids": gmail_thread_ids,
            "threading_ok": threading_ok,
            "audit": audit,
        }
        reports.append(report)
        print(json.dumps(report, ensure_ascii=False, default=_json_default), flush=True)

    all_runs = [run for report in reports for run in report["audit"]["model_runs"]]
    summary = {
        "run_id": run_id,
        "transport": "personal SMTP/IMAP -> Docker worker -> personal SMTP/IMAP",
        "agent_database": settings.database_url,
        "dry_run_business_api": settings.dry_run,
        "scenarios": reports,
        "totals": {
            "scenarios": len(reports),
            "passed": sum(1 for report in reports if report["passed"]),
            "failed": sum(1 for report in reports if not report["passed"]),
            "threading_failures": sum(1 for report in reports if not report["threading_ok"]),
            "model_runs": len(all_runs),
            "prompt_tokens": sum(run["prompt_tokens"] or 0 for run in all_runs),
            "completion_tokens": sum(run["completion_tokens"] or 0 for run in all_runs),
            "known_cost_usd": str(
                sum(run["cost_usd"] for run in all_runs if run["cost_usd"] is not None)
            ),
        },
    }
    output: Path = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    print(json.dumps(summary["totals"], indent=2), flush=True)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-send", action="store_true")
    parser.add_argument("--timeout-seconds", type=float, default=300)
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[scenario.name for scenario in MAIL_JOURNEY_SCENARIOS],
        help="run only the named scenario; repeat to select a small staged set",
    )
    parser.add_argument(
        "--output", type=Path, default=Path("outputs/docker_mail_journey/report.json")
    )
    args = parser.parse_args()
    if not args.confirm_send:
        parser.error("--confirm-send is required because this sends real personal emails")
    run(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
