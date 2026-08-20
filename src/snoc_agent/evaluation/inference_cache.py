"""Persistent, schema-aware inference cache used by offline evaluation only."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from typing import Any, Literal, TypeVar

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from snoc_agent.ai.backend import (
    GenerationConfig,
    StructuredGenerationResult,
    safe_generation_settings,
)
from snoc_agent.ai.errors import InferenceError
from snoc_agent.ai.provider import StructuredOutputMode
from snoc_agent.db.base import utc_now
from snoc_agent.db.models import (
    EvaluationInference,
    InferenceCacheEntry,
    ModelRun,
)
from snoc_agent.db.session import SessionFactory, session_scope
from snoc_agent.workflow.model_audit import persist_failed_model_run, persist_model_run

ResponseT = TypeVar("ResponseT", bound=BaseModel)
CacheMode = Literal["use", "no_cache", "refresh"]


def canonical_hash(value: object) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _allowed_modes(config: GenerationConfig) -> list[str]:
    modes = [
        StructuredOutputMode.JSON_SCHEMA.value
        if config.use_json_schema
        else StructuredOutputMode.JSON_OBJECT.value
    ]
    if config.use_json_schema and config.allow_json_object_fallback:
        modes.append(StructuredOutputMode.JSON_OBJECT.value)
    if config.allow_prompt_json_fallback:
        modes.append(StructuredOutputMode.PROMPT_JSON.value)
    return list(dict.fromkeys(modes))


def cache_key(
    *,
    stage: str,
    base_model_id: str,
    resolved_model_id: str,
    prompt_version: str,
    structured_output_mode: str,
    settings_hash: str,
    context_hash: str,
    schema_hash: str,
) -> str:
    return canonical_hash(
        {
            "stage": stage,
            "base_model_id": base_model_id,
            "resolved_model_id": resolved_model_id,
            "prompt_version": prompt_version,
            "structured_output_mode": structured_output_mode,
            "generation_settings_hash": settings_hash,
            "normalized_input_context_hash": context_hash,
            "schema_hash": schema_hash,
        }
    )


def _result_from_run(run: ModelRun, response_model: type[BaseModel]) -> StructuredGenerationResult:
    return StructuredGenerationResult(
        parsed=response_model.model_validate(run.parsed_output),
        raw_output=run.raw_output or json.dumps(run.parsed_output, ensure_ascii=False),
        model_name=run.model_name,
        backend=run.backend,
        latency_seconds=run.latency_seconds or 0.0,
        prompt_tokens=run.prompt_tokens,
        completion_tokens=run.completion_tokens,
        total_tokens=run.total_tokens,
        logprobs=run.logprobs,
        logprob_metrics={
            str(key): value
            for key, value in run.logprob_metrics.items()
            if isinstance(value, int | float)
        },
        attempts=0,
        base_model_id=run.base_model_id,
        resolved_model_id=run.resolved_model_id,
        requested_route=run.requested_route,
        reported_provider=run.reported_provider,
        provider_request_id=run.provider_request_id,
        structured_output_mode=run.structured_output_mode or "unknown",
        json_schema=run.json_schema,
        schema_name=run.schema_name,
        fallback_reason=run.fallback_reason,
        parse_attempt_count=run.parse_attempt_count,
        validation_errors=run.validation_errors,
        reasoning_output=run.reasoning_output,
        pricing_metadata=run.pricing_metadata,
        input_cost_usd=run.input_cost_usd,
        output_cost_usd=run.output_cost_usd,
        total_cost_usd=run.total_cost_usd,
        cost_basis=run.cost_basis,
        cache_hit=True,
        original_model_run_id=str(run.id),
    )


class PersistentInferenceCache:
    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        evaluation_run_id: uuid.UUID,
        cache_mode: CacheMode,
        resume: bool,
        provider_namespace: str = "unspecified",
        backend_name: str = "evaluation",
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.evaluation_run_id = evaluation_run_id
        self.cache_mode = cache_mode
        self.resume = resume
        self.provider_namespace = provider_namespace
        self.backend_name = backend_name
        self.on_progress = on_progress

    def _completed_run(
        self,
        *,
        example_id: str,
        stage: str,
        base_model_id: str,
        proposal_hash: str,
        allowed_keys: list[str],
        prompt_version: str,
        resolved_model_id: str,
        context_hash: str,
        schema_hash: str,
        generation_settings_hash: str,
    ) -> ModelRun | None:
        if not self.resume:
            return None
        with session_scope(self.session_factory) as session:
            inference = session.scalar(
                select(EvaluationInference).where(
                    EvaluationInference.evaluation_run_id == self.evaluation_run_id,
                    EvaluationInference.example_id == example_id,
                    EvaluationInference.stage == stage,
                    EvaluationInference.base_model_id == base_model_id,
                    EvaluationInference.proposal_hash == proposal_hash,
                    EvaluationInference.status == "complete",
                )
            )
            if inference is None or inference.inference_key not in allowed_keys:
                return None
            run = session.get(ModelRun, inference.model_run_id)
            if run is None:
                return None
            if (
                run.prompt_version != prompt_version
                or run.resolved_model_id != resolved_model_id
                or run.input_context_hash != context_hash
                or run.schema_hash != schema_hash
                or run.generation_settings_hash != generation_settings_hash
            ):
                return None
            return run

    def _global_cached_run(self, keys: list[str]) -> tuple[ModelRun, str] | None:
        if self.cache_mode != "use":
            return None
        with session_scope(self.session_factory) as session:
            for key in keys:
                entry = session.get(InferenceCacheEntry, key)
                if entry is None:
                    continue
                run = session.get(ModelRun, entry.model_run_id)
                if run is None or not run.structured_output_valid:
                    continue
                entry.hit_count += 1
                entry.last_used_at = utc_now()
                return run, key
        return None

    @staticmethod
    def _upsert_inference(
        session: Session,
        *,
        evaluation_run_id: uuid.UUID,
        example_id: str,
        stage: str,
        analyzer_source_model_id: str | None,
        base_model_id: str,
        proposal_hash: str,
        inference_key: str,
        model_run_id: uuid.UUID | None,
        cache_hit: bool,
        status: str,
        error_category: str | None = None,
    ) -> EvaluationInference:
        existing = session.scalar(
            select(EvaluationInference).where(
                EvaluationInference.evaluation_run_id == evaluation_run_id,
                EvaluationInference.example_id == example_id,
                EvaluationInference.stage == stage,
                EvaluationInference.base_model_id == base_model_id,
                EvaluationInference.proposal_hash == proposal_hash,
            )
        )
        if existing is None:
            existing = EvaluationInference(
                evaluation_run_id=evaluation_run_id,
                example_id=example_id,
                stage=stage,
                analyzer_source_model_id=analyzer_source_model_id,
                base_model_id=base_model_id,
                proposal_hash=proposal_hash,
            )
            session.add(existing)
        existing.inference_key = inference_key
        existing.model_run_id = model_run_id
        if model_run_id is not None and not cache_hit:
            attempts = list(existing.attempt_model_run_ids or [])
            value = str(model_run_id)
            if value not in attempts:
                attempts.append(value)
            existing.attempt_model_run_ids = attempts
        existing.cache_hit = cache_hit
        existing.status = status
        existing.error_category = error_category
        return existing

    def _record_inference(self, **values: Any) -> None:
        with session_scope(self.session_factory) as session:
            self._upsert_inference(
                session,
                evaluation_run_id=self.evaluation_run_id,
                **values,
            )

    def get_or_run(
        self,
        *,
        example_id: str,
        stage: str,
        analyzer_source_model_id: str | None,
        prompt_version: str,
        input_context: dict[str, Any],
        response_model: type[ResponseT],
        config: GenerationConfig,
        proposal_hash: str = "",
        invoke: Callable[[], StructuredGenerationResult],
    ) -> StructuredGenerationResult:
        base_model = config.base_model or config.model
        context_hash = canonical_hash(input_context)
        schema = response_model.model_json_schema()
        schema_hash = canonical_hash(schema)
        settings = safe_generation_settings(config)
        settings["provider_namespace"] = self.provider_namespace
        settings_hash = canonical_hash(settings)
        keys = [
            cache_key(
                stage=stage,
                base_model_id=base_model,
                resolved_model_id=config.model,
                prompt_version=prompt_version,
                structured_output_mode=mode,
                settings_hash=settings_hash,
                context_hash=context_hash,
                schema_hash=schema_hash,
            )
            for mode in _allowed_modes(config)
        ]
        resumed = self._completed_run(
            example_id=example_id,
            stage=stage,
            base_model_id=base_model,
            proposal_hash=proposal_hash,
            allowed_keys=keys,
            prompt_version=prompt_version,
            resolved_model_id=config.model,
            context_hash=context_hash,
            schema_hash=schema_hash,
            generation_settings_hash=settings_hash,
        )
        if resumed is not None:
            return _result_from_run(resumed, response_model)
        cached = self._global_cached_run(keys)
        if cached is not None:
            run, _key = cached
            self._record_inference(
                example_id=example_id,
                stage=stage,
                analyzer_source_model_id=analyzer_source_model_id,
                base_model_id=base_model,
                proposal_hash=proposal_hash,
                inference_key=_key,
                model_run_id=run.id,
                cache_hit=True,
                status="complete",
            )
            if self.on_progress:
                self.on_progress()
            return _result_from_run(run, response_model)

        try:
            result = invoke()
        except Exception as exc:
            category = exc.category.value if isinstance(exc, InferenceError) else "unknown"
            if (
                category == "budget_exhausted"
                and isinstance(exc, InferenceError)
                and exc.transport_attempt_count == 0
            ):
                # No provider response exists when the preflight budget guard
                # stops a call.  Keep the resumable inference marker, but do
                # not manufacture a failed model run for an inference that was
                # never sent.
                self._record_inference(
                    example_id=example_id,
                    stage=stage,
                    analyzer_source_model_id=analyzer_source_model_id,
                    base_model_id=base_model,
                    proposal_hash=proposal_hash,
                    inference_key=keys[0],
                    model_run_id=None,
                    cache_hit=False,
                    status="paused",
                    error_category=category,
                )
                if self.on_progress:
                    self.on_progress()
                raise
            failed_key = cache_key(
                stage=stage,
                base_model_id=base_model,
                resolved_model_id=config.model,
                prompt_version=prompt_version,
                structured_output_mode=(
                    exc.structured_output_mode
                    if isinstance(exc, InferenceError) and exc.structured_output_mode
                    else _allowed_modes(config)[0]
                ),
                settings_hash=settings_hash,
                context_hash=context_hash,
                schema_hash=schema_hash,
            )
            with session_scope(self.session_factory) as session:
                failed = persist_failed_model_run(
                    session,
                    stage=stage,
                    prompt_version=prompt_version,
                    input_context=input_context,
                    email_message_id=None,
                    model_name=config.model,
                    backend=self.backend_name,
                    error=exc,
                    error_category=category,
                    generation_settings=settings,
                    base_model_id=base_model,
                    resolved_model_id=config.model,
                    requested_route=config.model,
                    json_schema=schema,
                    schema_name=response_model.__name__,
                )
                self._upsert_inference(
                    session,
                    evaluation_run_id=self.evaluation_run_id,
                    example_id=example_id,
                    stage=stage,
                    analyzer_source_model_id=analyzer_source_model_id,
                    base_model_id=base_model,
                    proposal_hash=proposal_hash,
                    inference_key=failed_key,
                    model_run_id=failed.id,
                    cache_hit=False,
                    status="error",
                    error_category=category,
                )
            if self.on_progress:
                self.on_progress()
            raise

        with session_scope(self.session_factory) as session:
            run = persist_model_run(
                session,
                result=result,
                stage=stage,
                prompt_version=prompt_version,
                input_context=input_context,
                email_message_id=None,
                quantization=config.quantization,
                generation_settings=settings,
            )
            if self.cache_mode != "no_cache":
                actual_key = cache_key(
                    stage=stage,
                    base_model_id=base_model,
                    resolved_model_id=config.model,
                    prompt_version=prompt_version,
                    structured_output_mode=result.structured_output_mode,
                    settings_hash=settings_hash,
                    context_hash=context_hash,
                    schema_hash=schema_hash,
                )
                if self.cache_mode == "refresh":
                    for stale_key in keys:
                        if stale_key == actual_key:
                            continue
                        stale_entry = session.get(InferenceCacheEntry, stale_key)
                        if stale_entry is not None:
                            session.delete(stale_entry)
                entry = session.get(InferenceCacheEntry, actual_key)
                if entry is None:
                    entry = InferenceCacheEntry(
                        cache_key=actual_key,
                        model_run_id=run.id,
                        stage=stage,
                        base_model_id=base_model,
                        resolved_model_id=config.model,
                        prompt_version=prompt_version,
                        structured_output_mode=result.structured_output_mode,
                        context_hash=context_hash,
                        schema_hash=schema_hash,
                        generation_settings_hash=settings_hash,
                    )
                    session.add(entry)
                else:
                    entry.model_run_id = run.id
                    entry.last_used_at = utc_now()
            actual_key = cache_key(
                stage=stage,
                base_model_id=base_model,
                resolved_model_id=config.model,
                prompt_version=prompt_version,
                structured_output_mode=result.structured_output_mode,
                settings_hash=settings_hash,
                context_hash=context_hash,
                schema_hash=schema_hash,
            )
            self._upsert_inference(
                session,
                evaluation_run_id=self.evaluation_run_id,
                example_id=example_id,
                stage=stage,
                analyzer_source_model_id=analyzer_source_model_id,
                base_model_id=base_model,
                proposal_hash=proposal_hash,
                inference_key=actual_key,
                model_run_id=run.id,
                cache_hit=False,
                status="complete",
            )
        result.original_model_run_id = str(run.id)
        if self.on_progress:
            self.on_progress()
        return result
