"""Small structured-JSON logging setup with explicit correlation fields."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

CORRELATION_FIELDS = (
    "email_message_id",
    "rfc_message_id",
    "conversation_id",
    "request_id",
    "operation_id",
    "clarification_id",
    "execution_id",
    "model_run_id",
)
EVENT_FIELDS = (
    "action",
    "correlation_strength",
    "decision",
    "request_reference",
    "status",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in (*CORRELATION_FIELDS, *EVENT_FIELDS):
            value = getattr(record, field, None)
            if value is not None:
                event[field] = str(value)
        if record.exc_info:
            event["exception"] = self.formatException(record.exc_info)
        return json.dumps(event, ensure_ascii=False)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
