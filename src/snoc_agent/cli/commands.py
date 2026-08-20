"""Implementation of CLI commands; parsing stays in ``main.py``."""

from __future__ import annotations

import imaplib
import json
import logging
import re
import shlex
import time
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from alembic.config import Config as AlembicConfig
from pydantic import BaseModel, ConfigDict
from sqlalchemy import inspect, select, text
from sqlalchemy.engine import make_url

from alembic import command
from snoc_agent.ai.backend import (
    ChatMessage,
    StructuredGenerationResult,
    safe_generation_settings,
)
from snoc_agent.ai.model_registry import VLLM_MODEL_PAIRS, ModelPair
from snoc_agent.ai.provider import LLMProvider
from snoc_agent.ai.vllm_deployments import VLLMModelCatalog
from snoc_agent.cli.runtime import build_model_services, build_runtime
from snoc_agent.config import Settings
from snoc_agent.db import create_engine_and_session, create_schema
from snoc_agent.db.models import (
    BusinessRequest,
    Clarification,
    Conversation,
    EmailMessage,
    Escalation,
    Execution,
    MailAccount,
    ModelRun,
    Operation,
    OutboxMessage,
    ValidationDecision,
)
from snoc_agent.db.repositories import EmailRepository
from snoc_agent.db.session import session_scope
from snoc_agent.evaluation.calibration import CalibrationMethod, fit_calibration
from snoc_agent.evaluation.dataset_loader import load_dataset
from snoc_agent.evaluation.dataset_subsets import build_evaluation_subsets
from snoc_agent.evaluation.inference_cache import CacheMode
from snoc_agent.evaluation.matrix_runner import run_persistent_matrix
from snoc_agent.evaluation.vllm_smoke import run_vllm_smoke_test
from snoc_agent.mail.imap_client import RealIMAPMailbox
from snoc_agent.mail.parser import parse_email
from snoc_agent.workflow.inbound_processor import InboundIdentity, ProcessingResult
from snoc_agent.workflow.model_audit import persist_model_run
from snoc_agent.workflow.orchestrator import MailOrchestrator

LOGGER = logging.getLogger(__name__)

IMAP_WORKER_LOCK_KEY = 1_397_639_747

SYMBOLIC_CLARIFICATION_RE = re.compile(
    r"(?im)^(In-Reply-To:\s*)<clarification-[^>]+@snoc-agent\.invalid>\s*$"
)
REQUEST_REFERENCE_RE = re.compile(r"SNOC-REQ-[A-Z0-9]{12}")
SCENARIO_TOKEN_RE = re.compile(r"\{\{([a-zA-Z0-9_-]+)\.([a-zA-Z0-9_]+)\}\}")


class _ProviderCheckSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool


class _ChatProbeAudit(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    assistant_content: str


def _json_default(value: object) -> str:
    if isinstance(value, (uuid.UUID, Path)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=_json_default))


def result_dict(result: ProcessingResult) -> dict[str, Any]:
    return {
        "email_message_id": result.email_message_id,
        "status": result.status,
        "conversation_id": result.conversation_id,
        "request_ids": result.request_ids,
        "operation_ids": result.operation_ids,
        "decisions": result.decisions,
        "duplicate_of_id": result.duplicate_of_id,
        "detail": result.detail,
    }


def db_init(settings: Settings) -> None:
    config = AlembicConfig("alembic.ini")
    config.attributes["runtime_database_url"] = settings.database_url
    engine, _ = create_engine_and_session(settings.database_url)
    try:
        with engine.begin() as connection:
            table_names = set(inspect(connection).get_table_names())
            legacy_tables = {
                "email_messages",
                "model_runs",
                "requests",
                "operations",
                "inference_cache_entries",
                "evaluation_runs",
                "evaluation_inferences",
                "calibration_artifacts",
            }
            is_unversioned_legacy_schema = (
                "alembic_version" not in table_names and legacy_tables <= table_names
            )
            if is_unversioned_legacy_schema:
                model_run_columns = {
                    column["name"] for column in inspect(connection).get_columns("model_runs")
                }
                # Older development databases were built through create_all() rather
                # than Alembic. These two additive fields arrived after that snapshot.
                # Reconcile them before stamping the existing inference-audit tables at head.
                if "request_attempt_count" not in model_run_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE model_runs ADD COLUMN request_attempt_count "
                            "INTEGER NOT NULL DEFAULT 0"
                        )
                    )
                if "logprob_metrics" not in model_run_columns:
                    connection.execute(
                        text(
                            "ALTER TABLE model_runs ADD COLUMN logprob_metrics "
                            "JSON NOT NULL DEFAULT '{}'"
                        )
                    )
        if is_unversioned_legacy_schema:
            command.stamp(config, "head")
        else:
            command.upgrade(config, "head")
    finally:
        engine.dispose()
    safe_database_url = make_url(settings.database_url).render_as_string(hide_password=True)
    print_json({"database_url": safe_database_url, "migration": "head", "status": "ready"})


def _vllm_catalog(settings: Settings) -> VLLMModelCatalog:
    if not settings.effective_vllm_api_key:
        raise ValueError("VLLM_API_KEY is missing")
    return VLLMModelCatalog(
        deployments=settings.vllm_deployments,
        api_key=settings.effective_vllm_api_key,
        timeout_seconds=settings.vllm_request_timeout_seconds,
    )


