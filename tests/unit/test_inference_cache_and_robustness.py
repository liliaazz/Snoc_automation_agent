from __future__ import annotations

import json
import uuid
from decimal import Decimal
from email.message import EmailMessage as RFCEmailMessage
from pathlib import Path

import pytest
from sqlalchemy import func, select

from snoc_agent.ai.backend import GenerationConfig, StructuredGenerationResult
from snoc_agent.ai.context_builder import ContextBuilder
from snoc_agent.ai.errors import InferenceError, InferenceErrorCategory
from snoc_agent.ai.schemas import EmailAnalysis
from snoc_agent.cli.commands import evaluate_model_matrix
from snoc_agent.cli.runtime import build_runtime
from snoc_agent.config import Settings
from snoc_agent.db import create_engine_and_session, create_schema
from snoc_agent.db.models import (
    EmailMessage,
    EvaluationInference,
    EvaluationRun,
    Execution,
    MailAccount,
    ModelRun,
)
from snoc_agent.db.session import session_scope
from snoc_agent.domain.enums import ProcessingStatus
from snoc_agent.evaluation.dataset_loader import EvaluationExample, OperationExpectation
from snoc_agent.evaluation.inference_cache import PersistentInferenceCache
from snoc_agent.evaluation.matrix_runner import run_persistent_matrix
from snoc_agent.evaluation.pipeline_predictor import (
    evaluation_context,
    evaluation_context_builder,
)
from snoc_agent.mail.fake_mailbox import FakeIMAPMailbox
from snoc_agent.mail.interfaces import MailboxMessage
from snoc_agent.mail.mime import ContentLimits
from snoc_agent.mail.parser import parse_email
from snoc_agent.workflow.inbound_processor import InboundIdentity
from snoc_agent.workflow.orchestrator import MailOrchestrator


def raw_email(body: str = "La réunion est reportée.") -> bytes:
    message = RFCEmailMessage()
    message["Message-ID"] = "<robustness@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = "Information"
    message.set_content(body)
    return message.as_bytes()


def test_raw_size_limit_quarantines_once_and_retains_source(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'quarantine.db'}",
        raw_eml_directory=tmp_path / "raw",
        max_raw_email_bytes=50,
    )
    runtime = build_runtime(settings, initialize_schema=True)
    identity = InboundIdentity(mailbox="INBOX", uidvalidity=1, uid=7)

    first = runtime.processor.process_raw(raw_email(), identity=identity)
    second = runtime.processor.process_raw(raw_email(), identity=identity)

    assert first.status == ProcessingStatus.QUARANTINED.value
    assert second.status == ProcessingStatus.DUPLICATE.value
    with session_scope(runtime.session_factory) as session:
        stored = session.get(EmailMessage, first.email_message_id)
        assert stored is not None
        assert stored.quarantine_category == "raw_email_size_limit"
        assert stored.raw_eml_path is not None
        assert stored.raw_size_bytes == len(raw_email())


