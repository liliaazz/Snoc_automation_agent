"""Conversions between domain results and checkpoint-safe dictionaries."""

from __future__ import annotations

import uuid
from typing import Any

from snoc_agent.workflow.inbound_processor import ProcessingResult


def result_to_dict(result: ProcessingResult) -> dict[str, Any]:
    return {
        "email_message_id": str(result.email_message_id),
        "status": result.status,
        "conversation_id": str(result.conversation_id) if result.conversation_id else None,
        "request_ids": [str(value) for value in result.request_ids],
        "operation_ids": [str(value) for value in result.operation_ids],
        "decisions": list(result.decisions),
        "duplicate_of_id": str(result.duplicate_of_id) if result.duplicate_of_id else None,
        "detail": result.detail,
    }


def result_from_dict(value: dict[str, Any]) -> ProcessingResult:
    return ProcessingResult(
        email_message_id=uuid.UUID(str(value["email_message_id"])),
        status=str(value["status"]),
        conversation_id=(
            uuid.UUID(str(value["conversation_id"])) if value.get("conversation_id") else None
        ),
        request_ids=[uuid.UUID(str(item)) for item in value.get("request_ids", [])],
        operation_ids=[uuid.UUID(str(item)) for item in value.get("operation_ids", [])],
        decisions=[str(item) for item in value.get("decisions", [])],
        duplicate_of_id=(
            uuid.UUID(str(value["duplicate_of_id"])) if value.get("duplicate_of_id") else None
        ),
        detail=str(value.get("detail", "")),
    )
