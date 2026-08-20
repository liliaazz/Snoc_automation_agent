from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from snoc_agent.config import Settings
from snoc_agent.evaluation.calibration import fit_calibration
from snoc_agent.evaluation.dataset_loader import load_dataset
from snoc_agent.evaluation.dataset_subsets import (
    build_evaluation_subsets,
    synthetic_smoke_examples,
)
from snoc_agent.evaluation.pipeline_predictor import evaluation_context


def test_calibration_uses_only_manifest_calibration_rows(tmp_path) -> None:
    predictions = tmp_path / "predictions.csv"
    with predictions.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["example_id", "raw_confidence", "joint_action_and_fields_exact_match"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "example_id": "cal-1",
                    "raw_confidence": 0.2,
                    "joint_action_and_fields_exact_match": False,
                },
                {
                    "example_id": "cal-2",
                    "raw_confidence": 0.8,
                    "joint_action_and_fields_exact_match": True,
                },
                {
                    "example_id": "heldout",
                    "raw_confidence": 0.99,
                    "joint_action_and_fields_exact_match": False,
                },
            ]
        )
    manifest = tmp_path / "splits.json"
    manifest.write_text(
        json.dumps({"cal-1": "calibration", "cal-2": "calibration", "heldout": "held_out_test"}),
        encoding="utf-8",
    )

    artifact = fit_calibration(
        Settings(database_url=f"sqlite:///{tmp_path / 'calibration.db'}"),
        predictions_path=predictions,
        method="isotonic",
        split_manifest_path=manifest,
        output_path=tmp_path / "artifact.json",
    )

    assert artifact["dataset_split"] == "calibration"
    assert artifact["metrics"]["row_count"] == 2
    assert artifact["method"] == "isotonic"


def test_dataset_builder_labels_194_demo_candidates_as_not_qwen(tmp_path) -> None:
    source = Path("labeled_data/labeled data/SMOLDATA_last_1000_reviewed.csv")

    if not source.is_file():
        pytest.skip(
            "The reviewed SMOLDATA evaluation dataset is not included in this project bundle"
        )

    result = build_evaluation_subsets(
        Settings(),
        source=source,
        output_dir=tmp_path / "subsets",
    )

    assert result["demo_unsafe_candidate_count"] == 194
    assert result["demo_results_are_qwen_measurements"] is False
    manifest = json.loads(
        (tmp_path / "subsets/demo_unsafe_candidates_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["unsafe_candidate_count"] == 194
    assert manifest["measurement_type"] == "deterministic_demo_not_qwen"
    safety_examples = load_dataset(tmp_path / "subsets/safety_regression.jsonl")
    assert (
        sum(bool(example.metadata.get("demo_unsafe_candidate")) for example in safety_examples)
        == 194
    )
    migration = json.loads(
        (tmp_path / "subsets/demo_vs_real_regression_report.json").read_text(encoding="utf-8")
    )
    assert migration["real_model_measurement_performed"] is False
    assert migration["demo_backend_failures"]["count"] == 194


def test_generated_jsonl_preserves_explicit_scorability(tmp_path) -> None:
    dataset = tmp_path / "generated.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "example_id": "excluded-1",
                "subject": "test",
                "body": "test",
                "expected_operations": [],
                "expected_outcome": "irrelevant",
                "scorable": False,
                "operation_scorable": False,
                "exclusion_reason": "ground_truth_review_required",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    example = load_dataset(dataset)[0]

    assert example.scorable is False
    assert example.operation_scorable is False
    assert example.exclusion_reason == "ground_truth_review_required"


def test_smoke_phone_only_reply_contains_seeded_clarification_state() -> None:
    example = next(
        item for item in synthetic_smoke_examples() if item.example_id == "smoke-phone-only-reply"
    )

    context, candidates = evaluation_context(example)

    assert context["mode"] == "clarification_reply"
    assert context["target_operations"][0]["known_fields"]["pdv_code"] == "71000008"
    assert context["latest_user_message"] == "+213770000008"
    assert any(candidate["value"] == "+213770000008" for candidate in candidates)