def test_parse_fatal_is_quarantined_once_and_physical_rediscovery_skips_parser(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = raw_email("Message qui déclenche une panne injectée du parseur.")
    settings = Settings(
        llm_provider="demo",
        workflow_engine="legacy",
        database_url=f"sqlite:///{tmp_path / 'parse-fatal.db'}",
        raw_eml_directory=tmp_path / "raw",
    )
    runtime = build_runtime(settings, initialize_schema=True)
    with session_scope(runtime.session_factory) as session:
        account = MailAccount(name="parse-fatal-test", mailbox="INBOX")
        session.add(account)
        session.flush()
        account_id = account.id

    parse_calls = 0

    def injected_parse_failure(*_args: object, **_kwargs: object) -> object:
        nonlocal parse_calls
        parse_calls += 1
        raise RuntimeError("sensitive parser internals must not be persisted")

    monkeypatch.setattr(
        "snoc_agent.workflow.inbound_processor.parse_email",
        injected_parse_failure,
    )
    orchestrator = MailOrchestrator(
        mailbox=FakeIMAPMailbox(
            [
                MailboxMessage(
                    mailbox="INBOX",
                    uidvalidity=41,
                    uid=9,
                    raw_message=source,
                )
            ]
        ),
        processor=runtime.processor,
        mail_account_id=account_id,
    )

    first = orchestrator.poll_once()
    second = orchestrator.poll_once()

    assert [result.status for result in first] == [ProcessingStatus.QUARANTINED.value]
    assert [result.status for result in second] == [ProcessingStatus.DUPLICATE.value]
    assert parse_calls == 1
    with session_scope(runtime.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(EmailMessage)) == 1
        stored = session.get(EmailMessage, first[0].email_message_id)
        assert stored is not None
        assert stored.processing_status == ProcessingStatus.QUARANTINED.value
        assert stored.quarantine_category == "RuntimeError"
        assert stored.quarantine_message == (
            "MIME parsing failed; inspect the retained raw email before retrying."
        )
        assert "sensitive parser internals" not in stored.quarantine_message
        assert stored.quarantine_retry_count == 0
        assert "quarantined:RuntimeError" in stored.parsing_warnings
        assert stored.raw_eml_path is not None
        assert stored.raw_eml_blob is None
        assert Path(stored.raw_eml_path).read_bytes() == source


def test_quarantine_retry_uses_retained_raw_message(tmp_path) -> None:
    database_url = f"sqlite:///{tmp_path / 'retry.db'}"
    first_runtime = build_runtime(
        Settings(
            database_url=database_url,
            raw_eml_directory=tmp_path / "raw",
            max_raw_email_bytes=50,
        ),
        initialize_schema=True,
    )
    quarantined = first_runtime.processor.process_raw(raw_email())
    assert quarantined.status == ProcessingStatus.QUARANTINED.value

    retry_runtime = build_runtime(
        Settings(
            database_url=database_url,
            raw_eml_directory=tmp_path / "raw",
            max_raw_email_bytes=10_000,
        ),
        replay_authorized_senders={"manager@example.test"},
    )
    retried = retry_runtime.processor.retry_stored(quarantined.email_message_id)

    assert retried.status != ProcessingStatus.QUARANTINED.value
    with session_scope(retry_runtime.session_factory) as session:
        stored = session.get(EmailMessage, quarantined.email_message_id)
        assert stored is not None
        assert stored.quarantine_retry_count == 1
        assert stored.quarantine_category is None


def test_html_part_limit_is_explicit_and_bounds_decoded_html() -> None:
    message = RFCEmailMessage()
    message["Message-ID"] = "<html-limit@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = "HTML volumineux"
    message.set_content("<html><body><p>" + "A" * 500 + "</p></body></html>", subtype="html")

    parsed = parse_email(
        message.as_bytes(),
        content_limits=ContentLimits(
            max_text_part_bytes=128,
            max_html_part_bytes=48,
            max_attachment_count=2,
            max_attachment_bytes=64,
        ),
    )

    assert parsed.html_body is not None
    assert len(parsed.html_body.encode("utf-8")) <= 48
    assert "html_part_limit_exceeded" in parsed.parsing_warnings
    assert "html_only_body_converted_to_text" in parsed.parsing_warnings


def test_attachment_count_limit_omits_excess_metadata_with_warning() -> None:
    message = RFCEmailMessage()
    message["Message-ID"] = "<attachment-count@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = "Pièces jointes"
    message.set_content("Corps sans identifiant métier.")
    for index in range(3):
        message.add_attachment(
            f"attachment-{index}".encode(),
            maintype="application",
            subtype="octet-stream",
            filename=f"file-{index}.bin",
        )

    parsed = parse_email(
        message.as_bytes(),
        content_limits=ContentLimits(
            max_text_part_bytes=128,
            max_html_part_bytes=128,
            max_attachment_count=2,
            max_attachment_bytes=64,
        ),
    )

    assert [item["filename"] for item in parsed.attachment_metadata] == [
        "file-0.bin",
        "file-1.bin",
    ]
    assert "attachment_count_limit_exceeded" in parsed.parsing_warnings


def test_attachment_size_limit_retains_only_bounded_metadata_and_warning() -> None:
    message = RFCEmailMessage()
    message["Message-ID"] = "<attachment-size@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = "Pièce jointe volumineuse"
    message.set_content("Corps sans identifiant métier.")
    payload = b"X" * 80
    message.add_attachment(
        payload,
        maintype="application",
        subtype="octet-stream",
        filename="large.bin",
    )

    parsed = parse_email(
        message.as_bytes(),
        content_limits=ContentLimits(
            max_text_part_bytes=128,
            max_html_part_bytes=128,
            max_attachment_count=2,
            max_attachment_bytes=16,
        ),
    )

    assert len(parsed.attachment_metadata) == 1
    metadata = parsed.attachment_metadata[0]
    assert metadata["filename"] == "large.bin"
    assert metadata["size"] == len(payload)
    assert metadata["exceeds_size_limit"] is True
    assert len(metadata["sha256"]) == 64
    assert "attachment_size_limit_exceeded" in parsed.parsing_warnings


def test_configured_mime_limits_are_persisted_and_prevent_execution(tmp_path) -> None:
    message = RFCEmailMessage()
    message["Message-ID"] = "<configured-mime-limits@example.test>"
    message["From"] = "manager@example.test"
    message["To"] = "snoc@example.test"
    message["Subject"] = "Déblocage avec pièces jointes"
    message.set_content("Merci de débloquer le PDV 12345678.")
    message.add_alternative("<p>" + "H" * 500 + "</p>", subtype="html")
    for index, payload in enumerate((b"X" * 80, b"small", b"omitted")):
        message.add_attachment(
            payload,
            maintype="application",
            subtype="octet-stream",
            filename=f"configured-{index}.bin",
        )
    settings = Settings(
        llm_provider="demo",
        database_url=f"sqlite:///{tmp_path / 'configured-limits.db'}",
        raw_eml_directory=tmp_path / "raw",
        authorized_senders="manager@example.test",
        max_html_part_bytes=48,
        max_attachment_count=2,
        max_attachment_bytes=16,
    )
    runtime = build_runtime(settings, initialize_schema=True)

    result = runtime.processor.process_raw(message.as_bytes())

    assert "auto_execute" not in result.decisions
    with session_scope(runtime.session_factory) as session:
        stored = session.get(EmailMessage, result.email_message_id)
        assert stored is not None
        assert {
            "html_part_limit_exceeded",
            "attachment_count_limit_exceeded",
            "attachment_size_limit_exceeded",
        }.issubset(stored.parsing_warnings)
        assert stored.context_limit_metadata["automatic_execution_allowed"] is False
        assert set(stored.context_limit_metadata["mime_limit_warnings"]) == {
            "html_part_limit_exceeded",
            "attachment_count_limit_exceeded",
            "attachment_size_limit_exceeded",
        }
        assert session.scalar(select(func.count()).select_from(Execution)) == 0


def test_mime_and_context_limits_are_explicit_and_block_auto_execution() -> None:
    parsed = parse_email(
        raw_email("A" * 1000),
        content_limits=ContentLimits(
            max_text_part_bytes=32,
            max_html_part_bytes=32,
            max_attachment_count=1,
            max_attachment_bytes=32,
        ),
    )
    context = ContextBuilder(
        max_context_characters=300,
        max_latest_characters=20,
        max_relevant_thread_characters=20,
    ).new_request(parsed)

    assert "text_part_limit_exceeded" in parsed.parsing_warnings
    assert context["automatic_execution_allowed"] is False
    assert "latest_message_character_limit_exceeded" in context["context_limit_warnings"]
    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True)) <= 300


