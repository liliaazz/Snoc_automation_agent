"""Write reproducible, machine-readable offline evaluation artifacts."""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from snoc_agent.evaluation.dataset_loader import EvaluationExample
from snoc_agent.evaluation.metrics import EvaluationResult, coerce_prediction


@dataclass(frozen=True, slots=True)
class ReportPaths:
    predictions_csv: Path
    summary_json: Path
    confusion_matrix_json: Path
    errors_json: Path
    model_configuration_json: Path
    summary_markdown: Path


@dataclass(frozen=True, slots=True)
class ComparisonReportPaths:
    comparison_json: Path
    comparison_markdown: Path


class OfflineRunLike(Protocol):
    @property
    def examples(self) -> Sequence[EvaluationExample]: ...

    @property
    def predictions(self) -> Sequence[object]: ...

    @property
    def evaluation(self) -> EvaluationResult: ...

    @property
    def configuration(self) -> object: ...


def _jsonable(value: object) -> Any:
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Enum):
        return _jsonable(value.value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return _jsonable(asdict(value))
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="python"))
    if hasattr(value, "as_dict"):
        return _jsonable(value.as_dict())
    return str(value)


def _json_text(value: object) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _configuration_dict(configuration: object | None) -> dict[str, Any]:
    if configuration is None:
        return {}
    if isinstance(configuration, Mapping):
        return dict(configuration)
    if hasattr(configuration, "as_dict"):
        result = configuration.as_dict()
        return dict(result) if isinstance(result, Mapping) else {"value": result}
    if is_dataclass(configuration) and not isinstance(configuration, type):
        return asdict(configuration)
    return {"value": str(configuration)}


def _prediction_rows(
    examples: Sequence[EvaluationExample],
    predictions: Sequence[object],
    evaluation: EvaluationResult,
) -> list[dict[str, Any]]:
    if not (len(examples) == len(predictions) == len(evaluation.rows)):
        raise ValueError("examples, predictions, and evaluation rows must have equal length")
    rows: list[dict[str, Any]] = []
    for example, raw_prediction, score in zip(examples, predictions, evaluation.rows, strict=True):
        prediction = coerce_prediction(raw_prediction)
        rows.append(
            {
                "example_id": example.example_id,
                "scored": score["scored"],
                "operation_scored": score["operation_scored"],
                "exclusion_reason": score["exclusion_reason"] or "",
                "expected_label": score["expected_label"],
                "predicted_label": score["predicted_label"],
                "expected_operations": json.dumps(
                    score["expected_operations"], ensure_ascii=False, sort_keys=True
                ),
                "predicted_operations": json.dumps(
                    score["predicted_operations"], ensure_ascii=False, sort_keys=True
                ),
                "label_match": score["label_match"],
                "operation_count_match": score["operation_count_match"],
                "action_exact_match": score["action_exact_match"],
                "pdv_exact_match": score["pdv_exact_match"],
                "phone_exact_match": score["phone_exact_match"],
                "numbers_exact_match": score["numbers_exact_match"],
                "joint_action_and_fields_exact_match": score["joint_action_and_fields_exact_match"],
                "structured_output_valid": score["structured_output_valid"],
                "analyzer_verifier_agreement": score["analyzer_verifier_agreement"],
                "expected_contradiction": score["expected_contradiction"],
                "predicted_contradiction": score["predicted_contradiction"],
                "decisions": ";".join(score["decisions"]),
                "operation_decisions": ";".join(score["operation_decisions"]),
                "unsafe_auto_execute": score["unsafe_auto_execute"],
                "validation_pass_but_wrong": score["validation_pass_but_wrong"],
                "validation_fail_but_correct": score["validation_fail_but_correct"],
                "false_escalation": score["false_escalation"],
                "latency_ms": prediction.latency_ms,
                "prompt_tokens": prediction.raw.get("prompt_tokens"),
                "completion_tokens": prediction.raw.get("completion_tokens"),
                "total_tokens": prediction.total_tokens,
                "raw_confidence": prediction.raw.get("raw_confidence"),
                "logprob_margin": prediction.raw.get("logprob_margin"),
                "total_cost_usd": prediction.raw.get("total_cost_usd"),
                "cost_bases": json.dumps(prediction.raw.get("cost_bases", []), ensure_ascii=False),
                "incremental_prompt_tokens": prediction.raw.get("incremental_prompt_tokens"),
                "incremental_completion_tokens": prediction.raw.get(
                    "incremental_completion_tokens"
                ),
                "incremental_total_cost_usd": prediction.raw.get("incremental_total_cost_usd"),
                "incremental_cost_known": prediction.raw.get("incremental_cost_known"),
                "structured_output_modes": json.dumps(
                    prediction.raw.get("structured_output_modes", []), ensure_ascii=False
                ),
                "cache_hits": prediction.raw.get("cache_hits", 0),
                "error": prediction.error or "",
            }
        )
    return rows


