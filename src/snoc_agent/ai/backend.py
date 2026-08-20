"""Backend protocol and request/result value objects."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class GenerationConfig:
    model: str
    base_model: str | None = None
    temperature: float = 0.0
    max_output_tokens: int | None = None
    context_window_tokens: int | None = None
    context_safety_margin_tokens: int = 128
    max_retries: int = 2
    retry_base_seconds: float = 2.0
    supports_logprobs: bool = False
    enable_thinking: bool | None = None
    use_json_schema: bool = True
    allow_json_object_fallback: bool = False
    allow_prompt_json_fallback: bool = False
    quantization: str | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)


def safe_generation_settings(config: GenerationConfig) -> dict[str, Any]:
    """Return cache/audit settings without persisting advanced body values."""

    canonical_extra = json.dumps(
        config.extra_body,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "temperature": config.temperature,
        "max_output_tokens": config.max_output_tokens,
        "context_window_tokens": config.context_window_tokens,
        "context_safety_margin_tokens": config.context_safety_margin_tokens,
        "supports_logprobs": config.supports_logprobs,
        "enable_thinking": config.enable_thinking,
        "use_json_schema": config.use_json_schema,
        "allow_json_object_fallback": config.allow_json_object_fallback,
        "allow_prompt_json_fallback": config.allow_prompt_json_fallback,
        "quantization": config.quantization,
        "extra_body_hash": hashlib.sha256(canonical_extra.encode("utf-8")).hexdigest(),
    }


ResponseT = TypeVar("ResponseT", bound=BaseModel)


@dataclass(slots=True)
class StructuredGenerationResult:
    parsed: BaseModel
    raw_output: str
    model_name: str
    backend: str
    latency_seconds: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    logprobs: dict[str, Any] = field(default_factory=dict)
    logprob_metrics: dict[str, float | int] = field(default_factory=dict)
    attempts: int = 1
    base_model_id: str | None = None
    resolved_model_id: str | None = None
    requested_route: str | None = None
    reported_provider: str | None = None
    provider_request_id: str | None = None
    structured_output_mode: str = "json_schema"
    json_schema: dict[str, Any] = field(default_factory=dict)
    schema_name: str | None = None
    fallback_reason: str | None = None
    parse_attempt_count: int = 1
    validation_errors: list[dict[str, Any]] = field(default_factory=list)
    reasoning_output: str | None = None
    pricing_metadata: dict[str, Any] = field(default_factory=dict)
    input_cost_usd: Decimal | None = None
    output_cost_usd: Decimal | None = None
    total_cost_usd: Decimal | None = None
    cost_basis: str = "unknown"
    error_category: str | None = None
    cache_hit: bool = False
    original_model_run_id: str | None = None


class LLMBackend(Protocol):
    def generate_structured(
        self,
        *,
        messages: list[ChatMessage],
        response_model: type[ResponseT],
        config: GenerationConfig,
    ) -> StructuredGenerationResult: ...
