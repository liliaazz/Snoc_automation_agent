from __future__ import annotations

import json
from dataclasses import fields

import snoc_agent.evaluation as evaluation_package
from snoc_agent.evaluation.comparison import compare_runs
from snoc_agent.evaluation.dataset_loader import (
    EvaluationExample,
    OperationExpectation,
    load_dataset,
)
from snoc_agent.evaluation.metrics import (
    evaluate_predictions,
    exact,
    label_metrics,
    set_exact,
)
from snoc_agent.evaluation.offline_runner import ModelConfiguration, run_offline_evaluation
from snoc_agent.evaluation.oracle import analyze_oracle_rescues
from snoc_agent.evaluation.reports import write_offline_run_report


def operation(
    action: str, pdv_code: str | None = None, phone: str | None = None
) -> OperationExpectation:
    return OperationExpectation(action=action, pdv_code=pdv_code, phone=phone)


def example(
    example_id: str, *operations: OperationExpectation, contradiction: bool | None = None
) -> EvaluationExample:
    return EvaluationExample(
        example_id=example_id,
        subject="Demande",
        body="Corps",
        expected_operations=tuple(operations),
        expected_contradiction=contradiction,
    )


def test_load_legacy_dataset_maps_labels_and_columns(tmp_path) -> None:
    dataset = tmp_path / "legacy.csv"
    dataset.write_text(
        "row_id,objet,corps,label,code_pos_pdv_number,code_otp_number\n"
        "A-1,Changement OTP,Merci,otp,12345678,712345678\n"
        "A-2,Compte bloque,Merci,locked,87654321,\n",
        encoding="utf-8",
    )

    rows = load_dataset(dataset)

    assert rows[0].expected_operations == (operation("otp_number_change", "12345678", "712345678"),)
    assert rows[1].expected_operations == (operation("account_unblock", "87654321"),)


