"""Classified inference failures and retry policy helpers."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any


class InferenceErrorCategory(StrEnum):
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_REQUEST = "invalid_request"
    STRUCTURED_OUTPUT_UNSUPPORTED = "structured_output_unsupported"
    MALFORMED_OUTPUT = "malformed_output"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNKNOWN = "unknown"


class InferenceError(RuntimeError):
    def __init__(
        self,
        category: InferenceErrorCategory,
        message: str,
        *,
        status_code: int | None = None,
        retry_after_seconds: float | None = None,
        structured_output_mode: str | None = None,
        json_schema: dict[str, Any] | None = None,
        schema_name: str | None = None,
        fallback_reason: str | None = None,
        parse_attempt_count: int = 0,
        validation_errors: list[dict[str, Any]] | None = None,
        raw_output: str | None = None,
        reasoning_output: str | None = None,
        model_name: str | None = None,
        reported_provider: str | None = None,
        provider_request_id: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
        pricing_metadata: dict[str, Any] | None = None,
        input_cost_usd: Decimal | None = None,
        output_cost_usd: Decimal | None = None,
        total_cost_usd: Decimal | None = None,
        cost_basis: str = "unknown",
        latency_seconds: float | None = None,
        logprobs: dict[str, Any] | None = None,
        logprob_metrics: dict[str, float | int] | None = None,
        transport_attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.structured_output_mode = structured_output_mode
        self.json_schema = json_schema or {}
        self.schema_name = schema_name
        self.fallback_reason = fallback_reason
        self.parse_attempt_count = parse_attempt_count
        self.validation_errors = validation_errors or []
        self.raw_output = raw_output
        self.reasoning_output = reasoning_output
        self.model_name = model_name
        self.reported_provider = reported_provider
        self.provider_request_id = provider_request_id
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.pricing_metadata = pricing_metadata or {}
        self.input_cost_usd = input_cost_usd
        self.output_cost_usd = output_cost_usd
        self.total_cost_usd = total_cost_usd
        self.cost_basis = cost_basis
        self.latency_seconds = latency_seconds
        self.logprobs = logprobs or {}
        self.logprob_metrics = logprob_metrics or {}
        self.transport_attempt_count = transport_attempt_count

    @property
    def retryable(self) -> bool:
        return self.category in {
            InferenceErrorCategory.RATE_LIMIT,
            InferenceErrorCategory.TIMEOUT,
            InferenceErrorCategory.PROVIDER_UNAVAILABLE,
        }


def classify_http_failure(
    status_code: int,
    response_text: str,
    *,
    retry_after_seconds: float | None = None,
) -> InferenceError:
    """Map an OpenAI-compatible error to a stable, non-secret category."""

    message = " ".join(response_text.split())[:1000]
    lowered = message.casefold()
    if status_code == 401:
        category = InferenceErrorCategory.AUTHENTICATION
    elif status_code == 403:
        category = InferenceErrorCategory.PERMISSION
    elif status_code == 408:
        category = InferenceErrorCategory.TIMEOUT
    elif status_code == 429:
        category = InferenceErrorCategory.RATE_LIMIT
    elif status_code in {502, 503, 504}:
        category = InferenceErrorCategory.PROVIDER_UNAVAILABLE
    elif status_code == 404 or any(
        term in lowered for term in ("model not found", "unknown model", "model unavailable")
    ):
        category = InferenceErrorCategory.MODEL_UNAVAILABLE
    elif status_code in {400, 406, 415, 422} and (
        ("response_format" in lowered or "json_schema" in lowered or "json object" in lowered)
        and any(
            term in lowered
            for term in ("unsupported", "not support", "not available", "invalid type")
        )
    ):
        category = InferenceErrorCategory.STRUCTURED_OUTPUT_UNSUPPORTED
    elif status_code in {400, 405, 409, 415, 422}:
        category = InferenceErrorCategory.INVALID_REQUEST
    else:
        category = InferenceErrorCategory.UNKNOWN
    context_limit_failure = status_code in {400, 413, 422} and any(
        term in lowered
        for term in (
            "context length",
            "context window",
            "maximum context",
            "max model length",
            "too many tokens",
            "token limit",
        )
    )
    detail = message or f"HTTP {status_code}"
    if context_limit_failure:
        detail = f"provider_rejected_context: {detail}"
    return InferenceError(
        category,
        detail,
        status_code=status_code,
        retry_after_seconds=retry_after_seconds,
    )
