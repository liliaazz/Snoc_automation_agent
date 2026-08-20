"""Deterministic offline metrics for labels, fields, validation, and safety."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any

from snoc_agent.evaluation.dataset_loader import (
    DatasetFormatError,
    EvaluationExample,
    OperationExpectation,
    coerce_operation,
    example_from_mapping,
)

AUTO_EXECUTE = "AUTO_EXECUTE"
ESCALATE = "ESCALATE"
REVIEW_CORRECTION = "REVIEW_CORRECTION"


@dataclass(slots=True)
class NormalizedPrediction:
    """Backend-neutral prediction metadata consumed by the metric engine."""

    operations: tuple[OperationExpectation, ...] = ()
    outcome: str | None = None
    structured_output_valid: bool = True
    analyzer_verifier_agreement: bool | None = None
    contradiction_present: bool | None = None
    decisions: tuple[str, ...] = ()
    operation_decisions: tuple[str, ...] = ()
    validation_passed: bool | None = None
    hard_invariants_passed: bool | None = None
    latency_ms: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EvaluationResult:
    """Machine-readable aggregate and per-example evaluation output."""

    summary: dict[str, Any]
    rows: list[dict[str, Any]]
    per_class: dict[str, dict[str, float | int]]
    operation_action_per_class: dict[str, dict[str, float | int]]
    confusion_matrix: dict[str, dict[str, int]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "per_class": self.per_class,
            "operation_action_per_class": self.operation_action_per_class,
            "confusion_matrix": self.confusion_matrix,
            "rows": self.rows,
        }


def canonical_value(value: object) -> str:
    """Port of the reviewed-dataset scalar canonicalizer."""

    text = "" if value is None else str(value).strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def value_set(value: object) -> set[str]:
    """Port of the legacy semicolon-separated set representation."""

    return {part.strip() for part in canonical_value(value).split(";") if part.strip()}


def exact(left: object, right: object) -> bool:
    return canonical_value(left) == canonical_value(right)


def set_exact(left: object, right: object) -> bool:
    return value_set(left) == value_set(right)


def score_field(
    rows: Sequence[Mapping[str, Any]],
    gold_key: str,
    predicted_key: str,
    equality: Callable[[object, object], bool] = exact,
    *,
    status_key: str = "evaluation_status",
) -> dict[str, float | int]:
    """Score a historical tabular field, excluding explicitly excluded rows."""

    scored = [
        row
        for row in rows
        if not canonical_value(row.get(status_key)).casefold().startswith("excluded")
    ]
    correct = sum(equality(row.get(gold_key), row.get(predicted_key)) for row in scored)
    total = len(scored)
    return {"correct": correct, "total": total, "accuracy": _ratio(correct, total)}


def label_metrics(
    rows: Sequence[Mapping[str, Any]],
    predicted_key: str,
    *,
    gold_key: str = "label",
    status_key: str = "evaluation_status",
) -> dict[str, Any]:
    """Port the legacy single-label accuracy, macro-F1, and confusion metrics."""

    scored = [
        row
        for row in rows
        if not canonical_value(row.get(status_key)).casefold().startswith("excluded")
    ]
    truth = [canonical_value(row.get(gold_key)) for row in scored]
    predicted = [canonical_value(row.get(predicted_key)) for row in scored]
    report = classification_report(truth, predicted)
    return {
        "correct": report["correct"],
        "total": report["total"],
        "accuracy": report["accuracy"],
        "macro_f1": report["macro_f1"],
        "per_class": report["per_class"],
        "confusion": report["confusion_matrix"],
    }


def _ratio(numerator: int | float, denominator: int | float) -> float:
    return float(numerator / denominator) if denominator else 0.0


def _plain_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="python")
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if is_dataclass(value) and not isinstance(value, type):
        dumped = asdict(value)
        if isinstance(dumped, Mapping):
            return dict(dumped)
    if hasattr(value, "__dict__"):
        return dict(vars(value))
    raise DatasetFormatError(f"prediction must be mapping-like, got {type(value).__name__}")


def _optional_bool(value: object) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().casefold()
    if text in {"true", "yes", "y", "1", "pass", "passed", "agree", "agreed"}:
        return True
    if text in {"false", "no", "n", "0", "fail", "failed", "disagree"}:
        return False
    return None


def _optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    if not isinstance(value, (str, bytes, int, float)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: object) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None and number >= 0 else None


def _first(mapping: Mapping[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _normalize_decision(value: object) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).strip().upper()
    return text or None


def _extract_decisions(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value if isinstance(value, Sequence) and not isinstance(value, str) else [value]
    return tuple(decision for item in values if (decision := _normalize_decision(item)))


def _unwrap_prediction(value: object) -> dict[str, Any]:
    outer = _plain_mapping(value)
    for key in ("parsed_output", "parsed", "analysis", "result"):
        nested = outer.get(key)
        if nested is None:
            continue
        try:
            inner = _plain_mapping(nested)
        except DatasetFormatError:
            continue
        merged = dict(outer)
        merged.update(inner)
        return merged
    return outer


def coerce_prediction(value: object) -> NormalizedPrediction:
    """Normalize dict, dataclass, or Pydantic-like model output."""

    try:
        payload = _unwrap_prediction(value)
    except DatasetFormatError as exc:
        return NormalizedPrediction(
            structured_output_valid=False,
            error=str(exc),
            raw={"unparsed_value": repr(value)},
        )

    raw_operations = _first(payload, ("operations", "predicted_operations", "proposals"))
    legacy_label = _first(payload, ("predicted_label", "label"))
    legacy_label_text = canonical_value(legacy_label).casefold()
    operation_values: Sequence[object]
    invalid_operations_container = False
    if raw_operations is None:
        operation_values = []
        top_action = _first(payload, ("action", "operation_type"))
        if top_action is not None or (
            legacy_label is not None
            and legacy_label_text not in {"irrelevant", "ambiguous", "unknown", "automated"}
        ):
            operation_values = [payload]
    elif isinstance(raw_operations, Sequence) and not isinstance(
        raw_operations, (str, bytes, bytearray)
    ):
        operation_values = raw_operations
    else:
        operation_values = []
        invalid_operations_container = True

    structured_valid = _optional_bool(
        _first(payload, ("structured_output_valid", "structured_output_validity"))
    )
    structured_valid = True if structured_valid is None else structured_valid
    operations: list[OperationExpectation] = []
    operation_decisions: list[str] = []
    parse_errors: list[str] = []
    for index, operation in enumerate(operation_values):
        try:
            operations.append(coerce_operation(operation))
            operation_mapping = _plain_mapping(operation)
            operation_decision = _normalize_decision(
                _first(operation_mapping, ("final_decision", "decision"))
            )
            if operation_decision:
                operation_decisions.append(operation_decision)
        except DatasetFormatError as exc:
            parse_errors.append(f"operation[{index}]: {exc}")
    if parse_errors or invalid_operations_container:
        structured_valid = False

    raw_outcome = _first(payload, ("outcome", "analysis_outcome", "message_kind"))
    if raw_outcome is None and legacy_label_text in {
        "irrelevant",
        "ambiguous",
        "unknown",
        "automated",
    }:
        raw_outcome = legacy_label_text
    outcome = canonical_value(getattr(raw_outcome, "value", raw_outcome)).casefold() or None
    if outcome == "automated":
        outcome = "irrelevant"
    if outcome not in {None, "irrelevant", "ambiguous", "unknown"}:
        outcome = None

    token_usage = payload.get("token_usage")
    token_mapping = token_usage if isinstance(token_usage, Mapping) else {}
    prompt_tokens = _optional_int(
        _first(payload, ("prompt_tokens", "input_tokens"))
        or _first(token_mapping, ("prompt_tokens", "input_tokens"))
    )
    completion_tokens = _optional_int(
        _first(payload, ("completion_tokens", "output_tokens"))
        or _first(token_mapping, ("completion_tokens", "output_tokens"))
    )
    total_tokens = _optional_int(
        _first(payload, ("total_tokens",)) or _first(token_mapping, ("total_tokens",))
    )
    if total_tokens is None and (prompt_tokens is not None or completion_tokens is not None):
        total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)

    decisions = _extract_decisions(
        _first(payload, ("final_decisions", "decisions", "final_decision", "decision"))
    )
    validation_passed = _optional_bool(
        _first(payload, ("validation_passed", "validation_pass", "validator_passed"))
    )
    if validation_passed is None and AUTO_EXECUTE in decisions:
        validation_passed = True

    errors = parse_errors[:]
    explicit_error = canonical_value(payload.get("error"))
    if explicit_error:
        errors.append(explicit_error)
    return NormalizedPrediction(
        operations=tuple(operations),
        outcome=outcome,
        structured_output_valid=structured_valid,
        analyzer_verifier_agreement=_optional_bool(
            _first(payload, ("analyzer_verifier_agreement", "model_agreement", "agreement"))
        ),
        contradiction_present=_optional_bool(
            _first(payload, ("contradiction_present", "contradiction_with_stored_state"))
        ),
        decisions=decisions,
        operation_decisions=tuple(operation_decisions),
        validation_passed=validation_passed,
        hard_invariants_passed=_optional_bool(payload.get("hard_invariants_passed")),
        latency_ms=_optional_float(_first(payload, ("latency_ms", "latency"))),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        error="; ".join(errors) or None,
        raw=payload,
    )


def classification_report(
    truth: Sequence[str], predicted: Sequence[str], *, labels: Sequence[str] | None = None
) -> dict[str, Any]:
    if len(truth) != len(predicted):
        raise ValueError("truth and predicted labels must have equal length")
    ordered_labels = list(labels or sorted(set(truth) | set(predicted)))
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    for gold, guess in zip(truth, predicted, strict=True):
        confusion[gold][guess] += 1

    per_class: dict[str, dict[str, float | int]] = {}
    for label in ordered_labels:
        tp = confusion[label][label]
        fp = sum(confusion[gold][label] for gold in ordered_labels if gold != label)
        fn = sum(count for guess, count in confusion[label].items() if guess != label)
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        f1 = _ratio(2 * precision * recall, precision + recall)
        per_class[label] = {
            "support": sum(confusion[label].values()),
            "precision": precision,
            "recall": recall,
            "f1": f1,
        }

    correct = sum(confusion[label][label] for label in ordered_labels)
    total = len(truth)
    return {
        "correct": correct,
        "total": total,
        "accuracy": _ratio(correct, total),
        "macro_f1": _ratio(sum(item["f1"] for item in per_class.values()), len(per_class)),
        "per_class": per_class,
        "confusion_matrix": {
            gold: {guess: confusion[gold][guess] for guess in ordered_labels}
            for gold in ordered_labels
        },
    }


def _operation_key(operation: OperationExpectation) -> tuple[str, str, str, str]:
    additional = json.dumps(
        dict(operation.additional_fields), ensure_ascii=False, sort_keys=True, default=str
    )
    return (
        operation.action,
        canonical_value(operation.pdv_code),
        canonical_value(operation.phone),
        additional,
    )


def _field_counter(operations: Sequence[OperationExpectation], field_name: str) -> Counter[str]:
    return Counter(canonical_value(getattr(operation, field_name)) for operation in operations)


def _row_label(operations: Sequence[OperationExpectation], outcome: str | None) -> str:
    if not operations:
        return outcome or "unknown"
    if len(operations) == 1:
        return operations[0].action
    return "multiple"


def _operation_class_report(
    expected_rows: Sequence[Sequence[OperationExpectation]],
    predicted_rows: Sequence[Sequence[OperationExpectation]],
) -> dict[str, dict[str, float | int]]:
    labels = sorted(
        {
            operation.action
            for operations in (*expected_rows, *predicted_rows)
            for operation in operations
        }
    )
    report: dict[str, dict[str, float | int]] = {}
    for label in labels:
        tp = fp = fn = support = 0
        for expected, predicted in zip(expected_rows, predicted_rows, strict=True):
            expected_count = Counter(operation.action for operation in expected)[label]
            predicted_count = Counter(operation.action for operation in predicted)[label]
            tp += min(expected_count, predicted_count)
            fp += max(0, predicted_count - expected_count)
            fn += max(0, expected_count - predicted_count)
            support += expected_count
        precision = _ratio(tp, tp + fp)
        recall = _ratio(tp, tp + fn)
        report[label] = {
            "support": support,
            "precision": precision,
            "recall": recall,
            "f1": _ratio(2 * precision * recall, precision + recall),
        }
    return report


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _aligned_predictions(
    examples: Sequence[EvaluationExample],
    predictions: Sequence[object] | Mapping[str, object],
) -> list[object]:
    if isinstance(predictions, Mapping):
        missing = [
            example.example_id for example in examples if example.example_id not in predictions
        ]
        if missing:
            raise ValueError(f"predictions are missing example IDs: {missing}")
        return [predictions[example.example_id] for example in examples]
    if len(examples) != len(predictions):
        raise ValueError(f"expected {len(examples)} predictions, received {len(predictions)}")
    return list(predictions)


def _coerce_example(value: object, index: int) -> EvaluationExample:
    if isinstance(value, EvaluationExample):
        return value
    if isinstance(value, Mapping):
        return example_from_mapping(value, row_number=index)
    raise TypeError(f"example must be EvaluationExample or mapping, got {type(value).__name__}")


def evaluate_predictions(
    examples: Sequence[EvaluationExample | Mapping[str, Any]],
    predictions: Sequence[object] | Mapping[str, object],
) -> EvaluationResult:
    """Evaluate operation attribution, validator behavior, and auto-execution safety."""

    normalized_examples = [
        _coerce_example(example, index) for index, example in enumerate(examples, start=1)
    ]
    raw_predictions = _aligned_predictions(normalized_examples, predictions)
    normalized_predictions = [coerce_prediction(prediction) for prediction in raw_predictions]

    rows: list[dict[str, Any]] = []
    truth_labels: list[str] = []
    predicted_labels: list[str] = []
    expected_operation_rows: list[Sequence[OperationExpectation]] = []
    predicted_operation_rows: list[Sequence[OperationExpectation]] = []

    counters: Counter[str] = Counter()
    latencies: list[float] = []
    total_tokens = prompt_tokens = completion_tokens = 0
    agreement_observations = contradiction_prediction_observations = 0
    contradiction_tp = contradiction_fp = contradiction_fn = contradiction_tn = 0

    for example, prediction in zip(normalized_examples, normalized_predictions, strict=True):
        expected = example.expected_operations
        proposed = prediction.operations
        expected_label = _row_label(expected, example.expected_outcome)
        predicted_label = _row_label(proposed, prediction.outcome)
        operation_count_match = len(expected) == len(proposed)
        action_match = Counter(item.action for item in expected) == Counter(
            item.action for item in proposed
        )
        pdv_match = _field_counter(expected, "pdv_code") == _field_counter(proposed, "pdv_code")
        phone_match = _field_counter(expected, "phone") == _field_counter(proposed, "phone")
        numbers_match = pdv_match and phone_match
        joint_match = Counter(map(_operation_key, expected)) == Counter(
            map(_operation_key, proposed)
        )
        label_match = expected_label == predicted_label

        operation_decisions = prediction.operation_decisions
        if operation_decisions:
            auto_execute_count = sum(decision == AUTO_EXECUTE for decision in operation_decisions)
        elif AUTO_EXECUTE in prediction.decisions:
            auto_execute_count = len(proposed) or 1
        else:
            auto_execute_count = 0
        all_decisions = set(prediction.decisions) | set(operation_decisions)
        escalated = bool({ESCALATE, REVIEW_CORRECTION} & all_decisions)

        operation_scored = example.scorable and example.operation_scorable
        unsafe_auto_execute = bool(
            operation_scored
            and auto_execute_count
            and (
                not joint_match
                or not expected
                or not proposed
                or example.expected_contradiction is True
                or prediction.hard_invariants_passed is False
                or not prediction.structured_output_valid
            )
        )
        validation_pass_but_wrong = bool(
            operation_scored and prediction.validation_passed is True and not joint_match
        )
        validation_fail_but_correct = bool(
            operation_scored and prediction.validation_passed is False and joint_match
        )
        false_escalation = bool(
            operation_scored
            and escalated
            and joint_match
            and example.expected_contradiction is not True
        )

        row = {
            "example_id": example.example_id,
            "scored": example.scorable,
            "operation_scored": operation_scored,
            "exclusion_reason": example.exclusion_reason,
            "expected_label": expected_label,
            "predicted_label": predicted_label,
            "expected_operations": [item.as_dict() for item in expected],
            "predicted_operations": [item.as_dict() for item in proposed],
            "label_match": label_match,
            "operation_count_match": operation_count_match,
            "action_exact_match": action_match,
            "pdv_exact_match": pdv_match,
            "phone_exact_match": phone_match,
            "numbers_exact_match": numbers_match,
            "joint_label_and_numbers_exact_match": joint_match,
            "joint_action_and_fields_exact_match": joint_match,
            "structured_output_valid": prediction.structured_output_valid,
            "analyzer_verifier_agreement": prediction.analyzer_verifier_agreement,
            "expected_contradiction": example.expected_contradiction,
            "predicted_contradiction": prediction.contradiction_present,
            "decisions": list(prediction.decisions),
            "operation_decisions": list(operation_decisions),
            "auto_execution_count": auto_execute_count,
            "unsafe_auto_execute": unsafe_auto_execute,
            "validation_passed": prediction.validation_passed,
            "validation_pass_but_wrong": validation_pass_but_wrong,
            "validation_fail_but_correct": validation_fail_but_correct,
            "false_escalation": false_escalation,
            "latency_ms": prediction.latency_ms,
            "total_tokens": prediction.total_tokens,
            "error": prediction.error,
        }
        rows.append(row)
        if not example.scorable:
            continue

        counters["evaluated_examples"] += 1
        counters["label_match"] += label_match
        counters["structured_output_valid"] += prediction.structured_output_valid

        truth_labels.append(expected_label)
        predicted_labels.append(predicted_label)

        if prediction.analyzer_verifier_agreement is not None:
            agreement_observations += 1
            counters["analyzer_verifier_agreement"] += prediction.analyzer_verifier_agreement
        if example.expected_contradiction is not None:
            counters["contradiction_examples"] += 1
            contradiction_prediction_observations += prediction.contradiction_present is not None
            predicted_contradiction = prediction.contradiction_present is True
            if example.expected_contradiction and predicted_contradiction:
                contradiction_tp += 1
            elif not example.expected_contradiction and predicted_contradiction:
                contradiction_fp += 1
            elif example.expected_contradiction and not predicted_contradiction:
                contradiction_fn += 1
            else:
                contradiction_tn += 1
        if prediction.latency_ms is not None:
            latencies.append(prediction.latency_ms)
        prompt_tokens += prediction.prompt_tokens or 0
        completion_tokens += prediction.completion_tokens or 0
        total_tokens += prediction.total_tokens or 0

        if not example.operation_scorable:
            continue
        counters["operation_evaluated_examples"] += 1
        counters["expected_operations"] += len(expected)
        counters["actionable_examples"] += bool(expected)
        counters["operation_count_match"] += operation_count_match
        counters["action_exact_match"] += action_match
        counters["pdv_exact_match"] += pdv_match
        counters["phone_exact_match"] += phone_match
        counters["numbers_exact_match"] += numbers_match
        counters["joint_match"] += joint_match
        counters["auto_execution_attempt_rows"] += bool(auto_execute_count)
        counters["auto_execution_attempts"] += auto_execute_count
        counters["auto_execution_rows"] += bool(auto_execute_count and expected)
        counters["auto_executed_operations"] += (
            min(auto_execute_count, len(expected)) if expected else 0
        )
        counters["unsafe_auto_execute_rows"] += unsafe_auto_execute
        counters["unsafe_auto_execute"] += auto_execute_count if unsafe_auto_execute else 0
        counters["escalation_rows"] += escalated
        counters["false_escalation"] += false_escalation
        counters["validation_pass_rows"] += prediction.validation_passed is True
        counters["validation_fail_rows"] += prediction.validation_passed is False
        counters["validation_pass_but_wrong"] += validation_pass_but_wrong
        counters["validation_fail_but_correct"] += validation_fail_but_correct
        expected_operation_rows.append(expected)
        predicted_operation_rows.append(proposed)

    counters["excluded_examples"] = len(normalized_examples) - counters["evaluated_examples"]
    classification = classification_report(truth_labels, predicted_labels)
    evaluated = counters["evaluated_examples"]
    operation_evaluated = counters["operation_evaluated_examples"]
    counters["operation_excluded_examples"] = evaluated - operation_evaluated
    contradiction_total = contradiction_tp + contradiction_fp + contradiction_fn + contradiction_tn
    contradiction_precision = _ratio(contradiction_tp, contradiction_tp + contradiction_fp)
    contradiction_recall = _ratio(contradiction_tp, contradiction_tp + contradiction_fn)
    summary: dict[str, Any] = {
        "evaluated_examples": evaluated,
        "excluded_examples": counters["excluded_examples"],
        "operation_evaluated_examples": operation_evaluated,
        "operation_excluded_examples": counters["operation_excluded_examples"],
        "classification_accuracy": classification["accuracy"],
        "classification_macro_f1": classification["macro_f1"],
        "label_match": _ratio(counters["label_match"], evaluated),
        "operation_count_accuracy": _ratio(counters["operation_count_match"], operation_evaluated),
        "action_exact_match": _ratio(counters["action_exact_match"], operation_evaluated),
        "pdv_exact_match": _ratio(counters["pdv_exact_match"], operation_evaluated),
        "phone_exact_match": _ratio(counters["phone_exact_match"], operation_evaluated),
        "numbers_exact_match": _ratio(counters["numbers_exact_match"], operation_evaluated),
        "joint_label_and_numbers_exact_match": _ratio(counters["joint_match"], operation_evaluated),
        "joint_action_and_fields_exact_match": _ratio(counters["joint_match"], operation_evaluated),
        "structured_output_validity": _ratio(counters["structured_output_valid"], evaluated),
        "structured_output_failure_rate": _ratio(
            evaluated - counters["structured_output_valid"], evaluated
        ),
        "analyzer_verifier_agreement": _ratio(
            counters["analyzer_verifier_agreement"], agreement_observations
        ),
        "analyzer_verifier_agreement_observations": agreement_observations,
        "analyzer_verifier_disagreement_rate": _ratio(
            agreement_observations - counters["analyzer_verifier_agreement"],
            agreement_observations,
        ),
        "contradiction_detection_accuracy": _ratio(
            contradiction_tp + contradiction_tn, contradiction_total
        ),
        "contradiction_detection_precision": contradiction_precision,
        "contradiction_detection_recall": contradiction_recall,
        "contradiction_detection_f1": _ratio(
            2 * contradiction_precision * contradiction_recall,
            contradiction_precision + contradiction_recall,
        ),
        "contradiction_detection_coverage": _ratio(
            contradiction_prediction_observations, counters["contradiction_examples"]
        ),
        "auto_execution_coverage": _ratio(
            counters["auto_execution_rows"], counters["actionable_examples"]
        ),
        "auto_execute_coverage": _ratio(
            counters["auto_execution_rows"], counters["actionable_examples"]
        ),
        "automatic_execution_coverage": _ratio(
            counters["auto_execution_rows"], counters["actionable_examples"]
        ),
        "auto_execution_operation_coverage": _ratio(
            counters["auto_executed_operations"], counters["expected_operations"]
        ),
        "auto_execution_rows": counters["auto_execution_rows"],
        "auto_executed_operations": counters["auto_executed_operations"],
        "auto_execution_attempt_rows": counters["auto_execution_attempt_rows"],
        "auto_execution_attempts": counters["auto_execution_attempts"],
        "unsafe_auto_execute": counters["unsafe_auto_execute"],
        "unsafe_auto_execute_count": counters["unsafe_auto_execute"],
        "unsafe_auto_execute_rows": counters["unsafe_auto_execute_rows"],
        "unsafe_auto_execute_rate": _ratio(
            counters["unsafe_auto_execute_rows"], counters["auto_execution_attempt_rows"]
        ),
        "validation_pass_but_wrong": counters["validation_pass_but_wrong"],
        "validation_pass_but_wrong_count": counters["validation_pass_but_wrong"],
        "validation_pass_but_wrong_rate": _ratio(
            counters["validation_pass_but_wrong"], counters["validation_pass_rows"]
        ),
        "validation_fail_but_correct": counters["validation_fail_but_correct"],
        "validation_fail_but_correct_count": counters["validation_fail_but_correct"],
        "validation_fail_but_correct_rate": _ratio(
            counters["validation_fail_but_correct"], counters["validation_fail_rows"]
        ),
        "false_escalation_count": counters["false_escalation"],
        "false_escalation_rate": _ratio(
            counters["false_escalation"], counters["actionable_examples"]
        ),
        "false_escalation_share": _ratio(counters["false_escalation"], counters["escalation_rows"]),
        "mean_latency_ms": _ratio(sum(latencies), len(latencies)) if latencies else None,
        "p95_latency_ms": _percentile(latencies, 0.95),
        "latency_observations": len(latencies),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }
    return EvaluationResult(
        summary=summary,
        rows=rows,
        per_class=classification["per_class"],
        operation_action_per_class=_operation_class_report(
            expected_operation_rows, predicted_operation_rows
        ),
        confusion_matrix=classification["confusion_matrix"],
    )
