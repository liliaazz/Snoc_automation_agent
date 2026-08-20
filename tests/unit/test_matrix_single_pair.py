from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from snoc_agent.ai.backend import StructuredGenerationResult
from snoc_agent.ai.model_registry import ModelPair
from snoc_agent.ai.schemas import EmailAnalysis
from snoc_agent.cli.commands import evaluate_model_matrix
from snoc_agent.cli.runtime import build_model_services, build_runtime
from snoc_agent.config import Settings
from snoc_agent.db import create_engine_and_session, create_schema
from snoc_agent.db.models import EvaluationInference, EvaluationRun, ModelRun
from snoc_agent.db.session import session_scope
from snoc_agent.evaluation.dataset_loader import EvaluationExample
from snoc_agent.evaluation.matrix_runner import _restored_budget, run_persistent_matrix
from snoc_agent.evaluation.pipeline_predictor import materialize_prediction
from snoc_agent.mail.fake_mailbox import FakeSMTPTransport
from snoc_agent.mail.smtp_client import RealSMTPTransport


def _irrelevant_analysis_result(*, total_cost_usd: Decimal | None) -> StructuredGenerationResult:
    parsed = EmailAnalysis(
        message_kind="irrelevant",
        referenced_existing_operation_ids=[],
        operations=[],
        new_request_present=False,
        contradiction_with_stored_state=False,
        contradiction_details=[],
        unresolved_ambiguities=[],
    )
    return StructuredGenerationResult(
        parsed=parsed,
        raw_output=parsed.model_dump_json(),
        model_name="demo:model-a",
        backend="deterministic_demo",
        latency_seconds=0,
        base_model_id="model-a",
        resolved_model_id="model-a",
        requested_route="model-a",
        json_schema=EmailAnalysis.model_json_schema(),
        schema_name=EmailAnalysis.__name__,
        total_cost_usd=total_cost_usd,
        cost_basis="estimated" if total_cost_usd is not None else "unknown",
    )


def test_single_pair_supports_distinct_analyzer_and_verifier_models(tmp_path) -> None:
    dataset = tmp_path / "dataset.jsonl"
    example = EvaluationExample(
        example_id="distinct-models",
        subject="Information",
        body="Aucune demande opérationnelle.",
        expected_operations=(),
        expected_outcome="irrelevant",
    )
    dataset.write_text(json.dumps(example.as_dict()) + "\n", encoding="utf-8")

    result = run_persistent_matrix(
        Settings(database_url=f"sqlite:///{tmp_path / 'matrix.db'}"),
        dataset_path=dataset,
        examples=[example],
        output_dir=tmp_path / "output",
        cache_mode="no_cache",
        resume=False,
        budget_usd=Decimal("2"),
        checkpoint_every=1,
        resumable_command="resume",
        model_pairs={"selected_pair": ModelPair("selected_pair", "model-a", "model-b")},
    )

    assert result["examples_per_run"] == 1
    assert result["runs"].keys() == {"selected_pair"}


def test_vllm_runtime_requires_an_api_key_even_in_dry_run() -> None:
    settings = Settings(
        llm_provider="vllm",
        vllm_api_key="",
        llm_api_key="",
        dry_run=True,
    )

    try:
        build_model_services(settings)
    except ValueError as error:
        assert "VLLM_API_KEY is required" in str(error)
    else:
        raise AssertionError("vLLM service construction must fail without a token")


def test_dry_run_email_delivery_requires_explicit_opt_in(tmp_path) -> None:
    common = {
        "_env_file": None,
        "database_url": f"sqlite:///{tmp_path / 'runtime.db'}",
        "dry_run": True,
        "smtp_host": "smtp.example.invalid",
        "smtp_username": "tester@example.invalid",
        "smtp_password": "not-a-real-secret",
    }

    safe_runtime = build_runtime(Settings(**common), initialize_schema=True)
    live_mail_runtime = build_runtime(
        Settings(**common, dry_run_send_emails=True), initialize_schema=True
    )

    assert isinstance(safe_runtime.smtp_transport, FakeSMTPTransport)
    assert isinstance(live_mail_runtime.smtp_transport, RealSMTPTransport)
    assert safe_runtime.business_api.__class__.__name__ == "MockBusinessAPI"
    assert live_mail_runtime.business_api.__class__.__name__ == "MockBusinessAPI"