def test_context_truncation_preserves_complete_pre_truncation_numeric_candidates() -> None:
    identifier = "12345678"
    parsed = parse_email(raw_email("préfixe " + "A" * 400 + f" {identifier} " + "B" * 400))
    context = ContextBuilder(
        max_context_characters=600,
        max_latest_characters=40,
        max_relevant_thread_characters=20,
    ).new_request(parsed)

    serialized = json.dumps(context, ensure_ascii=False, sort_keys=True)
    retained_text = str(context["latest_user_message"])
    candidate_values = [row["value"] for row in context["numeric_candidates"]]

    assert len(serialized) <= 600
    assert candidate_values == [identifier]
    assert identifier not in retained_text
    assert not any(identifier[:length] in retained_text for length in range(3, len(identifier)))
    assert context["automatic_execution_allowed"] is False


def test_evaluation_context_uses_settings_limits_and_retains_source_candidates() -> None:
    settings = Settings(
        max_model_context_characters=512,
        max_latest_message_characters=32,
        max_relevant_thread_characters=16,
    )
    example = EvaluationExample(
        example_id="bounded-evaluation",
        subject="Demande",
        body="A" * 200 + " PDV 87654321 " + "B" * 200,
        expected_operations=(),
    )

    context, candidates = evaluation_context(
        example,
        context_builder=evaluation_context_builder(settings),
    )

    assert len(json.dumps(context, ensure_ascii=False, sort_keys=True)) <= 512
    assert len(str(context["latest_user_message"])) <= 32
    assert any(candidate["value"] == "87654321" for candidate in candidates)
    assert context["automatic_execution_allowed"] is False


def test_context_builder_rejects_impractically_small_total_limit() -> None:
    with pytest.raises(ValueError, match="at least 256"):
        ContextBuilder(max_context_characters=255)