def _vllm_models_list(settings: Settings, *, json_output: bool) -> None:
    catalog = _vllm_catalog(settings)
    try:
        payload = {
            "provider": LLMProvider.VLLM.value,
            "deployments": catalog.list_models(),
        }
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, default=_json_default))
        else:
            print_json(payload)
    finally:
        catalog.close()


def _vllm_models_check(settings: Settings) -> None:
    catalog = _vllm_catalog(settings)
    analyzer_backend = None
    verifier_backend = None
    engine = None
    try:
        availability = catalog.check_exact_models()
        analyzer_backend, verifier_backend, analyzer, verifier = build_model_services(settings)
        chat_probe = getattr(analyzer_backend, "probe_chat", None)
        if not callable(chat_probe):
            raise RuntimeError("configured vLLM analyzer cannot perform a chat probe")
        chat_result = chat_probe(
            model=analyzer.config.model,
            base_model=analyzer.config.base_model,
            max_retries=settings.vllm_max_retries,
            retry_base_seconds=settings.vllm_retry_base_seconds,
        )
        structured_result = verifier_backend.generate_structured(
            messages=[
                ChatMessage(
                    role="system",
                    content="Return only the requested JSON object, without explanations.",
                ),
                ChatMessage(role="user", content='Return {"ok": true}.'),
            ],
            response_model=_ProviderCheckSchema,
            config=verifier.config,
        )
        engine, session_factory = create_engine_and_session(settings.database_url)
        create_schema(engine)
        chat_audit = StructuredGenerationResult(
            parsed=_ChatProbeAudit(assistant_content=str(chat_result["assistant_content"])),
            raw_output=str(chat_result["assistant_content"]),
            model_name=str(chat_result["model"]),
            backend=LLMProvider.VLLM.value,
            latency_seconds=float(chat_result["latency_seconds"]),
            prompt_tokens=chat_result.get("prompt_tokens"),
            completion_tokens=chat_result.get("completion_tokens"),
            total_tokens=chat_result.get("total_tokens"),
            attempts=int(chat_result["attempts"]),
            base_model_id=analyzer.config.base_model,
            resolved_model_id=analyzer.config.model,
            requested_route=analyzer.config.model,
            reported_provider=chat_result.get("reported_provider"),
            provider_request_id=chat_result.get("provider_request_id"),
            structured_output_mode="unstructured_probe",
            json_schema=_ChatProbeAudit.model_json_schema(),
            schema_name=_ChatProbeAudit.__name__,
            reasoning_output=chat_result.get("reasoning_output"),
            cost_basis="unknown",
        )
        with session_scope(session_factory) as session:
            persist_model_run(
                session,
                result=chat_audit,
                stage="model_check_chat",
                prompt_version="provider_check_v1",
                input_context={"probe": "minimal_chat", "synthetic": True},
                email_message_id=None,
                generation_settings=safe_generation_settings(analyzer.config),
            )
            persist_model_run(
                session,
                result=structured_result,
                stage="model_check_structured",
                prompt_version="provider_check_v1",
                input_context={"probe": "structured_output", "synthetic": True},
                email_message_id=None,
                generation_settings=safe_generation_settings(verifier.config),
            )
        print_json(
            {
                "status": "compatible",
                "provider": LLMProvider.VLLM.value,
                "authentication": "passed",
                "availability": availability,
                "chat_probe": chat_result,
                "structured_probe": {
                    "base_model_id": structured_result.base_model_id,
                    "resolved_model_id": structured_result.resolved_model_id,
                    "reported_provider": structured_result.reported_provider,
                    "structured_output_mode": structured_result.structured_output_mode,
                    "schema_guaranteed": (
                        structured_result.structured_output_mode == "json_schema"
                    ),
                    "fallback_reason": structured_result.fallback_reason,
                    "prompt_tokens": structured_result.prompt_tokens,
                    "completion_tokens": structured_result.completion_tokens,
                    "total_tokens": structured_result.total_tokens,
                    "cost_usd": None,
                    "cost_basis": "unknown",
                },
            }
        )
    finally:
        for backend in (analyzer_backend, verifier_backend):
            if backend is not None:
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
        if engine is not None:
            engine.dispose()
        catalog.close()


def models_list(settings: Settings, *, show_all: bool, refresh: bool, json_output: bool) -> None:
    if settings.effective_llm_provider != LLMProvider.VLLM:
        raise ValueError("models list requires LLM_PROVIDER=vllm")
    _vllm_models_list(settings, json_output=json_output)


def models_check(settings: Settings, *, refresh: bool) -> None:
    if settings.effective_llm_provider != LLMProvider.VLLM:
        raise ValueError("models check requires LLM_PROVIDER=vllm")
    _vllm_models_check(settings)


def evaluation_datasets_build(settings: Settings, *, source: Path, output_dir: Path) -> None:
    print_json(build_evaluation_subsets(settings, source=source, output_dir=output_dir))


def models_smoke_test(
    settings: Settings,
    *,
    analyzer_model: str | None,
    verifier_model: str | None,
    output_dir: Path,
) -> None:
    if settings.effective_llm_provider != LLMProvider.VLLM:
        raise ValueError("models smoke-test requires LLM_PROVIDER=vllm")
    runner = run_vllm_smoke_test
    analyzer_model = analyzer_model or settings.vllm_analyzer_deployment.value
    verifier_model = verifier_model or settings.vllm_verifier_deployment.value
    print_json(
        runner(
            settings,
            analyzer_model=analyzer_model,
            verifier_model=verifier_model,
            output_dir=output_dir,
        )
    )


