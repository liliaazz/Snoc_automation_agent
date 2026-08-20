"""Bounded extraction and strict validation of JSON model responses."""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from snoc_agent.domain.errors import StructuredOutputError

FENCED_JSON_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_structured_output(raw: str, response_model: type[BaseModel]) -> BaseModel:
    candidate = raw.strip()
    if match := FENCED_JSON_RE.fullmatch(candidate):
        candidate = match.group(1).strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"model response is not a single JSON value: {exc}") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("model response must be a JSON object")
    try:
        return response_model.model_validate(value)
    except ValidationError as exc:
        raise StructuredOutputError(f"model response violates schema: {exc}") from exc
