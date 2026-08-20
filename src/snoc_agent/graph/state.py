"""Serializable state shared by the five graph agents."""

from __future__ import annotations

from typing import Any, TypedDict


class WorkflowState(TypedDict, total=False):
    workflow_run_id: str
    inbound_email_id: str
    conversation_id: str | None
    request_ids: list[str]
    operation_ids: list[str]
    processing_status: str
    authorization: dict[str, Any]
    correlation: dict[str, Any]
    analysis: dict[str, Any]
    decisions: list[str]
    execute_operation_ids: list[str]
    result: dict[str, Any]
    terminal: bool
    graph_version: str
    completed_agents: list[str]


GRAPH_VERSION = "five-agent-compat-v1"