def test_load_structured_dataset_supports_multiple_operations(tmp_path) -> None:
    dataset = tmp_path / "structured.json"
    dataset.write_text(
        json.dumps(
            [
                {
                    "id": "multi-1",
                    "subject": "Trois demandes",
                    "body": "...",
                    "expected_operations": [
                        {"action": "locked", "pdv_code": "11111111"},
                        {
                            "action": "otp",
                            "pdv_code": "22222222",
                            "new_phone": "712345678",
                        },
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    rows = load_dataset(dataset)

    assert rows[0].scorable is True
    assert [item.action for item in rows[0].expected_operations] == [
        "account_unblock",
        "otp_number_change",
    ]


def test_legacy_multiple_values_keep_label_scoring_without_guessing_attribution(
    tmp_path,
) -> None:
    dataset = tmp_path / "ambiguous.csv"
    dataset.write_text(
        "objet,corps,label,code_pos_pdv_number,code_otp_number\n"
        'Demande,Corps,otp,"11111111;22222222","712345678;798765432"\n',
        encoding="utf-8",
    )

    row = load_dataset(dataset)[0]

    assert row.scorable is True
    assert row.operation_scorable is False
    assert [item.action for item in row.expected_operations] == ["otp_number_change"]
    assert row.exclusion_reason == "legacy_multi_value_fields_have_no_operation_attribution"
    result = evaluate_predictions([row], [{"predicted_label": "otp"}])
    assert result.summary["classification_accuracy"] == 1.0
    assert result.summary["operation_evaluated_examples"] == 0


def test_legacy_exact_and_label_metrics_are_preserved() -> None:
    assert exact(" 123 ", "123")
    assert set_exact("111;222", "222;111")
    result = label_metrics(
        [
            {"label": "otp", "prediction": "otp"},
            {"label": "vpn", "prediction": "otp"},
            {
                "label": "reset",
                "prediction": "vpn",
                "evaluation_status": "excluded_multiple",
            },
        ],
        "prediction",
    )
    assert result["correct"] == 1
    assert result["total"] == 2
    assert result["accuracy"] == 0.5


def test_joint_metric_detects_swapped_multi_operation_attribution() -> None:
    sample = example(
        "multi",
        operation("otp_number_change", "11111111", "711111111"),
        operation("otp_number_change", "22222222", "722222222"),
    )
    prediction = {
        "operations": [
            {
                "action": "otp_number_change",
                "pdv_code": "11111111",
                "new_phone": "722222222",
            },
            {
                "action": "otp_number_change",
                "pdv_code": "22222222",
                "new_phone": "711111111",
            },
        ]
    }

    result = evaluate_predictions([sample], [prediction])
    row = result.rows[0]

    assert row["action_exact_match"] is True
    assert row["pdv_exact_match"] is True
    assert row["phone_exact_match"] is True
    assert row["numbers_exact_match"] is True
    assert row["joint_action_and_fields_exact_match"] is False
    assert result.summary["joint_action_and_fields_exact_match"] == 0.0


def test_safety_and_validator_metrics_distinguish_failure_modes() -> None:
    samples = [
        example("unsafe", operation("account_unblock", "11111111")),
        example("false-escalation", operation("password_reset", "22222222")),
    ]
    predictions = [
        {
            "operations": [{"action": "account_unblock", "pdv_code": "99999999"}],
            "final_decision": "AUTO_EXECUTE",
            "validation_passed": True,
            "hard_invariants_passed": True,
        },
        {
            "operations": [{"action": "password_reset", "pdv_code": "22222222"}],
            "final_decision": "ESCALATE",
            "validation_passed": False,
        },
    ]

    summary = evaluate_predictions(samples, predictions).summary

    assert summary["auto_execution_coverage"] == 0.5
    assert summary["auto_execute_coverage"] == 0.5
    assert summary["unsafe_auto_execute"] == 1
    assert summary["unsafe_auto_execute_rows"] == 1
    assert summary["validation_pass_but_wrong"] == 1
    assert summary["validation_fail_but_correct"] == 1
    assert summary["false_escalation_count"] == 1


def test_auto_execute_on_non_action_outcome_is_unsafe() -> None:
    sample = EvaluationExample(
        example_id="irrelevant",
        subject="Invitation",
        body="Reunion demain",
        expected_operations=(),
        expected_outcome="irrelevant",
    )

    result = evaluate_predictions(
        [sample],
        [
            {
                "operations": [],
                "outcome": "irrelevant",
                "final_decision": "AUTO_EXECUTE",
                "validation_passed": True,
            }
        ],
    )

    assert result.summary["unsafe_auto_execute"] == 1
    assert result.summary["auto_execution_coverage"] == 0.0


def test_legacy_irrelevant_prediction_is_an_outcome_not_unknown_operation() -> None:
    sample = EvaluationExample(
        example_id="irrelevant",
        subject="Invitation",
        body="Reunion demain",
        expected_operations=(),
        expected_outcome="irrelevant",
    )

    result = evaluate_predictions([sample], [{"predicted_label": "irrelevant"}])

    assert result.rows[0]["predicted_operations"] == []
    assert result.rows[0]["predicted_label"] == "irrelevant"
    assert result.summary["classification_accuracy"] == 1.0


def test_explicit_empty_operation_list_is_valid_structured_irrelevant_output() -> None:
    sample = EvaluationExample(
        example_id="irrelevant-empty-list",
        subject="Invitation",
        body="Réunion demain",
        expected_operations=(),
        expected_outcome="irrelevant",
    )

    result = evaluate_predictions(
        [sample],
        [
            {
                "predicted_label": "irrelevant",
                "operations": [],
                "structured_output_valid": True,
            }
        ],
    )

    assert result.summary["structured_output_validity"] == 1.0
    assert result.summary["joint_action_and_fields_exact_match"] == 1.0


def test_offline_runner_and_reports_emit_all_required_artifacts(tmp_path) -> None:
    samples = [example("one", operation("account_unblock", "12345678"))]

    def predictor(_: EvaluationExample) -> dict[str, object]:
        return {
            "operations": [{"action": "locked", "pdv_code": "12345678"}],
            "final_decision": "AUTO_EXECUTE",
            "validation_passed": True,
            "analyzer_verifier_agreement": True,
            "total_tokens": 12,
        }

    configuration = ModelConfiguration(
        analyzer_model="Qwen2.5-7B-Instruct",
        verifier_model="Qwen3-8B",
        prompt_versions={"analyzer": "analyzer_v1", "verifier": "verifier_v1"},
    )
    run = run_offline_evaluation(samples, predictor, configuration)
    paths = write_offline_run_report(tmp_path / "run", run)

    assert run.evaluation.summary["joint_action_and_fields_exact_match"] == 1.0
    assert all(getattr(paths, item.name).exists() for item in fields(paths))
    summary = json.loads(paths.summary_json.read_text(encoding="utf-8"))
    assert summary["unsafe_auto_execute"] == 0


def test_comparison_prioritizes_zero_unsafe_execution() -> None:
    comparison = compare_runs(
        {
            "high-coverage-unsafe": {
                "unsafe_auto_execute": 1,
                "validation_pass_but_wrong_count": 1,
                "joint_action_and_fields_exact_match": 1.0,
                "auto_execution_coverage": 1.0,
                "mean_latency_ms": 1.0,
            },
            "safe": {
                "unsafe_auto_execute": 0,
                "validation_pass_but_wrong_count": 0,
                "joint_action_and_fields_exact_match": 0.9,
                "auto_execution_coverage": 0.5,
                "mean_latency_ms": 10.0,
            },
        }
    )

    assert comparison.recommended_run == "safe"


def test_oracle_is_explicit_and_selects_only_correct_escalations() -> None:
    assert "analyze_oracle_rescues" not in evaluation_package.__all__
    samples = [
        example("correct", operation("account_unblock", "12345678")),
        example("wrong", operation("account_unblock", "87654321")),
    ]
    predictions = [
        {
            "operations": [{"action": "account_unblock", "pdv_code": "12345678"}],
            "final_decision": "ESCALATE",
        },
        {
            "operations": [{"action": "account_unblock", "pdv_code": "00000000"}],
            "final_decision": "ESCALATE",
        },
    ]

    analysis = analyze_oracle_rescues(samples, predictions)

    assert analysis.production_escalations == 2
    assert [item.example_id for item in analysis.oracle_rescue_candidates] == ["correct"]
    assert analysis.oracle_rescue_rate == 0.5