def evaluation_calibrate(
    settings: Settings,
    *,
    predictions: Path,
    method: CalibrationMethod,
    split_manifest: Path | None,
    output: Path,
) -> None:
    print_json(
        fit_calibration(
            settings,
            predictions_path=predictions,
            method=method,
            split_manifest_path=split_manifest,
            output_path=output,
        )
    )


def _mail_account(runtime: Any, settings: Settings) -> MailAccount:
    name = f"{settings.imap_username}@{settings.imap_host}:{settings.imap_mailbox}"
    with session_scope(runtime.session_factory) as session:
        account = session.scalar(select(MailAccount).where(MailAccount.name == name))
        if account is None:
            account = MailAccount(name=name, mailbox=settings.imap_mailbox, enabled=True)
            session.add(account)
            session.flush()
        return account


def _real_orchestrator(settings: Settings) -> tuple[Any, MailOrchestrator]:
    if not settings.imap_host or not settings.imap_username:
        raise ValueError("IMAP_HOST and IMAP_USERNAME are required for mail polling")
    runtime = build_runtime(settings)
    account = _mail_account(runtime, settings)
    mailbox = RealIMAPMailbox(
        host=settings.imap_host,
        port=settings.imap_port,
        username=settings.imap_username,
        password=settings.imap_password.get_secret_value(),
        mailbox=settings.imap_mailbox,
        use_ssl=settings.imap_ssl,
        search_criterion=settings.imap_search_criterion,
    )
    return runtime, MailOrchestrator(
        mailbox=mailbox, processor=runtime.processor, mail_account_id=account.id
    )


@contextmanager
def _imap_worker_lease(engine: Any):
    """Hold the singleton IMAP polling lease for the life of a worker."""

    if engine.dialect.name != "postgresql":
        yield lambda: None
        return

    connection = engine.connect()
    try:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:lock_id)"),
                {"lock_id": IMAP_WORKER_LOCK_KEY},
            ).scalar_one()
        )
        connection.commit()
        if not acquired:
            raise RuntimeError("another IMAP polling worker is already active")

        def keepalive() -> None:
            connection.execute(text("SELECT 1")).scalar_one()
            connection.commit()

        yield keepalive
    finally:
        try:
            connection.execute(
                text("SELECT pg_advisory_unlock(:lock_id)"),
                {"lock_id": IMAP_WORKER_LOCK_KEY},
            )
            connection.commit()
        except Exception:
            LOGGER.warning("could not explicitly release the IMAP advisory lock")
        connection.close()


def mail_poll(settings: Settings, *, loop: bool) -> None:
    runtime, orchestrator = _real_orchestrator(settings)
    with _imap_worker_lease(runtime.engine) as keepalive:
        while True:
            keepalive()
            try:
                results = orchestrator.poll_once()
            except (OSError, TimeoutError, imaplib.IMAP4.abort) as exc:
                if not loop:
                    raise
                LOGGER.warning(
                    "transient IMAP poll failed; retrying on the next interval",
                    extra={"error_type": type(exc).__name__},
                )
                print_json({"processed": [], "poll_error": type(exc).__name__, "retrying": True})
                time.sleep(settings.imap_poll_seconds)
                continue
            print_json([result_dict(result) for result in results])
            if not loop:
                return
            time.sleep(settings.imap_poll_seconds)


def worker_run(settings: Settings) -> None:
    runtime, orchestrator = _real_orchestrator(settings)
    with _imap_worker_lease(runtime.engine) as keepalive:
        while True:
            keepalive()
            poll_error: str | None = None
            try:
                results = orchestrator.poll_once()
            except (OSError, TimeoutError, imaplib.IMAP4.abort) as exc:
                LOGGER.warning(
                    "transient IMAP poll failed; worker remains active",
                    extra={"error_type": type(exc).__name__},
                )
                results = []
                poll_error = type(exc).__name__
            try:
                dispatch_outcomes = runtime.legacy_processor.dispatch_due_scheduled_executions()
                scheduled_dispatched = sum(
                    outcome.execution is not None for outcome in dispatch_outcomes
                )
                scheduled_failed = sum(
                    outcome.status.value == "failed" for outcome in dispatch_outcomes
                )
            except Exception as exc:
                LOGGER.exception(
                    "durable execution queue dispatch failed; worker remains active",
                    extra={"error_type": type(exc).__name__},
                )
                scheduled_dispatched = 0
                scheduled_failed = 1
            sent, failed = runtime.outbox.send_once()
            print_json(
                {
                    "processed": [result_dict(result) for result in results],
                    "outbox_sent": sent,
                    "outbox_failed": failed,
                    "scheduled_dispatched": scheduled_dispatched,
                    "scheduled_failed": scheduled_failed,
                    "poll_error": poll_error,
                }
            )
            time.sleep(settings.imap_poll_seconds)


def outbox_send(settings: Settings, *, loop: bool) -> None:
    runtime = build_runtime(settings)
    while True:
        sent, failed = runtime.outbox.send_once()
        print_json({"sent": sent, "failed": failed})
        if not loop:
            return
        time.sleep(settings.imap_poll_seconds)


def retry_failures(settings: Settings) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        ids = [message.id for message in EmailRepository(session).failures()]
    results = [runtime.processor.retry_stored(email_id) for email_id in ids]
    print_json([result_dict(result) for result in results])