def test_prediction_does_not_turn_unknown_new_cost_into_zero() -> None:
    example = EvaluationExample(
        example_id="unknown-cost",
        subject="Information",
        body="Aucune demande opérationnelle.",
        expected_operations=(),
        expected_outcome="irrelevant",
    )

    prediction = materialize_prediction(
        example,
        analyzer_result=_irrelevant_analysis_result(total_cost_usd=None),
        verifier_results=[],
    )

    assert prediction["total_cost_usd"] is None
    assert prediction["incremental_total_cost_usd"] is None
    assert prediction["incremental_cost_known"] is False


def test_all_cached_results_have_zero_incremental_cost() -> None:
    example = EvaluationExample(
        example_id="cached-cost",
        subject="Information",
        body="Aucune demande opérationnelle.",
        expected_operations=(),
        expected_outcome="irrelevant",
    )
    result = _irrelevant_analysis_result(total_cost_usd=Decimal("0.001"))
    result.cache_hit = True

    prediction = materialize_prediction(
        example,
        analyzer_result=result,
        verifier_results=[],
    )

    assert prediction["total_cost_usd"] == "0.001"
    assert prediction["incremental_total_cost_usd"] == "0"
    assert prediction["incremental_cost_known"] is True


def test_resume_rebuilds_budget_from_linked_attempts_after_stale_checkpoint(tmp_path) -> None:
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'resume.db'}")
    create_schema(engine)
    evaluation_run_id = uuid.uuid4()
    model_run_id = uuid.uuid4()
    with session_scope(session_factory) as session:
        session.add(
            EvaluationRun(
                id=evaluation_run_id,
                status="paused",
                dataset_path="dataset.jsonl",
                dataset_hash="d" * 64,
                configuration_hash="c" * 64,
                configuration={},
                output_dir="output",
                budget_usd=Decimal("2"),
                stop_before_budget_usd=Decimal("1.9"),
                cost_so_far_usd=Decimal("0"),
                budget_status="within_budget",
                request_count=0,
                unknown_cost_request_count=0,
                prompt_tokens=0,
                completion_tokens=0,
                checkpoint_row=0,
            )
        )
        session.add(
            ModelRun(
                id=model_run_id,
                stage="analysis",
                backend="vllm",
                model_name="Qwen/Qwen2.5-7B-Instruct-AWQ-AWQ",
                base_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
                resolved_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ-AWQ",
                prompt_version="analyzer_v1",
                input_context_hash="i" * 64,
                input_context={},
                structured_output_valid=True,
                request_attempt_count=2,
                prompt_tokens=20,
                completion_tokens=10,
                total_tokens=30,
                total_cost_usd=Decimal("1.25"),
                cost_basis="provider_reported",
            )
        )
        session.add(
            EvaluationInference(
                evaluation_run_id=evaluation_run_id,
                example_id="example-1",
                stage="analysis",
                base_model_id="Qwen/Qwen2.5-7B-Instruct-AWQ",
                proposal_hash="",
                model_run_id=model_run_id,
                attempt_model_run_ids=[str(model_run_id)],
                cache_hit=False,
                status="complete",
            )
        )

    with session_scope(session_factory) as session:
        stored_run = session.scalar(
            select(EvaluationRun).where(EvaluationRun.id == evaluation_run_id)
        )
        assert stored_run is not None
        restored = _restored_budget(
            session_factory,
            stored_run,
            budget_usd=Decimal("2"),
            stop_before_usd=Decimal("1.9"),
            allow_unknown_cost=True,
        )

    assert restored.cost_so_far_usd == Decimal("1.25")
    assert restored.request_count == 2
    assert restored.prompt_tokens == 20
    assert restored.completion_tokens == 10
    engine.dispose()


