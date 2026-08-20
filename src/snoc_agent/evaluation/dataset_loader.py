"""Load legacy and structured evaluation datasets without pandas.

The historical CSVs use French column names and one operation per row.  Newer
datasets can put a JSON list in ``expected_operations`` (or one of the accepted
aliases) to represent several attributed operations.  A legacy row containing
several semicolon-separated identifiers is retained but marked unscorable: the
old schema cannot prove which value belongs to which operation.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from snoc_agent.domain.value_objects import canonical_action

NON_ACTION_OUTCOMES = {"irrelevant", "ambiguous", "unknown", "automated"}
STRUCTURED_OPERATION_COLUMNS = (
    "expected_operations",
    "expected_operations_json",
    "ground_truth_operations",
    "operations_json",
    "operations",
)
PDV_COLUMNS = ("pdv_code", "code_pos_pdv_number", "code_pos_number", "pos_code")
PHONE_COLUMNS = ("new_phone", "phone", "code_otp_number", "otp_phone")


class DatasetFormatError(ValueError):
    """Raised when a dataset cannot be interpreted without guessing."""


@dataclass(frozen=True, slots=True)
class OperationExpectation:
    """Canonical expected or predicted operation used by offline metrics."""

    action: str
    pdv_code: str | None = None
    phone: str | None = None
    additional_fields: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "pdv_code": self.pdv_code,
            "phone": self.phone,
            "additional_fields": dict(self.additional_fields),
        }


@dataclass(frozen=True, slots=True)
class EvaluationExample:
    """One email example and its evaluation-only ground truth."""

    example_id: str
    subject: str
    body: str
    expected_operations: tuple[OperationExpectation, ...]
    expected_outcome: str | None = None
    expected_contradiction: bool | None = None
    scorable: bool = True
    operation_scorable: bool = True
    exclusion_reason: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "example_id": self.example_id,
            "subject": self.subject,
            "body": self.body,
            "expected_operations": [item.as_dict() for item in self.expected_operations],
            "expected_outcome": self.expected_outcome,
            "expected_contradiction": self.expected_contradiction,
            "scorable": self.scorable,
            "operation_scorable": self.operation_scorable,
            "exclusion_reason": self.exclusion_reason,
            "metadata": dict(self.metadata),
        }


def canonical_value(value: object) -> str:
    """Return a stable scalar while preserving identifier formatting."""

    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.casefold() in {"", "nan", "none", "null"} else text


def split_legacy_values(value: object) -> tuple[str, ...]:
    """Split the semicolon convention used by historical reviewed CSVs."""

    return tuple(part.strip() for part in canonical_value(value).split(";") if part.strip())


def parse_optional_bool(value: object, *, column: str) -> bool | None:
    text = canonical_value(value).casefold()
    if not text:
        return None
    if text in {"1", "true", "yes", "y", "oui"}:
        return True
    if text in {"0", "false", "no", "n", "non"}:
        return False
    raise DatasetFormatError(f"{column} must contain a boolean value, got {value!r}")


def _first_value(row: Mapping[str, Any], columns: Iterable[str]) -> str:
    for column in columns:
        value = canonical_value(row.get(column))
        if value:
            return value
    return ""


def _canonical_action_name(value: object) -> str:
    return canonical_action(canonical_value(value)).value


def coerce_operation(value: object) -> OperationExpectation:
    """Normalize a structured operation mapping or Pydantic-like object."""

    if isinstance(value, OperationExpectation):
        return value
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if not isinstance(value, Mapping):
        raise DatasetFormatError(f"operation must be an object, got {type(value).__name__}")

    action_value = _first_value(
        value, ("action", "operation_type", "predicted_label", "label", "type")
    )
    if not action_value:
        raise DatasetFormatError("operation is missing an action")
    action = _canonical_action_name(action_value)

    nested_fields = value.get("fields")
    fields = nested_fields if isinstance(nested_fields, Mapping) else {}
    pdv_code = _first_value(value, PDV_COLUMNS) or _first_value(fields, PDV_COLUMNS)
    phone = _first_value(value, PHONE_COLUMNS) or _first_value(fields, PHONE_COLUMNS)

    additional = value.get("additional_fields", value.get("additional_payload", {}))
    if additional is None:
        additional = {}
    if not isinstance(additional, Mapping):
        raise DatasetFormatError("additional_fields must be a JSON object")

    return OperationExpectation(
        action=action,
        pdv_code=pdv_code or None,
        phone=phone or None,
        additional_fields=dict(additional),
    )


def _parse_operations_json(value: object, *, column: str) -> tuple[OperationExpectation, ...]:
    if isinstance(value, str):
        try:
            parsed: object = json.loads(value)
        except json.JSONDecodeError as exc:
            raise DatasetFormatError(f"invalid JSON in {column}: {exc.msg}") from exc
    else:
        parsed = value

    if isinstance(parsed, Mapping):
        parsed = parsed.get("operations", parsed.get("expected_operations"))
    if not isinstance(parsed, Sequence) or isinstance(parsed, (str, bytes, bytearray)):
        raise DatasetFormatError(f"{column} must contain a JSON list of operations")
    return tuple(coerce_operation(operation) for operation in parsed)


def _structured_operations(row: Mapping[str, Any]) -> tuple[OperationExpectation, ...] | None:
    for column in STRUCTURED_OPERATION_COLUMNS:
        value = row.get(column)
        if value not in (None, ""):
            return _parse_operations_json(value, column=column)
    return None


def _legacy_truth(
    row: Mapping[str, Any],
) -> tuple[tuple[OperationExpectation, ...], str | None, bool, bool, str | None]:
    raw_label = _first_value(row, ("label", "expected_label", "ground_truth_label"))
    label = raw_label.casefold()
    if label in NON_ACTION_OUTCOMES:
        outcome = "irrelevant" if label == "automated" else label
        return (), outcome, True, True, None
    if label == "multiple":
        return (), None, False, False, "legacy_multiple_row_has_no_operation_attribution"
    if not label:
        raise DatasetFormatError("row is missing both structured operations and a legacy label")

    action = _canonical_action_name(label)
    if action == "unknown" and label != "unknown":
        raise DatasetFormatError(f"unsupported legacy label: {raw_label!r}")

    pdv_values = split_legacy_values(_first_value(row, PDV_COLUMNS))
    phone_values = split_legacy_values(_first_value(row, PHONE_COLUMNS))
    if len(pdv_values) > 1 or len(phone_values) > 1:
        operation = OperationExpectation(
            action=action,
            pdv_code=";".join(pdv_values) or None,
            phone=";".join(phone_values) or None,
        )
        return (
            (operation,),
            None,
            True,
            False,
            "legacy_multi_value_fields_have_no_operation_attribution",
        )

    operation = OperationExpectation(
        action=action,
        pdv_code=pdv_values[0] if pdv_values else None,
        phone=phone_values[0] if phone_values else None,
    )
    return (operation,), None, True, True, None


def example_from_mapping(row: Mapping[str, Any], *, row_number: int) -> EvaluationExample:
    """Convert one CSV/JSON record into a traceable evaluation example."""

    structured = _structured_operations(row)
    if structured is None:
        (
            operations,
            expected_outcome,
            scorable,
            operation_scorable,
            exclusion_reason,
        ) = _legacy_truth(row)
    else:
        operations = structured
        outcome_value = _first_value(row, ("expected_outcome", "analysis_outcome"))
        expected_outcome = outcome_value.casefold() or None
        if expected_outcome == "automated":
            expected_outcome = "irrelevant"
        if expected_outcome not in {None, "irrelevant", "ambiguous", "unknown"}:
            raise DatasetFormatError(f"unsupported expected outcome: {outcome_value!r}")
        scorable = True
        operation_scorable = True
        exclusion_reason = None

    status = canonical_value(row.get("evaluation_status")).casefold()
    if status.startswith("excluded"):
        scorable = False
        operation_scorable = False
        exclusion_reason = exclusion_reason or status

    # Dataset builders serialize these fields directly.  Honor them when a
    # generated JSON/JSONL subset is loaded again instead of reconstructing a
    # more permissive default from the operation payload alone.
    if canonical_value(row.get("scorable")):
        parsed_scorable = parse_optional_bool(row["scorable"], column="scorable")
        if parsed_scorable is not None:
            scorable = parsed_scorable
    if canonical_value(row.get("operation_scorable")):
        parsed_operation_scorable = parse_optional_bool(
            row["operation_scorable"], column="operation_scorable"
        )
        if parsed_operation_scorable is not None:
            operation_scorable = parsed_operation_scorable
    serialized_exclusion = canonical_value(row.get("exclusion_reason"))
    if serialized_exclusion:
        exclusion_reason = serialized_exclusion

    example_id = _first_value(row, ("row_id", "example_id", "id", "csv_line"))
    if not example_id:
        example_id = f"row-{row_number}"

    contradiction_value = None
    for column in ("expected_contradiction", "contradiction_present", "has_contradiction"):
        if canonical_value(row.get(column)):
            contradiction_value = parse_optional_bool(row[column], column=column)
            break

    subject = _first_value(row, ("objet", "subject"))
    body = _first_value(row, ("corps", "body", "email_body"))
    metadata = dict(row)
    nested_metadata = metadata.pop("metadata", None)
    if isinstance(nested_metadata, Mapping):
        metadata.update(nested_metadata)
    return EvaluationExample(
        example_id=example_id,
        subject=subject,
        body=body,
        expected_operations=operations,
        expected_outcome=expected_outcome,
        expected_contradiction=contradiction_value,
        scorable=scorable,
        operation_scorable=operation_scorable,
        exclusion_reason=exclusion_reason,
        metadata=metadata,
    )


def _read_csv(path: Path) -> list[Mapping[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise DatasetFormatError(f"CSV has no header: {path}")
        return list(reader)


def _read_json(path: Path) -> list[Mapping[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise DatasetFormatError(f"invalid JSON in {path}: {exc.msg}") from exc
    if isinstance(payload, Mapping):
        payload = payload.get("examples", payload.get("rows", payload.get("data")))
    if not isinstance(payload, list) or not all(isinstance(row, Mapping) for row in payload):
        raise DatasetFormatError("JSON dataset must be a list of objects")
    return payload


def _read_jsonl(path: Path) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    with path.open(encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise DatasetFormatError(
                    f"invalid JSON on line {line_number} of {path}: {exc.msg}"
                ) from exc
            if not isinstance(row, Mapping):
                raise DatasetFormatError(f"line {line_number} of {path} is not an object")
            rows.append(row)
    return rows


def load_dataset(path: str | Path) -> list[EvaluationExample]:
    """Load CSV, JSON, or JSONL examples and enforce unique example IDs."""

    dataset_path = Path(path)
    suffix = dataset_path.suffix.casefold()
    if suffix == ".csv":
        rows = _read_csv(dataset_path)
    elif suffix == ".json":
        rows = _read_json(dataset_path)
    elif suffix in {".jsonl", ".ndjson"}:
        rows = _read_jsonl(dataset_path)
    else:
        raise DatasetFormatError(f"unsupported dataset format: {dataset_path.suffix or '<none>'}")

    examples = [example_from_mapping(row, row_number=index) for index, row in enumerate(rows, 1)]
    seen: set[str] = set()
    duplicates: set[str] = set()
    for example in examples:
        if example.example_id in seen:
            duplicates.add(example.example_id)
        seen.add(example.example_id)
    if duplicates:
        raise DatasetFormatError(f"duplicate example IDs: {sorted(duplicates)}")
    return examples