def quarantine_list(settings: Settings) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        rows = [
            {
                "email_id": message.id,
                "category": message.quarantine_category,
                "safe_message": message.quarantine_message,
                "quarantined_at": message.quarantined_at,
                "retry_count": message.quarantine_retry_count,
                "raw_size_bytes": message.raw_size_bytes,
                "raw_eml_path": message.raw_eml_path,
            }
            for message in EmailRepository(session).quarantined()
        ]
    print_json(rows)


def quarantine_retry(settings: Settings, email_id: uuid.UUID) -> None:
    runtime = build_runtime(settings)
    print_json(result_dict(runtime.processor.retry_stored(email_id)))


def _replay_senders(files: list[Path]) -> set[str]:
    senders: set[str] = set()
    for path in files:
        parsed = parse_email(path.read_bytes())
        if parsed.sender_address:
            senders.add(parsed.sender_address)
    return senders


def _bind_symbolic_reply(raw: bytes, runtime: Any) -> bytes:
    text = raw.decode("utf-8", errors="replace")
    if not SYMBOLIC_CLARIFICATION_RE.search(text):
        return raw
    with session_scope(runtime.session_factory) as session:
        clarification = session.scalar(
            select(Clarification).order_by(Clarification.created_at.desc()).limit(1)
        )
        if clarification is None or clarification.outbound_email_id is None:
            return raw
        outbound = session.get(EmailMessage, clarification.outbound_email_id)
        request = session.get(BusinessRequest, clarification.request_id)
        if outbound is None or not outbound.rfc_message_id or request is None:
            return raw
        text = SYMBOLIC_CLARIFICATION_RE.sub(
            lambda match: f"{match.group(1)}{outbound.rfc_message_id}", text, count=1
        )
        text = REQUEST_REFERENCE_RE.sub(request.public_reference, text)
    return text.encode("utf-8")


def _escalation_summaries(runtime: Any, email_message_id: uuid.UUID) -> list[dict[str, Any]]:
    with session_scope(runtime.session_factory) as session:
        escalations = session.scalars(
            select(Escalation)
            .where(Escalation.email_message_id == email_message_id)
            .order_by(Escalation.created_at, Escalation.id)
        ).all()
        return [
            {
                "escalation_id": escalation.id,
                "request_id": escalation.request_id,
                "recipient": escalation.recipient,
                "reason_code": escalation.reason_code,
                "summary": escalation.summary,
                "evidence": escalation.evidence,
            }
            for escalation in escalations
        ]


def replay_files(settings: Settings, files: list[Path]) -> None:
    if not files:
        raise ValueError("no .eml files were found")
    replay_settings = settings.model_copy(update={"dry_run": True})
    runtime = build_runtime(
        replay_settings,
        replay_authorized_senders=_replay_senders(files),
        initialize_schema=True,
    )
    results: list[dict[str, Any]] = []
    for index, path in enumerate(files, 1):
        raw = _bind_symbolic_reply(path.read_bytes(), runtime)
        result = runtime.processor.process_raw(
            raw,
            identity=InboundIdentity(
                mailbox="REPLAY",
                uidvalidity=1,
                uid=index,
            ),
        )
        sent, failed = runtime.outbox.send_once()
        results.append(
            {
                "file": str(path),
                **result_dict(result),
                "outbox_sent": sent,
                "outbox_failed": failed,
                "escalations": _escalation_summaries(runtime, result.email_message_id),
            }
        )
    print_json(
        {
            "mode": settings.effective_llm_provider.value,
            "demo_backend_measurement": settings.effective_llm_provider == LLMProvider.DEMO,
            "dry_run": True,
            "results": results,
        }
    )


def replay_email(settings: Settings, path: Path) -> None:
    replay_files(settings, [path])


def _scenario_alias_state(runtime: Any, result: ProcessingResult) -> dict[str, str]:
    state: dict[str, str] = {}
    with session_scope(runtime.session_factory) as session:
        inbound = session.get(EmailMessage, result.email_message_id)
        if inbound and inbound.rfc_message_id:
            state["inbound_message_id"] = inbound.rfc_message_id
        if not result.request_ids:
            return state
        request = session.get(BusinessRequest, result.request_ids[0])
        if request is None:
            return state
        state["request_id"] = str(request.id)
        state["request_reference"] = request.public_reference
        clarification = session.scalar(
            select(Clarification)
            .where(Clarification.request_id == request.id)
            .order_by(Clarification.created_at.desc())
            .limit(1)
        )
        if clarification and clarification.outbound_email_id:
            outbound = session.get(EmailMessage, clarification.outbound_email_id)
            if outbound and outbound.rfc_message_id:
                state["clarification_message_id"] = outbound.rfc_message_id
        outbox = session.scalar(
            select(OutboxMessage)
            .where(OutboxMessage.related_request_id == request.id)
            .order_by(OutboxMessage.created_at.desc())
            .limit(1)
        )
        if outbox:
            outbound = session.get(EmailMessage, outbox.outbound_email_id)
            if outbound and outbound.rfc_message_id:
                state["latest_outbound_message_id"] = outbound.rfc_message_id
                if clarification is None or outbound.id != clarification.outbound_email_id:
                    state["completion_message_id"] = outbound.rfc_message_id
    return state


