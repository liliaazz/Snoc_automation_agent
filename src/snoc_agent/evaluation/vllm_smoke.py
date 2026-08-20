"""Fake-data-only Qwen/Gemma vLLM compatibility workflow."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from snoc_agent.ai.backend import LLMBackend, safe_generation_settings
from snoc_agent.ai.intent_safety import apply_intent_safety
from snoc_agent.ai.provider import LLMProvider
from snoc_agent.ai.schemas import EmailAnalysis, SemanticVerification
from snoc_agent.ai.vllm_deployments import VLLMModelCatalog, resolve_vllm_deployment
from snoc_agent.cli.runtime import build_model_services
from snoc_agent.config import Settings
from snoc_agent.db import create_engine_and_session, create_schema
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.evaluation.dataset_subsets import synthetic_smoke_examples
from snoc_agent.evaluation.pipeline_predictor import (
    evaluation_context,
    evaluation_context_builder,
    evaluation_verifier_inputs,
    materialize_prediction,
)
from snoc_agent.evaluation.smoke_utils import (
    _persist_failed_smoke_call,
    _result_audit,
    _safe_error_message,
    _write_smoke_report,
)
from snoc_agent.workflow.model_audit import persist_model_run


def run_vllm_smoke_test(
    settings: Settings,
    *,
    analyzer_model: str,
    verifier_model: str,
    output_dir: Path,
) -> dict[str, Any]:
    """Run the production analyzer/verifier contracts without mail or business I/O."""

    token = settings.effective_vllm_api_key
    report_path = output_dir / "smoke_report.json"
    rows: list[dict[str, Any]] = []
    model_calls: list[Any] = []
    analyzer_backend: LLMBackend | None = None
    verifier_backend: LLMBackend | None = None
    catalog: VLLMModelCatalog | None = None
    engine: Engine | None = None
    session_factory: SessionFactory | None = None
    active_case_id: str | None = None
    active_stage = "initialization"
    analyzer_deployment = resolve_vllm_deployment(analyzer_model, settings.vllm_deployments)
    verifier_deployment = resolve_vllm_deployment(verifier_model, settings.vllm_deployments)

    def payload(*, status: str, error: BaseException | None = None) -> dict[str, Any]:
        return {
            "mode": "vllm_smoke_test",
            "status": status,
            "failure": (
                {
                    "stage": active_stage,
                    "example_id": active_case_id,
                    "error_type": type(error).__name__,
                    "message": _safe_error_message(error, token=token),
                }
                if error is not None
                else None
            ),
            "dry_run": True,
            "external_side_effects": {"imap": False, "smtp": False, "business_api": False},
            "analyzer": {
                "deployment": analyzer_deployment.name.value,
                "model": analyzer_deployment.model_id,
            },
            "verifier": {
                "deployment": verifier_deployment.name.value,
                "model": verifier_deployment.model_id,
            },
            "completed_case_count": len(rows),
            "total_requests": sum(result.attempts for result in model_calls),
            "usage": {
                "prompt_tokens": sum(result.prompt_tokens or 0 for result in model_calls),
                "completion_tokens": sum(result.completion_tokens or 0 for result in model_calls),
                "total_tokens": sum(result.total_tokens or 0 for result in model_calls),
                "cost_usd": None,
                "cost_basis": "unknown",
            },
            "cases": rows,
            "report_path": str(report_path),
        }

    try:
        if not token:
            raise ValueError("VLLM_API_KEY is required for the vLLM smoke test")
        active_stage = "model_discovery"
        selected_deployments = tuple(
            {
                deployment.name: deployment
                for deployment in (analyzer_deployment, verifier_deployment)
            }.values()
        )
        catalog = VLLMModelCatalog(
            deployments=selected_deployments,
            api_key=token,
            timeout_seconds=settings.vllm_request_timeout_seconds,
        )
        catalog.check_exact_models()
        smoke_settings = settings.model_copy(
            update={
                "llm_provider": LLMProvider.VLLM,
                "dry_run": True,
                "vllm_analyzer_deployment": analyzer_deployment.name,
                "vllm_verifier_deployment": verifier_deployment.name,
            }
        )
        active_stage = "service_initialization"
        analyzer_backend, verifier_backend, analyzer, verifier = build_model_services(
            smoke_settings,
            analyzer_model=analyzer_deployment.name.value,
            verifier_model=verifier_deployment.name.value,
        )
        engine, session_factory = create_engine_and_session(smoke_settings.database_url)
        create_schema(engine)
        context_builder = evaluation_context_builder(smoke_settings)
        for example in synthetic_smoke_examples():
            active_case_id = example.example_id
            active_stage = "smoke_analysis"
            context, _candidates = evaluation_context(example, context_builder=context_builder)
            try:
                analyzer_result = analyzer.analyze(context)
                if not isinstance(analyzer_result.parsed, EmailAnalysis):
                    raise TypeError("smoke analyzer returned the wrong Pydantic schema")
            except Exception as error:
                _persist_failed_smoke_call(
                    session_factory,
                    error=error,
                    stage=active_stage,
                    prompt_version=analyzer.prompt_version,
                    input_context=context,
                    config=analyzer.config,
                    response_model=EmailAnalysis,
                    backend_name=LLMProvider.VLLM.value,
                    token=token,
                )
                raise
            model_calls.append(analyzer_result)
            with session_scope(session_factory) as session:
                persist_model_run(
                    session,
                    result=analyzer_result,
                    stage=active_stage,
                    prompt_version=analyzer.prompt_version,
                    input_context=context,
                    email_message_id=None,
                    generation_settings=safe_generation_settings(analyzer.config),
                )
            safety = apply_intent_safety(
                analyzer_result.parsed,
                str(context.get("latest_user_message", "")),
            )
            guarded_analyzer_result = replace(analyzer_result, parsed=safety.analysis)
            verifier_results = []
            verifier_audits = []
            for proposal in safety.analysis.operations:
                active_stage = "smoke_verification"
                verifier_inputs = evaluation_verifier_inputs(
                    example,
                    proposal,
                    context_builder=context_builder,
                )
                verifier_payload = verifier.input_payload(
                    proposed_operation=proposal,
                    **verifier_inputs,
                )
                try:
                    result = verifier.verify(proposed_operation=proposal, **verifier_inputs)
                    if not isinstance(result.parsed, SemanticVerification):
                        raise TypeError("smoke verifier returned the wrong Pydantic schema")
                except Exception as error:
                    _persist_failed_smoke_call(
                        session_factory,
                        error=error,
                        stage=active_stage,
                        prompt_version=verifier.prompt_version,
                        input_context=verifier_payload,
                        config=verifier.config,
                        response_model=SemanticVerification,
                        backend_name=LLMProvider.VLLM.value,
                        token=token,
                    )
                    raise
                model_calls.append(result)
                with session_scope(session_factory) as session:
                    persist_model_run(
                        session,
                        result=result,
                        stage=active_stage,
                        prompt_version=verifier.prompt_version,
                        input_context=verifier_payload,
                        email_message_id=None,
                        generation_settings=safe_generation_settings(verifier.config),
                    )
                verifier_results.append(result)
                verifier_audits.append(_result_audit(result))
            rows.append(
                {
                    "example_id": example.example_id,
                    "subject": example.subject,
                    "body": example.body,
                    "expected": example.as_dict(),
                    "analyzer": _result_audit(analyzer_result),
                    "intent_safety": {
                        "blocked_operation_ids": list(safety.blocked_operation_ids),
                        "reasons": list(safety.reasons),
                    },
                    "verifiers": verifier_audits,
                    "prediction": materialize_prediction(
                        example,
                        analyzer_result=guarded_analyzer_result,
                        verifier_results=verifier_results,
                        context_builder=context_builder,
                    ),
                }
            )
            active_case_id = None
            active_stage = "completed_case"
        report = payload(status="completed")
        _write_smoke_report(report_path, report)
        return report
    except BaseException as error:
        _write_smoke_report(report_path, payload(status="failed", error=error))
        raise
    finally:
        for backend in (analyzer_backend, verifier_backend):
            if backend is not None:
                close = getattr(backend, "close", None)
                if callable(close):
                    close()
        if catalog is not None:
            catalog.close()
        if engine is not None:
            engine.dispose()