def test_persistent_matrix_deduplicates_and_reuses_equivalent_calls(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    example = EvaluationExample(
        example_id="one",
        subject="Déblocage",
        body="Débloquer le PDV 12345678.",
        expected_operations=(OperationExpectation("account_unblock", "12345678"),),
    )
    dataset.write_text(json.dumps(example.as_dict()) + "\n", encoding="utf-8")
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'evaluation.db'}")

    first = run_persistent_matrix(
        settings,
        dataset_path=dataset,
        examples=[example],
        output_dir=tmp_path / "first",
        cache_mode="use",
        resume=False,
        budget_usd=Decimal("2"),
        checkpoint_every=1,
        resumable_command="resume-first",
    )
    engine, session_factory = create_engine_and_session(settings.database_url)
    with session_scope(session_factory) as session:
        first_runs = session.scalar(select(func.count()).select_from(ModelRun))
    second = run_persistent_matrix(
        settings,
        dataset_path=dataset,
        examples=[example],
        output_dir=tmp_path / "second",
        cache_mode="use",
        resume=False,
        budget_usd=Decimal("2"),
        checkpoint_every=1,
        resumable_command="resume-second",
    )
    with session_scope(session_factory) as session:
        second_runs = session.scalar(select(func.count()).select_from(ModelRun))
        inference_rows = session.scalar(select(func.count()).select_from(EvaluationInference))
    engine.dispose()

    assert first["demo_backend_measurement"] is True
    assert second["demo_backend_measurement"] is True
    assert first_runs == 4  # two analyzers plus two unique verifier-model calls
    assert second_runs == first_runs
    assert inference_rows == 8
    attribution = json.loads((tmp_path / "first/failure_attribution.json").read_text())
    assert attribution["real_model_measurement_performed"] is False
    assert all(
        not details["real_model_analyzer_failures"] for details in attribution["runs"].values()
    )


def test_persistent_matrix_applies_configured_context_limit(tmp_path) -> None:
    dataset = tmp_path / "bounded.jsonl"
    example = EvaluationExample(
        example_id="bounded-matrix",
        subject="Déblocage",
        body="A" * 1_000 + " PDV 12345678 " + "B" * 1_000,
        expected_operations=(OperationExpectation("account_unblock", "12345678"),),
    )
    dataset.write_text(json.dumps(example.as_dict()) + "\n", encoding="utf-8")
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'bounded-matrix.db'}",
        max_model_context_characters=512,
        max_latest_message_characters=40,
        max_relevant_thread_characters=20,
    )

    run_persistent_matrix(
        settings,
        dataset_path=dataset,
        examples=[example],
        output_dir=tmp_path / "bounded-output",
        cache_mode="no_cache",
        resume=False,
        budget_usd=Decimal("2"),
        checkpoint_every=1,
        resumable_command="resume-bounded",
    )

    engine, session_factory = create_engine_and_session(settings.database_url)
    with session_scope(session_factory) as session:
        analysis_runs = session.scalars(select(ModelRun).where(ModelRun.stage == "analysis")).all()
        assert analysis_runs
        for model_run in analysis_runs:
            assert (
                len(json.dumps(model_run.input_context, ensure_ascii=False, sort_keys=True))
                <= settings.max_model_context_characters
            )
            assert model_run.input_context["automatic_execution_allowed"] is False
    engine.dispose()


