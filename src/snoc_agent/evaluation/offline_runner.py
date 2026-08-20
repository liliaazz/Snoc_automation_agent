"""Synchronous offline runner for mock or configured model pipelines."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol

from snoc_agent.evaluation.dataset_loader import EvaluationExample, load_dataset
from snoc_agent.evaluation.metrics import EvaluationResult, coerce_prediction, evaluate_predictions


class OfflinePredictor(Protocol):
    """Minimal adapter implemented by mock and live analyzer/verifier pipelines."""

    def predict(self, example: EvaluationExample) -> object:
        """Return a mapping, dataclass, or Pydantic-like prediction."""


@dataclass(frozen=True, slots=True)
class ModelConfiguration:
    analyzer_model: str
    verifier_model: str
    analyzer_backend: str = "openai_compatible"
    verifier_backend: str = "openai_compatible"
    analyzer_quantization: str | None = None
    verifier_quantization: str | None = None
    prompt_versions: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "analyzer_model": self.analyzer_model,
            "verifier_model": self.verifier_model,
            "analyzer_backend": self.analyzer_backend,
            "verifier_backend": self.verifier_backend,
            "analyzer_quantization": self.analyzer_quantization,
            "verifier_quantization": self.verifier_quantization,
            "prompt_versions": dict(self.prompt_versions),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class OfflineRun:
    configuration: ModelConfiguration
    examples: tuple[EvaluationExample, ...]
    predictions: tuple[Mapping[str, Any], ...]
    evaluation: EvaluationResult
    started_at: datetime
    finished_at: datetime

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()

    def as_dict(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration.as_dict(),
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "evaluation": self.evaluation.as_dict(),
        }


PredictorCallable = Callable[[EvaluationExample], object]


def _invoke_predictor(
    predictor: OfflinePredictor | PredictorCallable, example: EvaluationExample
) -> object:
    method = getattr(predictor, "predict", None)
    if callable(method):
        return method(example)
    if callable(predictor):
        return predictor(example)
    raise TypeError("predictor must be callable or provide predict(example)")


def _prediction_payload(value: object, *, measured_latency_ms: float) -> dict[str, Any]:
    normalized = coerce_prediction(value)
    payload = dict(normalized.raw)
    if normalized.error and not payload.get("error"):
        payload["error"] = normalized.error
    if normalized.latency_ms is None:
        payload["latency_ms"] = measured_latency_ms
    return payload


def run_offline_evaluation(
    examples: Sequence[EvaluationExample],
    predictor: OfflinePredictor | PredictorCallable,
    configuration: ModelConfiguration,
    *,
    continue_on_error: bool = True,
) -> OfflineRun:
    """Replay examples sequentially and preserve deterministic model failures."""

    started_at = datetime.now(UTC)
    predictions: list[Mapping[str, Any]] = []
    for example in examples:
        start = perf_counter()
        try:
            raw_prediction = _invoke_predictor(predictor, example)
            latency_ms = (perf_counter() - start) * 1000
            predictions.append(_prediction_payload(raw_prediction, measured_latency_ms=latency_ms))
        except Exception as exc:
            if not continue_on_error:
                raise
            latency_ms = (perf_counter() - start) * 1000
            predictions.append(
                {
                    "operations": [],
                    "structured_output_valid": False,
                    "latency_ms": latency_ms,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )

    evaluation = evaluate_predictions(examples, predictions)
    finished_at = datetime.now(UTC)
    return OfflineRun(
        configuration=configuration,
        examples=tuple(examples),
        predictions=tuple(predictions),
        evaluation=evaluation,
        started_at=started_at,
        finished_at=finished_at,
    )


def evaluate_dataset(
    dataset_path: str,
    predictor: OfflinePredictor | PredictorCallable,
    configuration: ModelConfiguration,
    *,
    continue_on_error: bool = True,
) -> OfflineRun:
    """Convenience wrapper used by a CLI adapter."""

    examples = load_dataset(dataset_path)
    return run_offline_evaluation(
        examples,
        predictor,
        configuration,
        continue_on_error=continue_on_error,
    )