def _bind_scenario_tokens(raw: bytes, aliases: dict[str, dict[str, str]]) -> bytes:
    text = raw.decode("utf-8", errors="replace")

    def replacement(match: re.Match[str]) -> str:
        alias, field = match.groups()
        try:
            return aliases[alias][field]
        except KeyError as exc:
            raise ValueError(f"scenario token {match.group(0)} has no seeded value") from exc

    rendered = SCENARIO_TOKEN_RE.sub(replacement, text)
    if "{{" in rendered or "}}" in rendered:
        raise ValueError("scenario email contains an unresolved template token")
    return rendered.encode("utf-8")


def _replay_manifest(settings: Settings, path: Path, scenario: str | None) -> None:
    manifest_path = path / "scenario.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("scenario.json must contain one JSON object")
    scenarios = manifest.get("scenarios")
    if isinstance(scenarios, dict):
        if scenario is None:
            if len(scenarios) != 1:
                raise ValueError(
                    "this directory defines multiple scenarios; select one with --scenario"
                )
            scenario = next(iter(scenarios))
        raw_steps = scenarios.get(scenario)
        scenario_name = scenario
    else:
        raw_steps = manifest.get("steps")
        scenario_name = str(manifest.get("name") or path.name)
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError("scenario manifest must define a non-empty step list")
    files = [path / str(step["file"]) for step in raw_steps if isinstance(step, dict)]
    replay_settings = settings.model_copy(update={"dry_run": True, "database_url": "sqlite://"})
    runtime = build_runtime(
        replay_settings,
        replay_authorized_senders=_replay_senders(files),
        initialize_schema=True,
    )
    aliases: dict[str, dict[str, str]] = {}
    results: list[dict[str, Any]] = []
    for index, raw_step in enumerate(raw_steps, 1):
        if not isinstance(raw_step, dict) or "file" not in raw_step:
            raise ValueError("every scenario step must contain a file")
        file_path = path / str(raw_step["file"])
        raw = _bind_scenario_tokens(file_path.read_bytes(), aliases)
        raw = _bind_symbolic_reply(raw, runtime)
        result = runtime.processor.process_raw(
            raw,
            identity=InboundIdentity(mailbox="REPLAY", uidvalidity=1, uid=index),
            execute_operations=not bool(raw_step.get("defer_execution", False)),
        )
        sent, failed = runtime.outbox.send_once()
        alias = str(raw_step.get("alias") or f"step_{index}")
        aliases[alias] = _scenario_alias_state(runtime, result)
        results.append(
            {
                "step": index,
                "alias": alias,
                "file": str(file_path),
                "defer_execution": bool(raw_step.get("defer_execution", False)),
                **result_dict(result),
                "outbox_sent": sent,
                "outbox_failed": failed,
                "seeded_state": aliases[alias],
                "escalations": _escalation_summaries(runtime, result.email_message_id),
            }
        )
    print_json(
        {
            "scenario": scenario_name,
            "manifest": str(manifest_path),
            "dry_run": True,
            "results": results,
        }
    )


def replay_directory(settings: Settings, path: Path, *, scenario: str | None = None) -> None:
    if (path / "scenario.json").exists():
        _replay_manifest(settings, path, scenario)
    else:
        if scenario is not None:
            raise ValueError("--scenario requires a scenario.json manifest")
        replay_files(settings, sorted(path.rglob("*.eml")))


def _validated_evaluation_controls(
    settings: Settings,
    *,
    budget_usd: Decimal | None,
    stop_before_budget_usd: Decimal | None,
    checkpoint_every: int | None,
    limit: int | None,
    cache_flags: tuple[bool, bool, bool],
) -> tuple[Decimal, Decimal, int]:
    if limit is not None and limit < 1:
        raise ValueError("--limit must be at least 1")
    if checkpoint_every is not None and checkpoint_every < 1:
        raise ValueError("--checkpoint-every must be at least 1")
    if sum(cache_flags) > 1:
        raise ValueError("choose only one cache mode")
    effective_budget = budget_usd if budget_usd is not None else settings.evaluation_run_budget_usd
    if not effective_budget.is_finite() or effective_budget < 0:
        raise ValueError("--budget-usd must be a finite non-negative amount")
    if stop_before_budget_usd is not None:
        if not stop_before_budget_usd.is_finite() or stop_before_budget_usd < 0:
            raise ValueError("--stop-before-budget-usd must be a finite non-negative amount")
        if stop_before_budget_usd > effective_budget:
            raise ValueError("--stop-before-budget-usd cannot exceed --budget-usd")
    configured_stop = (
        stop_before_budget_usd
        if stop_before_budget_usd is not None
        else settings.evaluation_stop_before_budget_usd
    )
    effective_stop = min(configured_stop, effective_budget * Decimal("0.95"))
    return (
        effective_budget,
        effective_stop,
        checkpoint_every or settings.evaluation_checkpoint_every,
    )


