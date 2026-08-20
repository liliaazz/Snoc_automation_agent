"""Audited HTTP backend for OpenAI-compatible chat-completion APIs."""

from __future__ import annotations

import json
import math
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel

from snoc_agent.ai.backend import ChatMessage, GenerationConfig, StructuredGenerationResult
from snoc_agent.ai.confidence import logprob_metrics
from snoc_agent.ai.cost import BudgetTracker, calculate_cost
from snoc_agent.ai.errors import (
    InferenceError,
    InferenceErrorCategory,
    classify_http_failure,
)
from snoc_agent.ai.provider import StructuredOutputMode
from snoc_agent.ai.structured_output import parse_structured_output
from snoc_agent.domain.errors import StructuredOutputError

ResponseT = TypeVar("ResponseT", bound=BaseModel)
PricingResolver = Callable[[str, str | None], dict[str, Any]]
THINK_BLOCK_RE = re.compile(r"^\s*<think>(.*?)</think>\s*", re.DOTALL | re.IGNORECASE)
RESERVED_EXTRA_BODY_FIELDS = {
    "model",
    "messages",
    "response_format",
    "temperature",
    "max_tokens",
    "stream",
    "authorization",
}
MIN_STRUCTURED_OUTPUT_TOKENS = 64


def _retry_after(response: httpx.Response) -> float | None:
    raw = response.headers.get("Retry-After")
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(raw)
            now = datetime.now(UTC)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return max(0.0, (parsed - now).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return "" if value is None else str(value)


def _reasoning_and_final(message: dict[str, Any]) -> tuple[str | None, str]:
    reasoning_parts = [
        _content_text(message.get(key)).strip()
        for key in ("reasoning", "reasoning_content", "analysis")
        if message.get(key)
    ]
    content = _content_text(message.get("content"))
    think_match = THINK_BLOCK_RE.match(content)
    if think_match:
        reasoning_parts.append(think_match.group(1).strip())
        content = content[think_match.end() :]
    reasoning = "\n\n".join(part for part in reasoning_parts if part) or None
    return reasoning, content.strip()


class OpenAICompatibleBackend:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str = "",
        timeout_seconds: float = 60.0,
        client: httpx.Client | None = None,
        backend_name: str = "openai_compatible",
        reported_provider_fallback: str | None = None,
        send_thinking_parameters: bool = True,
        budget_tracker: BudgetTracker | None = None,
        pricing_resolver: PricingResolver | None = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.backend_name = backend_name
        self.reported_provider_fallback = reported_provider_fallback
        self.send_thinking_parameters = send_thinking_parameters
        self.budget_tracker = budget_tracker
        self.pricing_resolver = pricing_resolver
        self._sleep = sleep
        self._jitter = jitter
        self._owns_client = client is None
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout_seconds, headers=headers)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _usage(data: dict[str, Any], key: str) -> int | None:
        usage = data.get("usage")
        aliases = {
            "prompt_tokens": ("prompt_tokens", "input_tokens"),
            "completion_tokens": ("completion_tokens", "output_tokens"),
            "total_tokens": ("total_tokens",),
        }
        value = None
        if isinstance(usage, dict):
            value = next(
                (usage[name] for name in aliases.get(key, (key,)) if name in usage),
                None,
            )
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        numeric = float(value)
        if not math.isfinite(numeric) or numeric < 0 or not numeric.is_integer():
            return None
        return int(numeric)

    def _post(
        self, payload: dict[str, Any], config: GenerationConfig
    ) -> tuple[dict[str, Any], httpx.Response, int]:
        max_attempts = max(1, config.max_retries + 1)
        last_error: InferenceError | None = None
        projected_cost = None
        if self.pricing_resolver is not None and config.max_output_tokens is not None:
            suffix = config.model.rsplit(":", 1)[-1] if ":" in config.model else ""
            projected_provider = (
                suffix if suffix and suffix not in {"fastest", "cheapest", "preferred"} else None
            )
            projected_pricing = self.pricing_resolver(
                config.base_model or config.model,
                projected_provider,
            )
            prompt_upper_bound = len(
                json.dumps(payload.get("messages", []), ensure_ascii=False).encode("utf-8")
            )
            projected_cost = calculate_cost(
                prompt_tokens=prompt_upper_bound,
                completion_tokens=config.max_output_tokens,
                pricing_metadata=projected_pricing,
            ).total_cost_usd
        for attempt in range(1, max_attempts + 1):
            if self.budget_tracker is not None:
                self.budget_tracker.before_attempt(projected_cost)
                self.budget_tracker.record_attempt()
            try:
                response = self.client.post(f"{self.base_url}/chat/completions", json=payload)
            except httpx.TimeoutException as exc:
                error = InferenceError(
                    InferenceErrorCategory.TIMEOUT,
                    "inference request timed out",
                    transport_attempt_count=attempt,
                )
                last_error = error
                if attempt >= max_attempts:
                    raise error from exc
            except httpx.TransportError as exc:
                error = InferenceError(
                    InferenceErrorCategory.PROVIDER_UNAVAILABLE,
                    "inference provider transport failure",
                    transport_attempt_count=attempt,
                )
                last_error = error
                if attempt >= max_attempts:
                    raise error from exc
            else:
                if not response.is_error:
                    try:
                        data = response.json()
                    except ValueError as exc:
                        raise InferenceError(
                            InferenceErrorCategory.UNKNOWN,
                            "inference provider returned invalid JSON",
                            status_code=response.status_code,
                            transport_attempt_count=attempt,
                        ) from exc
                    if not isinstance(data, dict):
                        raise InferenceError(
                            InferenceErrorCategory.UNKNOWN,
                            "inference provider returned an unexpected payload",
                            status_code=response.status_code,
                            transport_attempt_count=attempt,
                        )
                    return data, response, attempt
                error = classify_http_failure(
                    response.status_code,
                    response.text,
                    retry_after_seconds=_retry_after(response),
                )
                error.transport_attempt_count = attempt
                if config.extra_body:
                    error.args = (
                        f"inference request failed ({error.category.value}); provider details "
                        "were redacted because extra-body settings are configured",
                    )
                last_error = error
                if not error.retryable or attempt >= max_attempts:
                    raise error
            if last_error is not None:
                delay = last_error.retry_after_seconds
                if delay is None:
                    delay = config.retry_base_seconds * (2 ** (attempt - 1))
                    delay += delay * 0.25 * self._jitter()
                self._sleep(delay)
        raise last_error or InferenceError(InferenceErrorCategory.UNKNOWN, "inference failed")

    @staticmethod
    def _estimated_input_tokens(payload: dict[str, Any]) -> int:
        """Conservatively estimate tokens consumed by the complete request prompt.

        OpenAI-compatible providers do not expose a portable preflight tokenizer.
        Counting four UTF-8 bytes per token is close for ordinary Latin text, while
        the character-based term avoids undercounting scripts whose UTF-8 encoding
        is wider. A small fixed envelope accounts for chat-template framing.
        """

        prompt_envelope = {
            "messages": payload.get("messages", []),
            "response_format": payload.get("response_format"),
        }
        serialized = json.dumps(
            prompt_envelope,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        byte_estimate = math.ceil(len(serialized.encode("utf-8")) / 4)
        character_estimate = math.ceil(len(serialized) / 3)
        return max(byte_estimate, character_estimate) + 32

    @classmethod
    def _apply_context_budget(
        cls,
        payload: dict[str, Any],
        config: GenerationConfig,
    ) -> None:
        """Reserve output space without allowing input + output past the window."""

        if config.context_window_tokens is None:
            return
        estimated_input = cls._estimated_input_tokens(payload)
        available_output = (
            config.context_window_tokens - estimated_input - config.context_safety_margin_tokens
        )
        if available_output < MIN_STRUCTURED_OUTPUT_TOKENS:
            raise InferenceError(
                InferenceErrorCategory.INVALID_REQUEST,
                "model_context_limit: estimated prompt exceeds the configured model context window",
            )
        requested = config.max_output_tokens
        if requested is None or requested > available_output:
            payload["max_tokens"] = available_output

    @staticmethod
    def _payload(
        *,
        messages: list[ChatMessage],
        response_model: type[BaseModel],
        config: GenerationConfig,
        mode: StructuredOutputMode,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": config.model,
            "messages": [message.model_dump() for message in messages],
            "temperature": config.temperature,
        }
        if config.max_output_tokens is not None:
            payload["max_tokens"] = config.max_output_tokens
        if mode == StructuredOutputMode.JSON_SCHEMA:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_model.__name__,
                    "strict": True,
                    "schema": response_model.model_json_schema(),
                },
            }
        elif mode == StructuredOutputMode.JSON_OBJECT:
            payload["response_format"] = {"type": "json_object"}
        if config.supports_logprobs:
            payload["logprobs"] = True
        collisions = RESERVED_EXTRA_BODY_FIELDS.intersection(
            key.casefold() for key in config.extra_body
        )
        if collisions:
            raise ValueError(
                "extra-body settings cannot override reserved fields: "
                + ", ".join(sorted(collisions))
            )
        payload.update(config.extra_body)
        OpenAICompatibleBackend._apply_context_budget(payload, config)
        return payload

    @staticmethod
    def _prompt_json_messages(
        messages: list[ChatMessage], response_model: type[BaseModel], *, repair: bool = False
    ) -> list[ChatMessage]:
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False, sort_keys=True)
        instruction = ("Your previous answer was invalid. Repair it once. " if repair else "") + (
            "Return exactly one JSON object and no explanation, markdown, or reasoning. "
            f"It must validate against this JSON Schema: {schema}"
        )
        return [*messages, ChatMessage(role="user", content=instruction)]

    def _reported_provider(self, data: dict[str, Any], config: GenerationConfig) -> str | None:
        for key in ("provider", "inference_provider", "selected_provider"):
            if isinstance(data.get(key), str):
                return str(data[key])
        return self.reported_provider_fallback

    @staticmethod
    def _reported_cost_parts(
        data: dict[str, Any], usage: dict[str, Any]
    ) -> tuple[object, object, object]:
        def present(mapping: dict[str, Any], names: tuple[str, ...]) -> object:
            return next(
                (mapping[name] for name in names if name in mapping and mapping[name] is not None),
                None,
            )

        total = present(data, ("cost", "total_cost"))
        if total is None:
            total = present(usage, ("cost", "total_cost"))
        input_cost = present(data, ("input_cost", "prompt_cost"))
        if input_cost is None:
            input_cost = present(usage, ("input_cost", "prompt_cost"))
        output_cost = present(data, ("output_cost", "completion_cost"))
        if output_cost is None:
            output_cost = present(usage, ("output_cost", "completion_cost"))
        return total, input_cost, output_cost

    @staticmethod
    def _pricing_provider(reported_provider: str | None, config: GenerationConfig) -> str | None:
        if reported_provider:
            return reported_provider
        suffix = config.model.rsplit(":", 1)[-1] if ":" in config.model else ""
        return suffix if suffix and suffix not in {"fastest", "cheapest", "preferred"} else None

    def probe_chat(
        self,
        *,
        model: str,
        base_model: str | None = None,
        max_retries: int = 0,
        retry_base_seconds: float = 2.0,
    ) -> dict[str, Any]:
        """Make one very small unstructured compatibility request."""

        started = time.monotonic()
        config = GenerationConfig(
            model=model,
            base_model=base_model,
            max_output_tokens=8,
            max_retries=max_retries,
            retry_base_seconds=retry_base_seconds,
        )
        data, response, attempts = self._post(
            {
                "model": model,
                "messages": [
                    {"role": "system", "content": "Reply briefly."},
                    {"role": "user", "content": "Reply with OK."},
                ],
                "temperature": 0.0,
                "max_tokens": 8,
            },
            config,
        )
        prompt_tokens = self._usage(data, "prompt_tokens")
        completion_tokens = self._usage(data, "completion_tokens")
        provider = self._reported_provider(data, config)
        pricing_provider = self._pricing_provider(provider, config)
        pricing = (
            self.pricing_resolver(base_model or model, pricing_provider)
            if self.pricing_resolver is not None
            else {}
        )
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        provider_reported_cost, provider_input_cost, provider_output_cost = (
            self._reported_cost_parts(data, usage)
        )
        cost = calculate_cost(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            pricing_metadata=pricing,
            provider_reported_cost=provider_reported_cost,
            provider_reported_input_cost=provider_input_cost,
            provider_reported_output_cost=provider_output_cost,
        )
        if self.budget_tracker is not None:
            self.budget_tracker.record(
                total_cost_usd=cost.total_cost_usd,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        raw_content: str | None = None
        reasoning: str | None = None
        try:
            choice = data["choices"][0]
            if not isinstance(choice, dict):
                raise TypeError("choice")
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError("message")
            reasoning, raw_content = _reasoning_and_final(message)
            if not raw_content:
                raise ValueError("empty content")
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise InferenceError(
                InferenceErrorCategory.MALFORMED_OUTPUT,
                "chat compatibility probe did not return assistant content",
                raw_output=raw_content,
                model_name=str(data.get("model") or model),
                reported_provider=provider,
                provider_request_id=response.headers.get("Inference-Id")
                or response.headers.get("x-request-id"),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=self._usage(data, "total_tokens"),
                pricing_metadata=pricing,
                input_cost_usd=cost.input_cost_usd,
                output_cost_usd=cost.output_cost_usd,
                total_cost_usd=cost.total_cost_usd,
                cost_basis=cost.basis.value,
                transport_attempt_count=attempts,
                parse_attempt_count=1,
                latency_seconds=time.monotonic() - started,
                reasoning_output=reasoning,
            ) from exc
        return {
            "model": data.get("model") or model,
            "reported_provider": provider,
            "assistant_content": raw_content,
            "reasoning_output": reasoning,
            "provider_request_id": response.headers.get("Inference-Id")
            or response.headers.get("x-request-id"),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": self._usage(data, "total_tokens"),
            "attempts": attempts,
            "latency_seconds": time.monotonic() - started,
            "pricing_metadata": pricing,
            "input_cost_usd": (
                str(cost.input_cost_usd) if cost.input_cost_usd is not None else None
            ),
            "output_cost_usd": (
                str(cost.output_cost_usd) if cost.output_cost_usd is not None else None
            ),
            "total_cost_usd": str(cost.total_cost_usd) if cost.total_cost_usd is not None else None,
            "cost_basis": cost.basis.value,
        }

    def generate_structured(
        self,
        *,
        messages: list[ChatMessage],
        response_model: type[ResponseT],
        config: GenerationConfig,
    ) -> StructuredGenerationResult:
        schema = response_model.model_json_schema()
        mode = (
            StructuredOutputMode.JSON_SCHEMA
            if config.use_json_schema
            else StructuredOutputMode.JSON_OBJECT
        )
        fallback_reason: str | None = None
        total_http_attempts = 0
        parse_attempts = 0
        validation_errors: list[dict[str, Any]] = []
        started = time.monotonic()
        repair = False
        response_count = 0
        prompt_token_sum = 0
        completion_token_sum = 0
        total_token_sum = 0
        prompt_tokens_complete = True
        completion_tokens_complete = True
        total_tokens_complete = True
        known_input_cost = Decimal("0")
        known_output_cost = Decimal("0")
        known_total_cost = Decimal("0")
        input_cost_complete = True
        output_cost_complete = True
        total_cost_complete = True
        cost_bases: list[str] = []
        reasoning_parts: list[str] = []
        last_raw_output: str | None = None
        last_model_name: str | None = None
        last_reported_provider: str | None = None
        last_provider_request_id: str | None = None
        last_pricing: dict[str, Any] = {}
        pricing_history: list[dict[str, Any]] = []
        last_logprobs: dict[str, Any] = {}

        def aggregate_prompt_tokens() -> int | None:
            return prompt_token_sum if response_count and prompt_tokens_complete else None

        def aggregate_completion_tokens() -> int | None:
            return completion_token_sum if response_count and completion_tokens_complete else None

        def aggregate_total_tokens() -> int | None:
            if response_count and total_tokens_complete:
                return total_token_sum
            prompt = aggregate_prompt_tokens()
            completion = aggregate_completion_tokens()
            return prompt + completion if prompt is not None and completion is not None else None

        def aggregate_cost_basis() -> str:
            if not response_count or not total_cost_complete:
                return "unknown"
            bases = set(cost_bases)
            if bases == {"exact"}:
                return "exact"
            if bases <= {"exact", "provider_reported"}:
                return "provider_reported"
            return "estimated"

        def aggregate_pricing_metadata() -> dict[str, Any]:
            if len(pricing_history) <= 1:
                return last_pricing
            return {"responses": pricing_history}

        def audited(error: InferenceError) -> InferenceError:
            error.structured_output_mode = mode.value
            error.json_schema = schema
            error.schema_name = response_model.__name__
            error.fallback_reason = fallback_reason
            error.parse_attempt_count = parse_attempts
            error.validation_errors = list(validation_errors)
            error.raw_output = last_raw_output
            error.reasoning_output = "\n\n".join(reasoning_parts) or None
            error.model_name = last_model_name
            error.reported_provider = last_reported_provider
            error.provider_request_id = last_provider_request_id
            error.prompt_tokens = aggregate_prompt_tokens()
            error.completion_tokens = aggregate_completion_tokens()
            error.total_tokens = aggregate_total_tokens()
            error.pricing_metadata = aggregate_pricing_metadata()
            error.input_cost_usd = (
                known_input_cost if response_count and input_cost_complete else None
            )
            error.output_cost_usd = (
                known_output_cost if response_count and output_cost_complete else None
            )
            error.total_cost_usd = (
                known_total_cost if response_count and total_cost_complete else None
            )
            error.cost_basis = aggregate_cost_basis()
            error.latency_seconds = time.monotonic() - started
            error.logprobs = last_logprobs
            error.logprob_metrics = logprob_metrics(last_logprobs)
            error.transport_attempt_count = total_http_attempts
            return error

        while True:
            request_messages = (
                self._prompt_json_messages(messages, response_model, repair=repair)
                if mode == StructuredOutputMode.PROMPT_JSON
                else messages
            )
            payload = self._payload(
                messages=request_messages,
                response_model=response_model,
                config=config,
                mode=mode,
            )
            if self.send_thinking_parameters and config.enable_thinking is not None:
                payload["chat_template_kwargs"] = {"enable_thinking": config.enable_thinking}
            try:
                data, response, attempts = self._post(payload, config)
                total_http_attempts += attempts
            except InferenceError as exc:
                total_http_attempts += exc.transport_attempt_count
                if exc.category != InferenceErrorCategory.STRUCTURED_OUTPUT_UNSUPPORTED:
                    audited(exc)
                    raise
                if mode == StructuredOutputMode.JSON_SCHEMA and config.allow_json_object_fallback:
                    fallback_reason = f"json_schema rejected: {exc}"
                    mode = StructuredOutputMode.JSON_OBJECT
                    continue
                if mode == StructuredOutputMode.JSON_OBJECT and config.allow_prompt_json_fallback:
                    object_reason = f"json_object rejected: {exc}"
                    fallback_reason = (
                        f"{fallback_reason}; {object_reason}" if fallback_reason else object_reason
                    )
                    mode = StructuredOutputMode.PROMPT_JSON
                    continue
                audited(exc)
                raise

            prompt_tokens = self._usage(data, "prompt_tokens")
            completion_tokens = self._usage(data, "completion_tokens")
            response_total_tokens = self._usage(data, "total_tokens")
            reported_provider = self._reported_provider(data, config)
            pricing_provider = self._pricing_provider(reported_provider, config)
            pricing = (
                self.pricing_resolver(config.base_model or config.model, pricing_provider)
                if self.pricing_resolver is not None
                else {}
            )
            raw_usage = data.get("usage")
            usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
            provider_cost, provider_input_cost, provider_output_cost = self._reported_cost_parts(
                data, usage
            )
            cost = calculate_cost(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                pricing_metadata=pricing,
                provider_reported_cost=provider_cost,
                provider_reported_input_cost=provider_input_cost,
                provider_reported_output_cost=provider_output_cost,
            )
            if self.budget_tracker is not None:
                self.budget_tracker.record(
                    total_cost_usd=cost.total_cost_usd,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                )
            response_count += 1
            if prompt_tokens is None:
                prompt_tokens_complete = False
            else:
                prompt_token_sum += prompt_tokens
            if completion_tokens is None:
                completion_tokens_complete = False
            else:
                completion_token_sum += completion_tokens
            if response_total_tokens is None:
                total_tokens_complete = False
            else:
                total_token_sum += response_total_tokens
            if cost.input_cost_usd is None:
                input_cost_complete = False
            else:
                known_input_cost += cost.input_cost_usd
            if cost.output_cost_usd is None:
                output_cost_complete = False
            else:
                known_output_cost += cost.output_cost_usd
            if cost.total_cost_usd is None:
                total_cost_complete = False
            else:
                known_total_cost += cost.total_cost_usd
            cost_bases.append(cost.basis.value)
            last_model_name = str(data.get("model") or config.model)
            last_reported_provider = reported_provider
            last_provider_request_id = response.headers.get("Inference-Id") or response.headers.get(
                "x-request-id"
            )
            last_pricing = pricing
            pricing_history.append(
                {
                    "reported_provider": reported_provider,
                    "pricing_provider": pricing_provider,
                    "pricing": pricing,
                }
            )

            try:
                choice = data["choices"][0]
                message = choice["message"]
                if not isinstance(choice, dict) or not isinstance(message, dict):
                    raise KeyError("message")
            except (KeyError, IndexError, TypeError) as exc:
                raise audited(
                    InferenceError(
                        InferenceErrorCategory.MALFORMED_OUTPUT,
                        "chat completion response did not contain one assistant message",
                    )
                ) from exc
            reasoning, raw = _reasoning_and_final(message)
            last_raw_output = raw
            choice_logprobs = choice.get("logprobs")
            last_logprobs = choice_logprobs if isinstance(choice_logprobs, dict) else {}
            if reasoning:
                reasoning_parts.append(reasoning)
            parse_attempts += 1
            try:
                parsed = parse_structured_output(raw, response_model)
            except StructuredOutputError as exc:
                validation_errors.append({"attempt": parse_attempts, "message": str(exc)[:2000]})
                if mode == StructuredOutputMode.JSON_SCHEMA and config.allow_json_object_fallback:
                    fallback_reason = f"json_schema returned malformed content: {exc}"
                    mode = StructuredOutputMode.JSON_OBJECT
                    continue
                if mode == StructuredOutputMode.JSON_OBJECT and config.allow_prompt_json_fallback:
                    object_reason = f"json_object returned malformed content: {exc}"
                    fallback_reason = (
                        f"{fallback_reason}; {object_reason}" if fallback_reason else object_reason
                    )
                    mode = StructuredOutputMode.PROMPT_JSON
                    continue
                if mode == StructuredOutputMode.PROMPT_JSON and not repair:
                    repair = True
                    continue
                raise audited(
                    InferenceError(
                        InferenceErrorCategory.MALFORMED_OUTPUT,
                        f"structured response failed validation after {parse_attempts} parse attempt(s)",
                    )
                ) from exc

            return StructuredGenerationResult(
                parsed=parsed,
                raw_output=raw,
                model_name=last_model_name or config.model,
                backend=self.backend_name,
                latency_seconds=time.monotonic() - started,
                prompt_tokens=aggregate_prompt_tokens(),
                completion_tokens=aggregate_completion_tokens(),
                total_tokens=aggregate_total_tokens(),
                logprobs=last_logprobs,
                logprob_metrics=logprob_metrics(last_logprobs),
                attempts=total_http_attempts,
                base_model_id=config.base_model or config.model,
                resolved_model_id=config.model,
                requested_route=config.model,
                reported_provider=last_reported_provider,
                provider_request_id=last_provider_request_id,
                structured_output_mode=mode.value,
                json_schema=schema,
                schema_name=response_model.__name__,
                fallback_reason=fallback_reason,
                parse_attempt_count=parse_attempts,
                validation_errors=validation_errors,
                reasoning_output="\n\n".join(reasoning_parts) or None,
                pricing_metadata=aggregate_pricing_metadata(),
                input_cost_usd=(
                    known_input_cost if response_count and input_cost_complete else None
                ),
                output_cost_usd=(
                    known_output_cost if response_count and output_cost_complete else None
                ),
                total_cost_usd=(
                    known_total_cost if response_count and total_cost_complete else None
                ),
                cost_basis=aggregate_cost_basis(),
            )
