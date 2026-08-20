"""Offline evaluation utilities for analyzer and verifier experiments.

Oracle evaluation is intentionally not re-exported here.  Import
``snoc_agent.evaluation.oracle`` explicitly from evaluation-only code when a
ground-truth-aware diagnostic is required.
"""

from snoc_agent.evaluation.comparison import ComparisonResult, compare_runs
from snoc_agent.evaluation.dataset_loader import (
    DatasetFormatError,
    EvaluationExample,
    OperationExpectation,
    load_dataset,
)
from snoc_agent.evaluation.metrics import (
    EvaluationResult,
    evaluate_predictions,
)
from snoc_agent.evaluation.offline_runner import (
    ModelConfiguration,
    OfflineRun,
    run_offline_evaluation,
)
from snoc_agent.evaluation.reports import ReportPaths, write_evaluation_report

__all__ = [
    "ComparisonResult",
    "DatasetFormatError",
    "EvaluationExample",
    "EvaluationResult",
    "ModelConfiguration",
    "OfflineRun",
    "OperationExpectation",
    "ReportPaths",
    "compare_runs",
    "evaluate_predictions",
    "load_dataset",
    "run_offline_evaluation",
    "write_evaluation_report",
]
