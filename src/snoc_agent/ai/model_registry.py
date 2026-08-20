"""Named Qwen analyzer/verifier combinations for offline comparisons."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelPair:
    name: str
    analyzer_model: str
    verifier_model: str


MODEL_PAIRS = {
    pair.name: pair
    for pair in (
        ModelPair("qwen25_qwen25", "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen2.5-7B-Instruct"),
        ModelPair("qwen3_qwen3", "Qwen/Qwen3-8B", "Qwen/Qwen3-8B"),
        ModelPair("qwen25_qwen3", "Qwen/Qwen2.5-7B-Instruct", "Qwen/Qwen3-8B"),
        ModelPair("qwen3_qwen25", "Qwen/Qwen3-8B", "Qwen/Qwen2.5-7B-Instruct"),
    )
}

VLLM_MODEL_PAIRS = {
    pair.name: pair
    for pair in (
        ModelPair("qwen_qwen", "qwen", "qwen"),
        ModelPair("qwen_gemma", "qwen", "gemma"),
        ModelPair("gemma_qwen", "gemma", "qwen"),
        ModelPair("gemma_gemma", "gemma", "gemma"),
        ModelPair("qwen3_30b_qwen3_30b", "qwen3_30b", "qwen3_30b"),
        ModelPair("qwen3_30b_gemma", "qwen3_30b", "gemma"),
        ModelPair("gemma_qwen3_30b", "gemma", "qwen3_30b"),
    )
}
