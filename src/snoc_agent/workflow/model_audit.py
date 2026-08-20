"""Persist complete, hashed model invocations for traceability."""

from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy.orm import Session

from snoc_agent.ai.backend import StructuredGenerationResult
from snoc_agent.ai.errors import InferenceError
from snoc_agent.db.models import ModelRun


def persist_model_run(
    session: Session,
    *,
    result: StructuredGenerationResult,
    stage: str,
    prompt_version: str,
    input_context: dict[str, Any],
    email_message_id: uuid.UUID | None,
    operation_id: uuid.UUID | None = None,
    quantization: str | None = None,
    generation_settings: dict[str, Any] | None = None,
    cached_from_model_run_id: uuid.UUID | None = None,
) -> ModelRun:
    canonical = json.dumps(input_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    schema_canonical = json.dumps(
        result.json_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    settings = generation_settings or {}
    settings_canonical = json.dumps(
        settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    base_model = result.base_model_id or result.model_name
    run = ModelRun(
        email_message_id=email_message_id,
        operation_id=operation_id,
        stage=stage,
        backend=result.backend,
        model_family=base_model.rsplit("/", 1)[-1].split("-", 1)[0],
        model_name=result.model_name,
        base_model_id=base_model,
        resolved_model_id=result.resolved_model_id or result.model_name,
        requested_route=result.requested_route,
        reported_provider=result.reported_provider,
        provider_request_id=result.provider_request_id,
        quantization=quantization,
        prompt_version=prompt_version,
        input_context_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        input_context=input_context,
        raw_output=result.raw_output,
        parsed_output=result.parsed.model_dump(mode="json"),
        structured_output_valid=True,
        structured_output_mode=result.structured_output_mode,
        schema_name=result.schema_name,
        json_schema=result.json_schema,
        schema_hash=hashlib.sha256(schema_canonical.encode("utf-8")).hexdigest(),
        fallback_reason=result.fallback_reason,
        parse_attempt_count=result.parse_attempt_count,
        validation_errors=result.validation_errors,
        reasoning_output=result.reasoning_output,
        latency_seconds=result.latency_seconds,
        request_attempt_count=result.attempts,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        total_tokens=result.total_tokens,
        pricing_metadata=result.pricing_metadata,
        input_cost_usd=result.input_cost_usd,
        output_cost_usd=result.output_cost_usd,
        total_cost_usd=result.total_cost_usd,
        cost_basis=result.cost_basis,
        generation_settings=settings,
        generation_settings_hash=hashlib.sha256(settings_canonical.encode("utf-8")).hexdigest(),
        logprobs=result.logprobs,
        logprob_metrics=result.logprob_metrics,
        cached_from_model_run_id=cached_from_model_run_id,
    )
    session.add(run)
    session.flush()
    return run


def persist_failed_model_run(
    session: Session,
    *,
    stage: str,
    prompt_version: str,
    input_context: dict[str, Any],
    email_message_id: uuid.UUID | None,
    model_name: str,
    backend: str,
    error: str | Exception,
    operation_id: uuid.UUID | None = None,
    quantization: str | None = None,
    error_category: str | None = None,
    generation_settings: dict[str, Any] | None = None,
    base_model_id: str | None = None,
    resolved_model_id: str | None = None,
    requested_route: str | None = None,
    json_schema: dict[str, Any] | None = None,
    schema_name: str | None = None,
) -> ModelRun:
    canonical = json.dumps(input_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    settings = generation_settings or {}
    settings_canonical = json.dumps(
        settings, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    inferred_category = error_category
    inference_error = error if isinstance(error, InferenceError) else None
    if inferred_category is None and inference_error is not None:
        inferred_category = inference_error.category.value
    audited_schema = json_schema or (
        inference_error.json_schema if inference_error is not None else {}
    )
    schema_canonical = json.dumps(
        audited_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    structured_mode = inference_error.structured_output_mode if inference_error else None
    audited_schema_name = schema_name or (
        inference_error.schema_name if inference_error is not None else None
    )
    fallback_reason = inference_error.fallback_reason if inference_error else None
    parse_attempt_count = inference_error.parse_attempt_count if inference_error else 0
    validation_errors = inference_error.validation_errors if inference_error else []
    base_model = base_model_id or model_name
    reported_model = inference_error.model_name if inference_error else None
    run = ModelRun(
        email_message_id=email_message_id,
        operation_id=operation_id,
        stage=stage,
        backend=backend,
        model_family=base_model.rsplit("/", 1)[-1].split("-", 1)[0],
        model_name=reported_model or model_name,
        base_model_id=base_model,
        resolved_model_id=resolved_model_id or model_name,
        requested_route=requested_route or resolved_model_id or model_name,
        reported_provider=inference_error.reported_provider if inference_error else None,
        provider_request_id=inference_error.provider_request_id if inference_error else None,
        quantization=quantization,
        prompt_version=prompt_version,
        input_context_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        input_context=input_context,
        raw_output=inference_error.raw_output if inference_error else None,
        parsed_output={},
        structured_output_valid=False,
        structured_output_mode=structured_mode,
        schema_name=audited_schema_name,
        json_schema=audited_schema,
        schema_hash=(
            hashlib.sha256(schema_canonical.encode("utf-8")).hexdigest() if audited_schema else None
        ),
        fallback_reason=fallback_reason,
        parse_attempt_count=parse_attempt_count,
        validation_errors=validation_errors,
        reasoning_output=inference_error.reasoning_output if inference_error else None,
        latency_seconds=inference_error.latency_seconds if inference_error else None,
        request_attempt_count=(
            inference_error.transport_attempt_count if inference_error is not None else 0
        ),
        prompt_tokens=inference_error.prompt_tokens if inference_error else None,
        completion_tokens=inference_error.completion_tokens if inference_error else None,
        total_tokens=inference_error.total_tokens if inference_error else None,
        pricing_metadata=inference_error.pricing_metadata if inference_error else {},
        input_cost_usd=inference_error.input_cost_usd if inference_error else None,
        output_cost_usd=inference_error.output_cost_usd if inference_error else None,
        total_cost_usd=inference_error.total_cost_usd if inference_error else None,
        cost_basis=inference_error.cost_basis if inference_error else "unknown",
        generation_settings=settings,
        generation_settings_hash=hashlib.sha256(settings_canonical.encode("utf-8")).hexdigest(),
        logprobs=inference_error.logprobs if inference_error else {},
        logprob_metrics=inference_error.logprob_metrics if inference_error else {},
        error=str(error)[:4000],
        error_category=inferred_category,
    )
    session.add(run)
    session.flush()
    return run