def evaluate_models(
    settings: Settings,
    *,
    dataset: Path,
    analyzer_model: str,
    verifier_model: str,
    output_dir: Path,
    limit: int | None,
    budget_usd: Decimal | None = None,
    stop_before_budget_usd: Decimal | None = None,
    confirm_budget: bool = False,
    use_cache: bool = False,
    no_cache: bool = False,
    refresh_cache: bool = False,
    resume: bool = False,
    checkpoint_every: int | None = None,
    env_file: Path | None = None,
) -> None:
    effective_budget, effective_stop, checkpoint = _validated_evaluation_controls(
        settings,
        budget_usd=budget_usd,
        stop_before_budget_usd=stop_before_budget_usd,
        checkpoint_every=checkpoint_every,
        limit=limit,
        cache_flags=(use_cache, no_cache, refresh_cache),
    )
    if settings.evaluation_require_budget_confirmation and not confirm_budget:
        raise ValueError(
            "EVALUATION_REQUIRE_BUDGET_CONFIRMATION=true; review the run budget and rerun with "
            "--confirm-budget"
        )
    effective_settings = settings.model_copy(
        update={"evaluation_stop_before_budget_usd": effective_stop}
    )
    examples = load_dataset(dataset)
    if limit is not None:
        examples = examples[:limit]
    cache_mode: CacheMode = (
        "refresh"
        if refresh_cache
        else "no_cache"
        if no_cache
        else "use"
        if use_cache
        else "no_cache"
    )
    command_prefix = "python -m snoc_agent.cli.main"
    if env_file is not None:
        command_prefix += f" --env-file {shlex.quote(str(env_file))}"
    command_parts = [
        f"{command_prefix} evaluate",
        f"--dataset {shlex.quote(str(dataset))}",
        f"--analyzer-model {shlex.quote(analyzer_model)}",
        f"--verifier-model {shlex.quote(verifier_model)}",
        "--resume",
        f"--budget-usd {effective_budget}",
        f"--stop-before-budget-usd {effective_settings.evaluation_stop_before_budget_usd}",
        f"--checkpoint-every {checkpoint}",
        f"--output-dir {shlex.quote(str(output_dir))}",
        "--use-cache"
        if cache_mode == "use"
        else "--refresh-cache"
        if cache_mode == "refresh"
        else "--no-cache",
    ]
    if limit is not None:
        command_parts.append(f"--limit {limit}")
    if confirm_budget:
        command_parts.append("--confirm-budget")
    result = run_persistent_matrix(
        effective_settings,
        dataset_path=dataset,
        examples=examples,
        output_dir=output_dir,
        cache_mode=cache_mode,
        resume=resume,
        budget_usd=effective_budget,
        checkpoint_every=checkpoint,
        resumable_command=" ".join(command_parts),
        model_pairs={"selected_pair": ModelPair("selected_pair", analyzer_model, verifier_model)},
    )
    print_json(result)


def evaluate_model_matrix(
    settings: Settings,
    *,
    dataset: Path,
    output_dir: Path,
    limit: int | None,
    use_cache: bool = False,
    no_cache: bool = False,
    refresh_cache: bool = False,
    resume: bool = False,
    budget_usd: Decimal | None = None,
    stop_before_budget_usd: Decimal | None = None,
    checkpoint_every: int | None = None,
    confirm_budget: bool = False,
    env_file: Path | None = None,
) -> None:
    effective_budget, effective_stop, checkpoint = _validated_evaluation_controls(
        settings,
        budget_usd=budget_usd,
        stop_before_budget_usd=stop_before_budget_usd,
        checkpoint_every=checkpoint_every,
        limit=limit,
        cache_flags=(use_cache, no_cache, refresh_cache),
    )
    if settings.evaluation_require_budget_confirmation and not confirm_budget:
        raise ValueError(
            "EVALUATION_REQUIRE_BUDGET_CONFIRMATION=true; review the run budget and rerun with "
            "--confirm-budget"
        )
    examples = load_dataset(dataset)
    if limit is not None:
        examples = examples[:limit]
    if no_cache:
        cache_mode: CacheMode = "no_cache"
    elif refresh_cache:
        cache_mode = "refresh"
    else:
        cache_mode = "use" if use_cache else "no_cache"
    effective_settings = settings.model_copy(
        update={"evaluation_stop_before_budget_usd": effective_stop}
    )
    command_prefix = "python -m snoc_agent.cli.main"
    if env_file is not None:
        command_prefix += f" --env-file {shlex.quote(str(env_file))}"
    command_parts = [
        f"{command_prefix} evaluate",
        f"--dataset {shlex.quote(str(dataset))}",
        "--matrix",
        "--resume",
        f"--budget-usd {effective_budget}",
        f"--stop-before-budget-usd {effective_settings.evaluation_stop_before_budget_usd}",
        f"--checkpoint-every {checkpoint}",
        f"--output-dir {shlex.quote(str(output_dir))}",
    ]
    if limit is not None:
        command_parts.append(f"--limit {limit}")
    command_parts.append(
        "--use-cache"
        if cache_mode == "use"
        else "--refresh-cache"
        if cache_mode == "refresh"
        else "--no-cache"
    )
    if confirm_budget:
        command_parts.append("--confirm-budget")
    result = run_persistent_matrix(
        effective_settings,
        dataset_path=dataset,
        examples=examples,
        output_dir=output_dir,
        cache_mode=cache_mode,
        resume=resume,
        budget_usd=effective_budget,
        checkpoint_every=checkpoint,
        resumable_command=" ".join(command_parts),
        model_pairs=(
            VLLM_MODEL_PAIRS if settings.effective_llm_provider == LLMProvider.VLLM else None
        ),
    )
    print_json(result)


def request_show(settings: Settings, reference: str) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        request = session.scalar(
            select(BusinessRequest).where(BusinessRequest.public_reference == reference)
        )
        if request is None:
            raise LookupError(f"request {reference!r} was not found")
        print_json(
            {
                "id": request.id,
                "public_reference": request.public_reference,
                "conversation_id": request.conversation_id,
                "status": request.status,
                "request_kind": request.request_kind,
                "version": request.version,
                "escalation_reason": request.escalation_reason,
                "operations": [operation_dict(operation) for operation in request.operations],
                "clarifications": [
                    {
                        "id": clarification.id,
                        "status": clarification.status,
                        "requested_fields": clarification.requested_fields,
                        "round_number": clarification.round_number,
                    }
                    for clarification in request.clarifications
                ],
            }
        )


