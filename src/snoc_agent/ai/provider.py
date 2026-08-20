"""Model-provider and structured-output identifiers."""

from __future__ import annotations

from enum import StrEnum


class LLMProvider(StrEnum):
    DEMO = "demo"
    OPENAI_COMPATIBLE = "openai_compatible"
    VLLM = "vllm"


class VLLMDeploymentName(StrEnum):
    QWEN = "qwen"
    GEMMA = "gemma"
    QWEN3_30B = "qwen3_30b"


class StructuredOutputMode(StrEnum):
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_JSON = "prompt_json"


class CostBasis(StrEnum):
    EXACT = "exact"
    PROVIDER_REPORTED = "provider_reported"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"
