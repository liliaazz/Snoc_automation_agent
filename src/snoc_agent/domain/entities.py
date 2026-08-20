"""Transport-independent domain data used across AI and workflow layers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from snoc_agent.domain.enums import CorrelationStrength, OperationAction


@dataclass(slots=True)
class CorrelationResult:
    conversation_id: str | None
    request_id: str | None
    clarification_id: str | None
    strength: CorrelationStrength
    matched_by: str | None = None
    conflicts: list[str] = field(default_factory=list)


@dataclass(slots=True)
class OperationSnapshot:
    operation_id: str
    action: OperationAction
    pdv_code: str | None
    phone: str | None
    missing_fields: list[str]
    additional_payload: dict[str, Any] = field(default_factory=dict)
