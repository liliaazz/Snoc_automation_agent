"""Ground-truth-aware diagnostics that must never enter production workflow.

Oracle rescue measures the upper bound of cases where an operation prediction
was exactly correct but the production policy escalated it.  Because selection
uses ground truth, these candidates cannot be selected safely at runtime.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from snoc_agent.evaluation.dataset_loader import EvaluationExample
from snoc_agent.evaluation.metrics import ESCALATE, EvaluationResult, evaluate_predictions


@dataclass(frozen=True, slots=True)
class OracleRescueCandidate:
    example_id: str
    expected_operations: tuple[Mapping[str, Any], ...]
    predicted_operations: tuple[Mapping[str, Any], ...]
    production_decisions: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "expected_operations": [dict(item) for item in self.expected_operations],
            "predicted_operations": [dict(item) for item in self.predicted_operations],
            "production_decisions": list(self.production_decisions),
        }


@dataclass(frozen=True, slots=True)
class OracleAnalysis:
    scored_examples: int
    correct_operation_predictions: int
    production_escalations: int
    oracle_rescue_candidates: tuple[OracleRescueCandidate, ...]

    @property
    def oracle_rescue_rate(self) -> float:
        if not self.production_escalations:
            return 0.0
        return len(self.oracle_rescue_candidates) / self.production_escalations

    @property
    def potential_rescue_share_of_correct_predictions(self) -> float:
        if not self.correct_operation_predictions:
            return 0.0
        return len(self.oracle_rescue_candidates) / self.correct_operation_predictions

    def as_dict(self) -> dict[str, Any]:
        return {
            "scored_examples": self.scored_examples,
            "correct_operation_predictions": self.correct_operation_predictions,
            "production_escalations": self.production_escalations,
            "oracle_rescue_candidate_count": len(self.oracle_rescue_candidates),
            "oracle_rescue_rate": self.oracle_rescue_rate,
            "potential_rescue_share_of_correct_predictions": (
                self.potential_rescue_share_of_correct_predictions
            ),
            "candidates": [item.as_dict() for item in self.oracle_rescue_candidates],
        }


def analyze_oracle_rescues(
    examples: Sequence[EvaluationExample],
    predictions: Sequence[object] | Mapping[str, object],
    *,
    evaluation: EvaluationResult | None = None,
) -> OracleAnalysis:
    """Select exact predictions escalated by production, using ground truth."""

    result = evaluation or evaluate_predictions(examples, predictions)
    candidates: list[OracleRescueCandidate] = []
    production_escalations = 0
    correct_predictions = 0
    scored_examples = 0
    for row in result.rows:
        if not row["operation_scored"]:
            continue
        scored_examples += 1
        correct = bool(row["joint_action_and_fields_exact_match"])
        correct_predictions += correct
        decisions = tuple(row["decisions"]) + tuple(row["operation_decisions"])
        escalated = ESCALATE in decisions
        production_escalations += escalated
        if correct and escalated:
            candidates.append(
                OracleRescueCandidate(
                    example_id=row["example_id"],
                    expected_operations=tuple(row["expected_operations"]),
                    predicted_operations=tuple(row["predicted_operations"]),
                    production_decisions=decisions,
                )
            )
    return OracleAnalysis(
        scored_examples=scored_examples,
        correct_operation_predictions=correct_predictions,
        production_escalations=production_escalations,
        oracle_rescue_candidates=tuple(candidates),
    )


oracle_rescue_analysis = analyze_oracle_rescues
