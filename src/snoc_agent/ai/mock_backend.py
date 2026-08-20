"""Queue-backed deterministic model backend for tests."""

from __future__ import annotations

from collections import deque
from typing import Any, TypeVar

from pydantic import BaseModel

from snoc_agent.ai.backend import ChatMessage, GenerationConfig, StructuredGenerationResult

ResponseT = TypeVar("ResponseT", bound=BaseModel)


class MockLLMBackend:
    def __init__(self, outputs: list[dict[str, Any] | BaseModel]) -> None:
        self.outputs = deque(outputs)
        self.calls: list[tuple[list[ChatMessage], type[BaseModel], GenerationConfig]] = []

    def generate_structured(
        self,
        *,
        messages: list[ChatMessage],
        response_model: type[ResponseT],
        config: GenerationConfig,
    ) -> StructuredGenerationResult:
        self.calls.append((messages, response_model, config))
        if not self.outputs:
            raise RuntimeError("mock model output queue is empty")
        output = self.outputs.popleft()
        parsed = (
            output if isinstance(output, response_model) else response_model.model_validate(output)
        )
        raw = parsed.model_dump_json()
        return StructuredGenerationResult(
            parsed=parsed,
            raw_output=raw,
            model_name=config.model,
            backend="mock",
            latency_seconds=0.0,
            base_model_id=config.base_model or config.model,
            resolved_model_id=config.model,
            requested_route=config.model,
            structured_output_mode="json_schema",
            json_schema=response_model.model_json_schema(),
            schema_name=response_model.__name__,
        )