def _write_predictions_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0]) if rows else ["example_id"]
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _error_categories(
    examples: Sequence[EvaluationExample],
    evaluation: EvaluationResult,
    *,
    max_examples: int,
    include_email_content: bool,
) -> dict[str, list[dict[str, Any]]]:
    categories = {
        "label_mismatch": lambda row: not row["label_match"],
        "operation_count_mismatch": lambda row: (
            row["operation_scored"] and not row["operation_count_match"]
        ),
        "action_mismatch": lambda row: row["operation_scored"] and not row["action_exact_match"],
        "pdv_mismatch": lambda row: row["operation_scored"] and not row["pdv_exact_match"],
        "phone_mismatch": lambda row: row["operation_scored"] and not row["phone_exact_match"],
        "joint_action_and_fields_mismatch": lambda row: (
            row["operation_scored"] and not row["joint_action_and_fields_exact_match"]
        ),
        "structured_output_invalid": lambda row: not row["structured_output_valid"],
        "unsafe_auto_execute": lambda row: row["unsafe_auto_execute"],
        "validation_pass_but_wrong": lambda row: row["validation_pass_but_wrong"],
        "validation_fail_but_correct": lambda row: row["validation_fail_but_correct"],
        "false_escalation": lambda row: row["false_escalation"],
    }
    output: dict[str, list[dict[str, Any]]] = {name: [] for name in categories}
    for example, row in zip(examples, evaluation.rows, strict=True):
        if not row["scored"]:
            continue
        for name, predicate in categories.items():
            if len(output[name]) >= max_examples or not predicate(row):
                continue
            item = {
                "example_id": example.example_id,
                "expected_label": row["expected_label"],
                "predicted_label": row["predicted_label"],
                "expected_operations": row["expected_operations"],
                "predicted_operations": row["predicted_operations"],
                "decisions": row["decisions"],
                "error": row["error"],
            }
            if include_email_content:
                item["subject"] = example.subject
                item["body"] = example.body
            output[name].append(item)
    return output


def _summary_markdown(evaluation: EvaluationResult) -> str:
    summary = evaluation.summary
    displayed = (
        "classification_accuracy",
        "classification_macro_f1",
        "operation_count_accuracy",
        "action_exact_match",
        "pdv_exact_match",
        "phone_exact_match",
        "joint_action_and_fields_exact_match",
        "structured_output_validity",
        "analyzer_verifier_agreement",
        "auto_execution_coverage",
        "unsafe_auto_execute",
        "validation_pass_but_wrong",
        "validation_fail_but_correct",
        "false_escalation_rate",
        "mean_latency_ms",
        "total_tokens",
    )
    lines = ["# Offline evaluation summary", "", "| Metric | Value |", "|---|---:|"]
    for metric in displayed:
        value = summary.get(metric)
        rendered = f"{value:.4f}" if isinstance(value, float) else str(value)
        lines.append(f"| `{metric}` | {rendered} |")
    lines.append("")
    return "\n".join(lines)


