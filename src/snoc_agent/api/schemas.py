"""Pydantic response models for the dashboard API.

Shapes match what the updated vanilla-JS frontend expects from
``GET /api/dashboard``, ``POST /api/agent-toggle``, and ``POST /api/simulate-inbox``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# GET /api/dashboard
# ---------------------------------------------------------------------------


class EntityInfo(APIModel):
    pdv_code: str | None = None
    pdv: str | None = None
    phone_number: str | None = None
    phone: str | None = None


class ExecutionDetails(APIModel):
    status: str | None = None
    endpoint: str | None = None
    message: str | None = None
    response_status: int | None = None
    dry_run: bool | None = None
    attempt_count: int | None = None
    latency_ms: int | None = None
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_body: dict[str, Any] = Field(default_factory=dict)


class RequestMetadata(APIModel):
    execution_details: ExecutionDetails | None = None


class DashboardRequest(APIModel):
    record_type: Literal["business_operation"] = "business_operation"
    request_id: str
    email_message_id: str | None = None
    operation_id: str | None = None
    execution_occurred: bool = False
    intent: str | None = None
    request_type: str | None = None
    confidence: float | None = None
    request_status: str | None = None
    decision: str | None = None
    execution_status: str | None = None
    sender: str = ""
    recipient: str | None = None
    zone: str = "Unknown"
    created_at: str | None = None
    duration_ms: int | None = None
    body_text: str = ""
    cleaned_text: str = ""
    subject: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    detected_language: str | None = None
    validation_error: str | None = None
    missing_fields: list[str] = Field(default_factory=list)
    assigned_user: str | None = None
    reply_recipient: str | None = None
    reply_subject: str | None = None
    reply_text: str | None = None
    reply_status: str | None = None
    entities: EntityInfo = Field(default_factory=EntityInfo)
    metadata: RequestMetadata | None = None


class DashboardEmailSecurityEvent(APIModel):
    """Inbound security/ingress event that never became a business request."""

    record_type: Literal["email_security_event"] = "email_security_event"
    request_id: None = None
    email_message_id: str
    operation_id: None = None
    execution_occurred: Literal[False] = False
    intent: None = None
    request_type: None = None
    confidence: None = None
    request_status: Literal["UNAUTHORIZED", "REJECTED"]
    decision: str | None = None
    execution_status: None = None
    sender: str = ""
    recipient: str | None = None
    zone: str = "Unknown"
    created_at: str | None = None
    body_text: str = ""
    cleaned_text: str = ""
    subject: str = ""
    attachments: list[dict[str, Any]] = Field(default_factory=list)
    detected_language: None = None
    validation_error: str | None = None
    authorization_allowed: bool | None = None
    authorization_reason: str | None = None
    processing_status: str | None = None
    entities: EntityInfo = Field(default_factory=EntityInfo)
    metadata: None = None


class DashboardAlert(APIModel):
    id: str = ""
    severity: str = "info"
    message: str = ""
    time: str = ""
    region: str = ""
    status: str = "Active"


class DashboardStats(APIModel):
    total_requests: int = 0
    successful_executions: int = 0
    escalated: int = 0
    rejected: int = 0
    pending_requests: int = 0
    in_progress: int = 0
    failed: int = 0
    missing_entities: int = 0
    unauthorized: int = 0
    low_confidence: int = 0


class DashboardPayload(APIModel):
    agent_active: bool = True
    requests: list[DashboardRequest] = Field(default_factory=list)
    alerts: list[DashboardAlert] = Field(default_factory=list)
    stats: DashboardStats = Field(default_factory=DashboardStats)


# ---------------------------------------------------------------------------
# POST /api/agent-toggle
# ---------------------------------------------------------------------------


class AgentToggleResponse(APIModel):
    agent_active: bool


# ---------------------------------------------------------------------------
# POST /api/simulate-inbox
# ---------------------------------------------------------------------------


class SimulateInboxResponse(APIModel):
    processed: int