def operation_dict(operation: Operation) -> dict[str, Any]:
    return {
        "id": operation.id,
        "request_id": operation.request_id,
        "sequence_number": operation.sequence_number,
        "action": operation.action,
        "status": operation.status,
        "pdv_code": operation.pdv_code,
        "phone": operation.phone,
        "missing_fields": operation.missing_fields,
        "current_revision": operation.current_revision,
        "final_decision": operation.final_decision,
        "execution_eligible": operation.execution_eligible,
        "model_agreement": operation.model_agreement,
    }


def operation_show(settings: Settings, operation_id: uuid.UUID) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        operation = session.get(Operation, operation_id)
        if operation is None:
            raise LookupError(f"operation {operation_id} was not found")
        executions = list(
            session.scalars(select(Execution).where(Execution.operation_id == operation.id))
        )
        print_json(
            {
                **operation_dict(operation),
                "evidence": operation.evidence,
                "field_provenance": operation.field_provenance,
                "executions": [
                    {
                        "id": execution.id,
                        "idempotency_key": execution.idempotency_key,
                        "status": execution.status,
                        "dry_run": execution.dry_run,
                        "attempt_count": execution.attempt_count,
                    }
                    for execution in executions
                ],
            }
        )


def conversation_show(settings: Settings, conversation_id: uuid.UUID) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id} was not found")
        print_json(
            {
                "id": conversation.id,
                "normalized_subject": conversation.normalized_subject,
                "primary_sender": conversation.primary_sender,
                "status": conversation.status,
                "messages": [
                    {
                        "id": message.id,
                        "direction": message.direction,
                        "message_id": message.rfc_message_id,
                        "subject": message.subject,
                        "processing_status": message.processing_status,
                    }
                    for message in conversation.messages
                ],
                "requests": [
                    {
                        "id": request.id,
                        "public_reference": request.public_reference,
                        "status": request.status,
                    }
                    for request in conversation.requests
                ],
            }
        )


def _audit_model_run(run: ModelRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "stage": run.stage,
        "backend": run.backend,
        "base_model_id": run.base_model_id,
        "resolved_model_id": run.resolved_model_id,
        "reported_provider": run.reported_provider,
        "provider_request_id": run.provider_request_id,
        "prompt_version": run.prompt_version,
        "input_context": run.input_context,
        "raw_output": run.raw_output,
        "parsed_output": run.parsed_output,
        "structured_output_valid": run.structured_output_valid,
        "structured_output_mode": run.structured_output_mode,
        "schema_name": run.schema_name,
        "fallback_reason": run.fallback_reason,
        "parse_attempt_count": run.parse_attempt_count,
        "validation_errors": run.validation_errors,
        "reasoning_output": run.reasoning_output,
        "latency_seconds": run.latency_seconds,
        "request_attempt_count": run.request_attempt_count,
        "usage": {
            "prompt_tokens": run.prompt_tokens,
            "completion_tokens": run.completion_tokens,
            "total_tokens": run.total_tokens,
        },
        "cost": {
            "input_usd": run.input_cost_usd,
            "output_usd": run.output_cost_usd,
            "total_usd": run.total_cost_usd,
            "basis": run.cost_basis,
            "pricing_metadata": run.pricing_metadata,
        },
        "error": run.error,
        "error_category": run.error_category,
        "cached_from_model_run_id": run.cached_from_model_run_id,
        "created_at": run.created_at,
    }


def audit_list(settings: Settings, *, limit: int) -> None:
    if limit < 1:
        raise ValueError("--limit must be at least 1")
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        emails = session.scalars(
            select(EmailMessage)
            .where(EmailMessage.direction == "inbound")
            .order_by(EmailMessage.created_at.desc())
            .limit(limit)
        ).all()
        payload = []
        for email in emails:
            runs = session.scalars(
                select(ModelRun)
                .where(ModelRun.email_message_id == email.id)
                .order_by(ModelRun.created_at)
            ).all()
            request_references = session.scalars(
                select(BusinessRequest.public_reference).where(
                    BusinessRequest.initiating_email_id == email.id
                )
            ).all()
            payload.append(
                {
                    "email_id": email.id,
                    "created_at": email.created_at,
                    "message_id": email.rfc_message_id,
                    "sender": email.sender,
                    "subject": email.subject,
                    "processing_status": email.processing_status,
                    "automated_classification": email.automated_classification,
                    "authorization_allowed": email.authorization_allowed,
                    "request_references": request_references,
                    "model_stages": [run.stage for run in runs],
                    "model_error_categories": [
                        run.error_category for run in runs if run.error_category is not None
                    ],
                }
            )
    print_json(payload)