def write_evaluation_report(
    output_dir: str | Path,
    examples: Sequence[EvaluationExample],
    predictions: Sequence[object],
    evaluation: EvaluationResult,
    *,
    configuration: object | None = None,
    prompt_versions: Mapping[str, str] | None = None,
    max_error_examples: int = 20,
    include_email_content: bool = False,
) -> ReportPaths:
    """Write predictions, metrics, confusion data, errors, and model metadata."""

    if max_error_examples < 1:
        raise ValueError("max_error_examples must be at least one")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = ReportPaths(
        predictions_csv=directory / "predictions.csv",
        summary_json=directory / "summary.json",
        confusion_matrix_json=directory / "confusion_matrix.json",
        errors_json=directory / "per_error_category_examples.json",
        model_configuration_json=directory / "model_configuration.json",
        summary_markdown=directory / "summary.md",
    )

    prediction_rows = _prediction_rows(examples, predictions, evaluation)
    _write_predictions_csv(paths.predictions_csv, prediction_rows)
    _atomic_write_text(
        paths.summary_json,
        _json_text(
            {
                **evaluation.summary,
                "per_class": evaluation.per_class,
                "operation_action_per_class": evaluation.operation_action_per_class,
            }
        ),
    )
    _atomic_write_text(paths.confusion_matrix_json, _json_text(evaluation.confusion_matrix))
    _atomic_write_text(
        paths.errors_json,
        _json_text(
            _error_categories(
                examples,
                evaluation,
                max_examples=max_error_examples,
                include_email_content=include_email_content,
            )
        ),
    )
    config = _configuration_dict(configuration)
    if prompt_versions is not None:
        config["prompt_versions"] = dict(prompt_versions)
    _atomic_write_text(paths.model_configuration_json, _json_text(config))
    _atomic_write_text(paths.summary_markdown, _summary_markdown(evaluation))
    return paths


def write_offline_run_report(
    output_dir: str | Path,
    run: OfflineRunLike,
    *,
    max_error_examples: int = 20,
    include_email_content: bool = False,
) -> ReportPaths:
    """Write an ``OfflineRun`` without importing it and creating a cycle."""

    examples = run.examples
    predictions = run.predictions
    evaluation = run.evaluation
    configuration = getattr(run, "configuration", None)
    return write_evaluation_report(
        output_dir,
        examples,
        predictions,
        evaluation,
        configuration=configuration,
        max_error_examples=max_error_examples,
        include_email_content=include_email_content,
    )


def write_comparison_report(output_dir: str | Path, comparison: object) -> ComparisonReportPaths:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload_method = getattr(comparison, "as_dict", None)
    payload = payload_method() if callable(payload_method) else comparison
    if not isinstance(payload, Mapping):
        raise TypeError("comparison must be a mapping or provide as_dict()")
    paths = ComparisonReportPaths(
        comparison_json=directory / "comparison.json",
        comparison_markdown=directory / "comparison.md",
    )
    _atomic_write_text(paths.comparison_json, _json_text(payload))
    lines = [
        "# Analyzer/verifier matrix comparison",
        "",
        f"Baseline: `{payload.get('baseline_run')}`",
        f"Safety-first recommendation: `{payload.get('recommended_run')}`",
        "",
        "| Run | Unsafe auto-execute | Joint exact match | Auto coverage | Mean latency ms |",
        "|---|---:|---:|---:|---:|",
    ]
    for row in payload.get("runs", []):
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "| {run} | {unsafe} | {joint} | {coverage} | {latency} |".format(
                run=row.get("run_name"),
                unsafe=row.get("unsafe_auto_execute"),
                joint=row.get("joint_action_and_fields_exact_match"),
                coverage=row.get("auto_execution_coverage"),
                latency=row.get("mean_latency_ms"),
            )
        )
    lines.append("")
    _atomic_write_text(paths.comparison_markdown, "\n".join(lines))
    return paths
