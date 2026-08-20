"""Shared persistence and reporting helpers for inference smoke tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from snoc_agent.ai.backend import GenerationConfig, safe_generation_settings
from snoc_agent.ai.errors import InferenceError, InferenceErrorCategory
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.workflow.model_audit import persist_failed_model_run


def _error_category(error: BaseException) -> str:
    if isinstance(error, InferenceError):
        return error.category.value
    if isinstance(error, TypeError):
        return InferenceErrorCategory.MALFORMED_OUTPUT.value
    detail = str(error).casefold()
    if isinstance(error, ValueError) and "vllm_api_key" in detail:
        return InferenceErrorCategory.AUTHENTICATION.value
    if isinstance(error, ValueError) and "unavailable" in detail:
        return InferenceErrorCategory.MODEL_UNAVAILABLE.value
    return InferenceErrorCategory.UNKNOWN.value


def _safe_error_message(error: BaseException, *, token: str) -> str:
    detail = " ".join(str(error).split())[:1000]
    return detail.replace(token, "[REDACTED]") if token else detail


def _write_smoke_report(report_path: Path, payload: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = report_path.with_name(f".{report_path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(report_path)


def _persist_failed_smoke_call(
    session_factory: SessionFactory,
    *,
    error: Exception,
    stage: str,
    prompt_version: str,
    input_context: dict[str, Any],
    config: GenerationConfig,
    response_model: type[BaseModel],
    backend_name: str,
    token: str,
) -> None:
    with session_scope(session_factory) as session:
        run = persist_failed_model_run(
            session,
            stage=stage,
            prompt_version=prompt_version,
            input_context=input_context,
            email_message_id=None,
            model_name=config.model,
            base_model_id=config.base_model,
            resolved_model_id=config.model,
            requested_route=config.model,
            backend=backend_name,
            error=error,
            error_category=_error_category(error),
            quantization=config.quantization,
            generation_settings=safe_generation_settings(config),
            json_schema=response_model.model_json_schema(),
            schema_name=response_model.__name__,
        )
        run.error = _safe_error_message(error, token=token)


def _result_audit(result: Any) -> dict[str, Any]:
    return {
        "parsed": result.parsed.model_dump(mode="json"),
        "base_model_id": result.base_model_id,
        "resolved_model_id": result.resolved_model_id,
        "reported_provider": result.reported_provider,
        "structured_output_mode": result.structured_output_mode,
        "schema_guaranteed": result.structured_output_mode == "json_schema",
        "fallback_reason": result.fallback_reason,
        "parse_attempt_count": result.parse_attempt_count,
        "latency_ms": round(result.latency_seconds * 1000, 2),
        "reasoning_returned": result.reasoning_output is not None,
        "logprob_metrics": result.logprob_metrics,
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.total_tokens,
        },
        "cost": {
            "input_usd": str(result.input_cost_usd) if result.input_cost_usd is not None else None,
            "output_usd": str(result.output_cost_usd)
            if result.output_cost_usd is not None
            else None,
            "total_usd": str(result.total_cost_usd) if result.total_cost_usd is not None else None,
            "basis": result.cost_basis,
        },
    }