def test_resume_references_original_model_run_without_invoking_again(tmp_path) -> None:
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'resume.db'}")
    create_schema(engine)
    run_id = uuid.uuid4()
    with session_scope(session_factory) as session:
        session.add(
            EvaluationRun(
                id=run_id,
                status="running",
                dataset_path="dataset.jsonl",
                dataset_hash="d" * 64,
                configuration_hash="c" * 64,
                configuration={},
                output_dir="output",
                cost_so_far_usd=0,
                request_count=0,
                unknown_cost_request_count=0,
                prompt_tokens=0,
                completion_tokens=0,
                checkpoint_row=0,
            )
        )
    config = GenerationConfig(model="demo-model", base_model="demo-model")
    context = {"mode": "new_request"}
    parsed = EmailAnalysis(
        message_kind="irrelevant",
        referenced_existing_operation_ids=[],
        operations=[],
        new_request_present=False,
        contradiction_with_stored_state=False,
        contradiction_details=[],
        unresolved_ambiguities=[],
    )
    first_cache = PersistentInferenceCache(
        session_factory,
        evaluation_run_id=run_id,
        cache_mode="no_cache",
        resume=False,
    )
    first = first_cache.get_or_run(
        example_id="example",
        stage="analysis",
        analyzer_source_model_id=None,
        prompt_version="analyzer_v1",
        input_context=context,
        response_model=EmailAnalysis,
        config=config,
        invoke=lambda: StructuredGenerationResult(
            parsed=parsed,
            raw_output=parsed.model_dump_json(),
            model_name="demo-model",
            backend="demo",
            latency_seconds=0,
            base_model_id="demo-model",
            resolved_model_id="demo-model",
            json_schema=EmailAnalysis.model_json_schema(),
            schema_name="EmailAnalysis",
        ),
    )
    resumed_cache = PersistentInferenceCache(
        session_factory,
        evaluation_run_id=run_id,
        cache_mode="no_cache",
        resume=True,
    )
    resumed = resumed_cache.get_or_run(
        example_id="example",
        stage="analysis",
        analyzer_source_model_id=None,
        prompt_version="analyzer_v1",
        input_context=context,
        response_model=EmailAnalysis,
        config=config,
        invoke=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    engine.dispose()

    assert first.original_model_run_id is not None
    assert resumed.cache_hit is True
    assert resumed.original_model_run_id == first.original_model_run_id


def test_budget_preflight_pause_does_not_create_fake_model_run(tmp_path) -> None:
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'budget.db'}")
    create_schema(engine)
    run_id = uuid.uuid4()
    with session_scope(session_factory) as session:
        session.add(
            EvaluationRun(
                id=run_id,
                status="running",
                dataset_path="dataset.jsonl",
                dataset_hash="d" * 64,
                configuration_hash="c" * 64,
                configuration={},
                output_dir="output",
                cost_so_far_usd=0,
                request_count=0,
                unknown_cost_request_count=0,
                prompt_tokens=0,
                completion_tokens=0,
                checkpoint_row=0,
            )
        )
    cache = PersistentInferenceCache(
        session_factory,
        evaluation_run_id=run_id,
        cache_mode="use",
        resume=False,
    )

    def stop_before_transport() -> StructuredGenerationResult:
        raise InferenceError(
            InferenceErrorCategory.BUDGET_EXHAUSTED,
            "inference budget stop threshold reached",
        )

    try:
        cache.get_or_run(
            example_id="example",
            stage="analysis",
            analyzer_source_model_id=None,
            prompt_version="analyzer_v1",
            input_context={"mode": "new_request"},
            response_model=EmailAnalysis,
            config=GenerationConfig(model="model", base_model="model"),
            invoke=stop_before_transport,
        )
    except InferenceError as exc:
        assert exc.category == InferenceErrorCategory.BUDGET_EXHAUSTED
    else:
        raise AssertionError("budget exhaustion must stop the inference")

    with session_scope(session_factory) as session:
        assert session.scalar(select(func.count()).select_from(ModelRun)) == 0
        marker = session.scalar(select(EvaluationInference))
        assert marker is not None
        assert marker.status == "paused"
        assert marker.model_run_id is None
    engine.dispose()


def test_evaluation_budget_confirmation_blocks_before_dataset_or_network(tmp_path) -> None:
    settings = Settings(
        llm_provider="vllm",
        vllm_api_key="vllm_test_placeholder",
        evaluation_require_budget_confirmation=True,
    )

    with pytest.raises(ValueError, match="--confirm-budget"):
        evaluate_model_matrix(
            settings,
            dataset=tmp_path / "does-not-exist.jsonl",
            output_dir=tmp_path / "output",
            limit=None,
        )


def test_explicit_zero_matrix_budget_is_not_replaced_by_default(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    example = EvaluationExample(
        example_id="zero-budget",
        subject="Information",
        body="Aucune opération.",
        expected_operations=(),
    )
    dataset.write_text(json.dumps(example.as_dict()) + "\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_matrix(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "budget_stopped"}

    monkeypatch.setattr("snoc_agent.cli.commands.run_persistent_matrix", fake_matrix)
    evaluate_model_matrix(
        Settings(),
        dataset=dataset,
        output_dir=tmp_path / "output",
        limit=1,
        budget_usd=Decimal("0"),
        stop_before_budget_usd=Decimal("0"),
        checkpoint_every=2,
        env_file=tmp_path / "custom.env",
    )

    assert captured["budget_usd"] == Decimal("0")
    resume_command = str(captured["resumable_command"])
    assert "--budget-usd 0" in resume_command
    assert "--stop-before-budget-usd 0" in resume_command
    assert "--checkpoint-every 2" in resume_command
    assert "--limit 1" in resume_command
    assert "--env-file" in resume_command
