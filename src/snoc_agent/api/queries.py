"""Read-only query helpers that map DB models to the dashboard payload shape."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from snoc_agent.api.schemas import (
    DashboardAlert,
    DashboardPayload,
    DashboardRequest,
    DashboardStats,
    EntityInfo,
    ExecutionDetails,
    RequestMetadata,
)
from snoc_agent.datetime_utils import elapsed_seconds, utc_iso, utc_now
from snoc_agent.db.models import (
    BusinessRequest,
    EmailMessage,
    Escalation,
    Execution,
    Operation,
    OutboxMessage,
    ValidationDecision,
    WorkflowRun,
)
from snoc_agent.domain.enums import (
    OperationAction,
    RequestStatus,
)

_INTENT_LABELS: dict[str, str] = {
    OperationAction.ACCOUNT_UNBLOCK: "Locked Account",
    OperationAction.PASSWORD_RESET: "Password Reset",
    OperationAction.OTP_NUMBER_CHANGE: "OTP Update",
    OperationAction.VPN_ACCESS: "VPN Creation",
    "unknown": "Irrelevant",
}

_INTENT_ICONS: dict[str, str] = {
    OperationAction.ACCOUNT_UNBLOCK: "🔐",
    OperationAction.PASSWORD_RESET: "🔑",
    OperationAction.OTP_NUMBER_CHANGE: "📱",
    OperationAction.VPN_ACCESS: "🛠️",
    "unknown": "🗑️",
}

_STATUS_MAP: dict[str, str] = {
    "COMPLETED": "success",
    "EXECUTING": "processing",
    "ESCALATED": "escalated",
    "FAILED": "rejected",
    "CANCELLED": "rejected",
    "NEW": "pending",
    "ANALYZING": "pending",
    "ACTIVE": "pending",
    "NEEDS_INFORMATION": "pending",
    "READY_FOR_VALIDATION": "pending",
    "PARTIALLY_COMPLETED": "processing",
}


def _extract_confidence(operation: Operation) -> float | None:
    """Extract confidence as a 0-100 percentage, normalizing from either scale."""
    for source in (operation.analyzer_confidence, operation.verifier_confidence):
        if not source:
            continue
        for key in (
            "raw_model_confidence",
            "raw_action_confidence",
            "raw_confidence",
            "confidence",
        ):
            val = source.get(key)
            if val is not None:
                try:
                    num = float(val)
                    # Normalize: if value is in 0-1 range, scale to 0-100
                    return num * 100 if 0 < num <= 1 else num
                except (TypeError, ValueError):
                    pass
    return None


def _normalize_status(operation: Operation, request: BusinessRequest) -> str:
    op_status = operation.status
    mapped = _STATUS_MAP.get(op_status)
    if mapped:
        return mapped
    req_mapped = _STATUS_MAP.get(request.status)
    if req_mapped:
        return req_mapped
    return "pending"


def _infer_zone(email: EmailMessage | None) -> str:
    if email is None:
        return "Unknown"
    sender = (email.sender or "").lower()
    for keyword, zone in (
        ("east", "East Region"),
        ("west", "West Region"),
        ("north", "North Region"),
        ("south", "South Region"),
        ("center", "North Region"),
        ("centre", "North Region"),
    ):
        if keyword in sender:
            return zone
    return "Unknown"


def _build_request_row(
    operation: Operation,
    request: BusinessRequest,
    email: EmailMessage | None,
    *,
    execution: Execution | None = None,
    outbox: OutboxMessage | None = None,
    validation_decision: ValidationDecision | None = None,
    workflow_run: WorkflowRun | None = None,
) -> DashboardRequest:
    intent = _INTENT_LABELS.get(operation.action, operation.action)
    status = _normalize_status(operation, request)
    confidence = _extract_confidence(operation)
    pdv = operation.pdv_code
    phone = operation.phone
    zone = _infer_zone(email)
    created_at = request.created_at
    created_str = utc_iso(created_at)
    body = email.raw_text if email else ""
    cleaned = email.latest_user_message if email else ""
    subject = email.subject if email else ""
    recipients = list(email.recipients_json or []) if email else []

    entities = EntityInfo(pdv_code=pdv, pdv=pdv, phone_number=phone, phone=phone)

    execution_details = None
    if execution is not None:
        execution_elapsed = elapsed_seconds(execution.created_at, execution.updated_at)
        execution_details = ExecutionDetails(
            status=execution.status,
            endpoint=execution.endpoint,
            message=str((execution.response_body or {}).get("message") or "") or None,
            response_status=execution.response_status,
            dry_run=execution.dry_run,
            attempt_count=execution.attempt_count,
            latency_ms=(round(execution_elapsed * 1000) if execution_elapsed is not None else None),
            request_payload=execution.request_payload or {},
            response_body=execution.response_body or {},
        )

    metadata = RequestMetadata(execution_details=execution_details) if execution_details else None

    request_elapsed = None
    if workflow_run is not None:
        request_elapsed = elapsed_seconds(workflow_run.started_at, workflow_run.completed_at)
    if request_elapsed is None:
        request_elapsed = elapsed_seconds(request.created_at, request.completed_at)

    missing_fields = list(operation.missing_fields or [])
    decision_reasons = list(validation_decision.reasons or []) if validation_decision else []
    if missing_fields:
        rendered_fields = [
            "OTP/phone" if field in {"phone", "phone_number", "otp"} else field
            for field in missing_fields
        ]
        validation_error = f"Missing required fields: {', '.join(rendered_fields)}"
    elif decision_reasons:
        validation_error = "; ".join(str(reason) for reason in decision_reasons)
    else:
        validation_error = None

    assignment = request.human_assignment or {}
    assigned_user = (
        assignment.get("name")
        or assignment.get("username")
        or assignment.get("assignee")
        or assignment.get("assigned_to")
    )

    return DashboardRequest(
        request_id=request.public_reference,
        email_message_id=str(email.id) if email else None,
        operation_id=str(operation.id),
        execution_occurred=execution is not None,
        intent=intent,
        request_type=operation.action,
        confidence=confidence,
        request_status=status,
        decision=operation.final_decision,
        execution_status=operation.status,
        sender=email.sender if email else "unknown",
        recipient=recipients[0] if recipients else None,
        zone=zone,
        created_at=created_str,
        duration_ms=round(request_elapsed * 1000) if request_elapsed is not None else None,
        body_text=body,
        cleaned_text=cleaned,
        subject=subject,
        attachments=list(email.attachment_metadata or []) if email else [],
        detected_language=None,
        validation_error=validation_error,
        missing_fields=missing_fields,
        assigned_user=str(assigned_user) if assigned_user else None,
        reply_recipient=outbox.recipient if outbox else None,
        reply_subject=outbox.subject if outbox else None,
        reply_text=outbox.body if outbox else None,
        reply_status=outbox.status if outbox else None,
        entities=entities,
        metadata=metadata,
    )


def _build_alerts(session: Session) -> list[DashboardAlert]:
    escalations = (
        session.query(Escalation)
        .filter(Escalation.status == "open")
        .order_by(Escalation.created_at.desc())
        .limit(20)
        .all()
    )
    alerts: list[DashboardAlert] = []
    for esc in escalations:
        alerts.append(
            DashboardAlert(
                id=str(esc.id),
                severity="warning" if esc.reason_code != "low_confidence" else "critical",
                message=esc.summary or f"Escalation: {esc.reason_code}",
                time=esc.created_at.strftime("%H:%M:%S") if esc.created_at else "",
                region="Unknown",
                status="Active",
            )
        )
    return alerts


def _build_stats(session: Session, *, cutoff: datetime) -> DashboardStats:
    request_query = session.query(BusinessRequest).filter(BusinessRequest.created_at >= cutoff)
    total = request_query.count()
    completed = request_query.filter(
        BusinessRequest.status == RequestStatus.COMPLETED.value
    ).count()
    escalated = request_query.filter(
        BusinessRequest.status == RequestStatus.ESCALATED.value
    ).count()
    failed = request_query.filter(
        BusinessRequest.status.in_({RequestStatus.FAILED.value, RequestStatus.CANCELLED.value})
    ).count()
    in_progress = request_query.filter(
        BusinessRequest.status.in_(
            {
                RequestStatus.ANALYZING.value,
                RequestStatus.ACTIVE.value,
                RequestStatus.PARTIALLY_COMPLETED.value,
                RequestStatus.READY_FOR_VALIDATION.value,
            }
        )
    ).count()
    pending = request_query.filter(
        BusinessRequest.status.in_({RequestStatus.NEW.value, RequestStatus.NEEDS_INFORMATION.value})
    ).count()

    operations = (
        session.query(Operation)
        .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
        .filter(BusinessRequest.created_at >= cutoff)
        .all()
    )

    # Count low-confidence predictions (threshold: 70%)
    low_conf = 0
    for o in operations:
        conf = _extract_confidence(o)
        if conf is not None and conf < 70.0:
            low_conf += 1

    # Count missing entity fields
    missing_entities = sum(1 for o in operations if o.missing_fields)

    # Unauthorized inbound emails never create business requests, so count the
    # persisted security decision directly instead of inferring it from operations.
    unauthorized = (
        session.query(EmailMessage)
        .filter(
            EmailMessage.direction == "inbound",
            EmailMessage.created_at >= cutoff,
            EmailMessage.authorization_allowed.is_(False),
        )
        .count()
    )

    return DashboardStats(
        total_requests=total,
        successful_executions=completed,
        escalated=escalated,
        rejected=failed,
        pending_requests=max(0, pending),
        in_progress=in_progress,
        failed=failed,
        missing_entities=missing_entities,
        unauthorized=unauthorized,
        low_confidence=low_conf,
    )


def build_dashboard_payload(
    session: Session,
    agent_active: bool = True,
    authorized_senders: set[str] | None = None,
    *,
    since: datetime | None = None,
) -> DashboardPayload:
    """Query the database and build the full dashboard response."""
    cutoff = since or (utc_now() - timedelta(days=7))

    requests = (
        session.query(BusinessRequest)
        .filter(BusinessRequest.created_at >= cutoff)
        .order_by(BusinessRequest.created_at.desc())
        .limit(200)
        .all()
    )

    request_ids = [r.id for r in requests]
    operations = session.query(Operation).filter(Operation.request_id.in_(request_ids)).all()
    op_by_request: dict[str, list[Operation]] = {}
    for op in operations:
        op_by_request.setdefault(str(op.request_id), []).append(op)

    email_ids = set()
    for r in requests:
        email_ids.add(r.initiating_email_id)
    emails = (
        (session.query(EmailMessage).filter(EmailMessage.id.in_(email_ids)).all())
        if email_ids
        else []
    )
    email_map = {str(e.id): e for e in emails}

    executions = (
        session.query(Execution)
        .filter(Execution.operation_id.in_([operation.id for operation in operations]))
        .order_by(Execution.created_at.desc())
        .all()
        if operations
        else []
    )
    execution_by_operation: dict[str, Execution] = {}
    for execution in executions:
        execution_by_operation.setdefault(str(execution.operation_id), execution)

    decisions = (
        session.query(ValidationDecision)
        .filter(ValidationDecision.operation_id.in_([operation.id for operation in operations]))
        .order_by(ValidationDecision.created_at.desc())
        .all()
        if operations
        else []
    )
    decision_by_operation: dict[str, ValidationDecision] = {}
    for decision in decisions:
        decision_by_operation.setdefault(str(decision.operation_id), decision)

    outbox_messages = (
        session.query(OutboxMessage)
        .filter(OutboxMessage.related_request_id.in_(request_ids))
        .order_by(OutboxMessage.created_at.desc())
        .all()
        if request_ids
        else []
    )
    outbox_by_request: dict[str, OutboxMessage] = {}
    for message in outbox_messages:
        if message.related_request_id is not None:
            outbox_by_request.setdefault(str(message.related_request_id), message)

    workflow_runs = (
        session.query(WorkflowRun)
        .filter(WorkflowRun.inbound_email_id.in_(email_ids))
        .order_by(WorkflowRun.started_at.desc())
        .all()
        if email_ids
        else []
    )
    workflow_by_email: dict[str, WorkflowRun] = {}
    for workflow_run in workflow_runs:
        if workflow_run.inbound_email_id is not None:
            workflow_by_email.setdefault(str(workflow_run.inbound_email_id), workflow_run)

    rows: list[DashboardRequest] = []
    for req in requests:
        ops = op_by_request.get(str(req.id), [])
        email = email_map.get(str(req.initiating_email_id))
        for op in ops:
            rows.append(
                _build_request_row(
                    op,
                    req,
                    email,
                    execution=execution_by_operation.get(str(op.id)),
                    outbox=outbox_by_request.get(str(req.id)),
                    validation_decision=decision_by_operation.get(str(op.id)),
                    workflow_run=workflow_by_email.get(str(req.initiating_email_id)),
                )
            )

    alerts = _build_alerts(session)
    stats = _build_stats(session, cutoff=cutoff)

    return DashboardPayload(
        agent_active=agent_active,
        requests=rows,
        alerts=alerts,
        stats=stats,
    )
