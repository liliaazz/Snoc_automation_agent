"""Compare analyzer/verifier pair runs using safety-first ordering."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from snoc_agent.evaluation.metrics import EvaluationResult

CORE_METRICS = (
    "classification_accuracy",
    "classification_macro_f1",
    "operation_count_accuracy",
    "action_exact_match",
    "pdv_exact_match",
    "phone_exact_match",
    "joint_action_and_fields_exact_match",
    "structured_output_validity",
    "analyzer_verifier_agreement",
    "auto_execute_coverage",
    "auto_execution_coverage",
    "unsafe_auto_execute",
    "unsafe_auto_execute_count",
    "validation_pass_but_wrong",
    "validation_pass_but_wrong_count",
    "false_escalation_rate",
    "structured_output_failure_rate",
    "analyzer_verifier_disagreement_rate",
    "mean_latency_ms",
    "total_tokens",
)


@dataclass(frozen=True, slots=True)
class ComparisonRow:
    run_name: str
    metrics: Mapping[str, Any]
    deltas_from_baseline: Mapping[str, float | None]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_name": self.run_name,
            **dict(self.metrics),
            "deltas_from_baseline": dict(self.deltas_from_baseline),
        }


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    baseline_run: str
    recommended_run: str | None
    rows: tuple[ComparisonRow, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_run": self.baseline_run,
            "recommended_run": self.recommended_run,
            "runs": [row.as_dict() for row in self.rows],
        }


def _summary(value: EvaluationResult | Mapping[str, Any] | object) -> Mapping[str, Any]:
    if isinstance(value, EvaluationResult):
        return value.summary
    evaluation = getattr(value, "evaluation", None)
    if isinstance(evaluation, EvaluationResult):
        return evaluation.summary
    if isinstance(value, Mapping):
        nested = value.get("summary")
        return nested if isinstance(nested, Mapping) else value
    raise TypeError(f"cannot extract evaluation summary from {type(value).__name__}")


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def compare_runs(
    runs: Mapping[str, EvaluationResult | Mapping[str, Any] | object],
    *,
    baseline_run: str | None = None,
) -> ComparisonResult:
    """Return comparable metrics and a conservative recommendation.

    A run is eligible only when both unsafe-execution counters are exactly zero.
    Eligible runs are ordered by safety, joint correctness, safe coverage, and
    latency; no unsafe run is recommended merely because it is the least unsafe.
    """

    if not runs:
        raise ValueError("at least one run is required")
    baseline_name = baseline_run or next(iter(runs))
    if baseline_name not in runs:
        raise ValueError(f"unknown baseline run: {baseline_name}")
    summaries = {name: _summary(run) for name, run in runs.items()}
    baseline = summaries[baseline_name]

    rows: list[ComparisonRow] = []
    for name, summary in summaries.items():
        metrics = {metric: summary.get(metric) for metric in CORE_METRICS}
        unsafe = _number(
            summary.get("unsafe_auto_execute_count", summary.get("unsafe_auto_execute"))
        )
        validation_wrong = _number(
            summary.get(
                "validation_pass_but_wrong_count",
                summary.get("validation_pass_but_wrong"),
            )
        )
        metrics["release_eligible"] = unsafe == 0 and validation_wrong == 0
        deltas: dict[str, float | None] = {}
        for metric in CORE_METRICS:
            current = _number(summary.get(metric))
            base = _number(baseline.get(metric))
            deltas[metric] = current - base if current is not None and base is not None else None
        rows.append(ComparisonRow(name, metrics, deltas))

    def safety_key(
        row: ComparisonRow,
    ) -> tuple[float, float, float, float, float, float, str]:
        unsafe = _number(
            row.metrics.get("unsafe_auto_execute_count", row.metrics.get("unsafe_auto_execute"))
        )
        validation_wrong = _number(
            row.metrics.get(
                "validation_pass_but_wrong_count",
                row.metrics.get("validation_pass_but_wrong"),
            )
        )
        coverage = _number(row.metrics.get("auto_execution_coverage"))
        structured_failures = _number(row.metrics.get("structured_output_failure_rate"))
        disagreement = _number(row.metrics.get("analyzer_verifier_disagreement_rate"))
        latency = _number(row.metrics.get("mean_latency_ms"))
        return (
            unsafe if unsafe is not None else float("inf"),
            validation_wrong if validation_wrong is not None else float("inf"),
            -(coverage if coverage is not None else -1.0),
            structured_failures if structured_failures is not None else float("inf"),
            disagreement if disagreement is not None else float("inf"),
            latency if latency is not None else float("inf"),
            row.run_name,
        )

    ordered = tuple(sorted(rows, key=safety_key))
    recommended = next(
        (row.run_name for row in ordered if row.metrics.get("release_eligible") is True), None
    )
    return ComparisonResult(
        baseline_run=baseline_name,
        recommended_run=recommended,
        rows=ordered,
    )