def audit_show(settings: Settings, email_id: uuid.UUID) -> None:
    """Render all persisted audit records causally associated with one inbound email."""

    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        email = session.get(EmailMessage, email_id)
        if email is None:
            raise LookupError(f"email {email_id} was not found")
        if email.direction != "inbound":
            raise ValueError("audit show accepts an inbound email id")

        requests = session.scalars(
            select(BusinessRequest)
            .where(BusinessRequest.initiating_email_id == email.id)
            .order_by(BusinessRequest.created_at)
        ).all()
        clarifications = session.scalars(
            select(Clarification)
            .where(
                (Clarification.source_inbound_email_id == email.id)
                | (Clarification.reply_email_id == email.id)
            )
            .order_by(Clarification.created_at)
        ).all()
        request_by_id = {request.id: request for request in requests}
        for clarification in clarifications:
            request = session.get(BusinessRequest, clarification.request_id)
            if request is not None:
                request_by_id[request.id] = request
        request_ids = list(request_by_id)
        operations = (
            session.scalars(
                select(Operation)
                .where(Operation.request_id.in_(request_ids))
                .order_by(Operation.sequence_number)
            ).all()
            if request_ids
            else []
        )
        operation_ids = [operation.id for operation in operations]
        decisions = (
            session.scalars(
                select(ValidationDecision)
                .where(ValidationDecision.operation_id.in_(operation_ids))
                .order_by(ValidationDecision.created_at)
            ).all()
            if operation_ids
            else []
        )
        executions = (
            session.scalars(
                select(Execution)
                .where(Execution.operation_id.in_(operation_ids))
                .order_by(Execution.created_at)
            ).all()
            if operation_ids
            else []
        )
        model_runs = session.scalars(
            select(ModelRun)
            .where(ModelRun.email_message_id == email.id)
            .order_by(ModelRun.created_at)
        ).all()
        escalations = session.scalars(
            select(Escalation)
            .where(Escalation.email_message_id == email.id)
            .order_by(Escalation.created_at)
        ).all()
        outbox = (
            session.scalars(
                select(OutboxMessage)
                .where(OutboxMessage.related_request_id.in_(request_ids))
                .order_by(OutboxMessage.created_at)
            ).all()
            if request_ids
            else []
        )

        print_json(
            {
                "email": {
                    "id": email.id,
                    "message_id": email.rfc_message_id,
                    "sender": email.sender,
                    "recipients": email.recipients_json,
                    "subject": email.subject,
                    "processing_status": email.processing_status,
                    "imap": {
                        "mailbox": email.mailbox_name,
                        "uidvalidity": email.uidvalidity,
                        "uid": email.imap_uid,
                        "flags": email.flags_json,
                        "internal_date": email.internal_date,
                    },
                    "parsing": {
                        "automated_classification": email.automated_classification,
                        "warnings": email.parsing_warnings,
                        "quarantine_category": email.quarantine_category,
                        "quarantine_message": email.quarantine_message,
                        "context_limit_metadata": email.context_limit_metadata,
                    },
                    "authorization": {
                        "allowed": email.authorization_allowed,
                        "reason": email.authorization_reason,
                    },
                    "correlation": email.correlation_details,
                    "latest_user_message": email.latest_user_message,
                },
                "model_runs": [_audit_model_run(run) for run in model_runs],
                "requests": [
                    {
                        "id": request.id,
                        "reference": request.public_reference,
                        "status": request.status,
                        "request_kind": request.request_kind,
                        "escalation_reason": request.escalation_reason,
                    }
                    for request in request_by_id.values()
                ],
                "operations": [operation_dict(operation) for operation in operations],
                "validation_decisions": [
                    {
                        "id": decision.id,
                        "operation_id": decision.operation_id,
                        "decision": decision.decision,
                        "reasons": decision.reasons,
                        "hard_invariant_results": decision.hard_invariant_results,
                        "analyzer_result": decision.analyzer_result,
                        "verifier_result": decision.verifier_result,
                        "policy_version": decision.policy_version,
                        "created_at": decision.created_at,
                    }
                    for decision in decisions
                ],
                "clarifications": [
                    {
                        "id": clarification.id,
                        "request_id": clarification.request_id,
                        "status": clarification.status,
                        "requested_fields": clarification.requested_fields,
                        "target_operation_ids": clarification.target_operation_ids,
                        "round_number": clarification.round_number,
                        "reply_email_id": clarification.reply_email_id,
                    }
                    for clarification in clarifications
                ],
                "executions": [
                    {
                        "id": execution.id,
                        "operation_id": execution.operation_id,
                        "operation_revision": execution.operation_revision,
                        "idempotency_key": execution.idempotency_key,
                        "status": execution.status,
                        "dry_run": execution.dry_run,
                        "attempt_count": execution.attempt_count,
                        "endpoint": execution.endpoint,
                        "response_status": execution.response_status,
                        "response_body": execution.response_body,
                    }
                    for execution in executions
                ],
                "escalations": [
                    {
                        "id": escalation.id,
                        "reason_code": escalation.reason_code,
                        "status": escalation.status,
                        "summary": escalation.summary,
                        "evidence": escalation.evidence,
                    }
                    for escalation in escalations
                ],
                "outbox": [
                    {
                        "id": item.id,
                        "recipient": item.recipient,
                        "subject": item.subject,
                        "status": item.status,
                        "retry_count": item.retry_count,
                        "last_error": item.last_error,
                    }
                    for item in outbox
                ],
            }
        )


def failures_list(settings: Settings) -> None:
    runtime = build_runtime(settings)
    with session_scope(runtime.session_factory) as session:
        failures = EmailRepository(session).failures()
        print_json(
            [
                {
                    "email_message_id": message.id,
                    "rfc_message_id": message.rfc_message_id,
                    "subject": message.subject,
                    "warnings": message.parsing_warnings,
                }
                for message in failures
            ]
        )
