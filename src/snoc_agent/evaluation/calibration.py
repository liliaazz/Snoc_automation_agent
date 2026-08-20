"""Dependency-free offline confidence calibration for the calibration split only."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal

from snoc_agent.config import Settings
from snoc_agent.db import create_engine_and_session, create_schema
from snoc_agent.db.models import CalibrationArtifact
from snoc_agent.db.session import session_scope

CalibrationMethod = Literal["none", "logistic", "isotonic"]


def _bool(value: object) -> bool:
    return str(value).strip().casefold() in {"1", "true", "yes"}


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix.casefold() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line in handle:
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("calibration input rows must be JSON objects")
                rows.append(value)
    return rows


def _sigmoid(value: float) -> float:
    value = max(-40.0, min(40.0, value))
    return 1.0 / (1.0 + math.exp(-value))


def _fit_logistic(points: list[tuple[float, int]]) -> dict[str, Any]:
    intercept = 0.0
    slope = 1.0
    for _ in range(100):
        g0 = g1 = h00 = h01 = h11 = 0.0
        for x, y in points:
            probability = _sigmoid(intercept + slope * x)
            difference = probability - y
            weight = probability * (1.0 - probability)
            g0 += difference
            g1 += difference * x
            h00 += weight
            h01 += weight * x
            h11 += weight * x * x
        h00 += 1e-6
        h11 += 1e-6
        determinant = h00 * h11 - h01 * h01
        if abs(determinant) < 1e-12:
            break
        delta0 = (h11 * g0 - h01 * g1) / determinant
        delta1 = (-h01 * g0 + h00 * g1) / determinant
        intercept -= delta0
        slope -= delta1
        if max(abs(delta0), abs(delta1)) < 1e-8:
            break
    return {"intercept": intercept, "slope": slope}


def _fit_isotonic(points: list[tuple[float, int]]) -> dict[str, Any]:
    grouped: list[dict[str, float]] = []
    for x, y in sorted(points):
        if grouped and grouped[-1]["max_x"] == x:
            grouped[-1]["sum_y"] += y
            grouped[-1]["weight"] += 1
        else:
            grouped.append({"max_x": x, "sum_y": float(y), "weight": 1.0})
        while len(grouped) >= 2:
            left = grouped[-2]
            right = grouped[-1]
            if left["sum_y"] / left["weight"] <= right["sum_y"] / right["weight"]:
                break
            left["max_x"] = right["max_x"]
            left["sum_y"] += right["sum_y"]
            left["weight"] += right["weight"]
            grouped.pop()
    return {
        "blocks": [
            {"max_confidence": block["max_x"], "probability": block["sum_y"] / block["weight"]}
            for block in grouped
        ]
    }


def _predict(method: CalibrationMethod, parameters: dict[str, Any], raw: float) -> float:
    if method == "none":
        return raw
    if method == "logistic":
        return _sigmoid(parameters["intercept"] + parameters["slope"] * raw)
    blocks = parameters["blocks"]
    for block in blocks:
        if raw <= block["max_confidence"]:
            return float(block["probability"])
    return float(blocks[-1]["probability"])


def _brier(points: list[tuple[float, int]], probabilities: list[float]) -> float:
    return sum(
        (probability - y) ** 2 for probability, (_, y) in zip(probabilities, points, strict=True)
    ) / len(points)


def fit_calibration(
    settings: Settings,
    *,
    predictions_path: Path,
    method: CalibrationMethod,
    split_manifest_path: Path | None,
    output_path: Path,
) -> dict[str, Any]:
    rows = _read_rows(predictions_path)
    split_manifest: dict[str, str] = {}
    if split_manifest_path is not None:
        raw_manifest = json.loads(split_manifest_path.read_text(encoding="utf-8"))
        if not isinstance(raw_manifest, dict):
            raise ValueError("split manifest must map example IDs to split names")
        split_manifest = {str(key): str(value) for key, value in raw_manifest.items()}
    points: list[tuple[float, int]] = []
    for row in rows:
        example_id = str(row.get("example_id") or row.get("id") or "")
        split = split_manifest.get(example_id, str(row.get("split") or ""))
        if split != "calibration":
            continue
        raw_value = row.get("raw_confidence")
        if raw_value in {None, ""}:
            continue
        if not isinstance(raw_value, (str, int, float)):
            raise ValueError("raw confidence values must be numeric")
        raw_confidence = float(raw_value)
        if not 0 <= raw_confidence <= 1:
            raise ValueError("raw confidence values must be between zero and one")
        correct_value = row.get("correct", row.get("joint_action_and_fields_exact_match"))
        points.append((raw_confidence, int(_bool(correct_value))))
    if not points:
        raise ValueError("no calibration-split rows with raw confidence and correctness were found")
    if method == "logistic":
        parameters = _fit_logistic(points)
    elif method == "isotonic":
        parameters = _fit_isotonic(points)
    else:
        parameters = {}
    calibrated = [_predict(method, parameters, raw) for raw, _label in points]
    metrics = {
        "row_count": len(points),
        "raw_brier_score": _brier(points, [raw for raw, _label in points]),
        "calibrated_brier_score": _brier(points, calibrated),
    }
    digest = hashlib.sha256(predictions_path.read_bytes())
    if split_manifest_path is not None:
        digest.update(split_manifest_path.read_bytes())
    dataset_hash = digest.hexdigest()
    payload = {
        "method": method,
        "dataset_split": "calibration",
        "dataset_hash": dataset_hash,
        "feature_version": "raw-confidence-v1",
        "policy_version": "hybrid-v1",
        "parameters": parameters,
        "metrics": metrics,
        "warning": "Calibrated probabilities apply only to the calibration distribution.",
    }
    engine, session_factory = create_engine_and_session(settings.database_url)
    create_schema(engine)
    try:
        with session_scope(session_factory) as session:
            session.add(
                CalibrationArtifact(
                    method=method,
                    dataset_hash=dataset_hash,
                    dataset_split="calibration",
                    feature_version="raw-confidence-v1",
                    policy_version="hybrid-v1",
                    parameters=parameters,
                    metrics=metrics,
                )
            )
    finally:
        engine.dispose()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(output_path)
    return payload
