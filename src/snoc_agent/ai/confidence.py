"""Confidence metadata helpers; missing log probabilities stay missing."""

from __future__ import annotations

from typing import Any

from snoc_agent.ai.schemas import ConfidenceRecord


def logprob_metrics(logprobs: dict[str, Any]) -> dict[str, float | int]:
    """Extract label or token-choice margins without treating them as calibrated."""

    values = logprobs.get("label_logprobs")
    if isinstance(values, dict):
        numeric = sorted(
            (float(value) for value in values.values() if isinstance(value, int | float)),
            reverse=True,
        )
        if len(numeric) >= 2:
            return {"label_margin": numeric[0] - numeric[1], "observations": 1}

    token_margins: list[float] = []
    content = logprobs.get("content")
    if isinstance(content, list):
        for token_row in content:
            if not isinstance(token_row, dict):
                continue
            chosen = token_row.get("logprob")
            chosen_token = token_row.get("token")
            alternatives = token_row.get("top_logprobs")
            if not isinstance(chosen, int | float) or not isinstance(alternatives, list):
                continue
            alternative_values = [
                float(item["logprob"])
                for item in alternatives
                if isinstance(item, dict)
                and item.get("token") != chosen_token
                and isinstance(item.get("logprob"), int | float)
            ]
            if alternative_values:
                token_margins.append(float(chosen) - max(alternative_values))
    if not token_margins:
        return {}
    return {
        "minimum_token_margin": min(token_margins),
        "mean_token_margin": sum(token_margins) / len(token_margins),
        "observations": len(token_margins),
    }


def logprob_margin(logprobs: dict[str, Any]) -> float | None:
    metrics = logprob_metrics(logprobs)
    value = metrics.get("label_margin", metrics.get("minimum_token_margin"))
    return float(value) if isinstance(value, int | float) else None


def build_confidence_record(
    *,
    raw_model_confidence: float | None,
    logprobs: dict[str, Any],
    agreement: bool,
    evidence_complete: bool,
    correlation_strength: str,
    hard_invariants_passed: bool,
) -> ConfidenceRecord:
    return ConfidenceRecord(
        raw_model_confidence=raw_model_confidence,
        logprob_margin_if_available=logprob_margin(logprobs),
        analyzer_verifier_agreement=agreement,
        structured_output_valid=True,
        evidence_complete=evidence_complete,
        correlation_strength=correlation_strength,  # type: ignore[arg-type]
        hard_invariants_passed=hard_invariants_passed,
    )