def test_resume_counts_failed_local_attempt_but_not_later_cached_source(tmp_path) -> None:
    engine, session_factory = create_engine_and_session(f"sqlite:///{tmp_path / 'mixed.db'}")
    create_schema(engine)
    evaluation_run_id = uuid.uuid4()
    failed_run_id = uuid.uuid4()
    cached_source_id = uuid.uuid4()
    with session_scope(session_factory) as session:
        evaluation_run = EvaluationRun(
            id=evaluation_run_id,
            status="paused",
            dataset_path="dataset.jsonl",
            dataset_hash="d" * 64,
            configuration_hash="c" * 64,
            configuration={},
            output_dir="output",
            budget_usd=Decimal("2"),
            stop_before_budget_usd=Decimal("1.9"),
            cost_so_far_usd=Decimal("0"),
            budget_status="within_budget",
            request_count=0,
            unknown_cost_request_count=0,
            prompt_tokens=0,
            completion_tokens=0,
            checkpoint_row=0,
        )
        session.add(evaluation_run)
        for model_run_id, cost, attempts in (
            (failed_run_id, Decimal("0.25"), 1),
            (cached_source_id, Decimal("1.00"), 3),
        ):
            session.add(
                ModelRun(
                    id=model_run_id,
                    stage="analysis",
                    backend="vllm",
                    model_name="stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
                    base_model_id="stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
                    resolved_model_id="stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
                    prompt_version="analyzer_v1",
                    input_context_hash="i" * 64,
                    input_context={},
                    structured_output_valid=model_run_id == cached_source_id,
                    request_attempt_count=attempts,
                    prompt_tokens=10 * attempts,
                    completion_tokens=5 * attempts,
                    total_tokens=15 * attempts,
                    total_cost_usd=cost,
                    cost_basis="provider_reported",
                )
            )
        session.add(
            EvaluationInference(
                evaluation_run_id=evaluation_run_id,
                example_id="example-1",
                stage="analysis",
                base_model_id="stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
                proposal_hash="",
                model_run_id=cached_source_id,
                # Include both IDs to emulate an older row written before cached
                # sources were separated from locally incurred attempts.
                attempt_model_run_ids=[str(failed_run_id), str(cached_source_id)],
                cache_hit=True,
                status="complete",
            )
        )

    restored = _restored_budget(
        session_factory,
        evaluation_run,
        budget_usd=Decimal("2"),
        stop_before_usd=Decimal("1.9"),
        allow_unknown_cost=True,
    )

    assert restored.cost_so_far_usd == Decimal("0.25")
    assert restored.request_count == 1
    assert restored.prompt_tokens == 10
    assert restored.completion_tokens == 5
    engine.dispose()


@pytest.mark.parametrize("budget", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_evaluation_rejects_invalid_cli_budget_before_loading_dataset(
    tmp_path, budget: Decimal
) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        evaluate_model_matrix(
            Settings(),
            dataset=tmp_path / "missing.jsonl",
            output_dir=tmp_path / "output",
            limit=None,
            budget_usd=budget,
        )


def test_small_cli_budget_writes_effective_95_percent_stop_to_resume_command(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = tmp_path / "dataset.jsonl"
    example = EvaluationExample(
        example_id="small-budget",
        subject="Information",
        body="Aucune demande opérationnelle.",
        expected_operations=(),
        expected_outcome="irrelevant",
    )
    dataset.write_text(json.dumps(example.as_dict()) + "\n", encoding="utf-8")
    captured: dict[str, object] = {}

    def fake_runner(*_args: object, **kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "captured"}

    monkeypatch.setattr("snoc_agent.cli.commands.run_persistent_matrix", fake_runner)

    evaluate_model_matrix(
        Settings(),
        dataset=dataset,
        output_dir=tmp_path / "output",
        limit=1,
        budget_usd=Decimal("2"),
    )

    assert captured["budget_usd"] == Decimal("2")
    assert "--stop-before-budget-usd 1.90" in str(captured["resumable_command"])
