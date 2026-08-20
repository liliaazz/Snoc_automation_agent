"""Dashboard API router for the React frontend with real DB queries."""

from __future__ import annotations

import builtins
import contextlib
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, or_

from snoc_agent.api.auth import current_principal
from snoc_agent.datetime_utils import elapsed_seconds, ensure_utc, utc_iso, utc_now

router = APIRouter(
    prefix="/api/snoc", tags=["dashboard"], dependencies=[Depends(current_principal)]
)

DashboardPeriod = Literal["day", "week", "month", "year"]
_PERIOD_DAYS: dict[DashboardPeriod, int] = {
    "day": 1,
    "week": 7,
    "month": 30,
    "year": 365,
}


def _now_iso() -> str:
    return utc_now().isoformat()


def _period_cutoff(
    period: DashboardPeriod | None,
    range_value: DashboardPeriod | None,
) -> datetime:
    if period is not None and range_value is not None and period != range_value:
        raise HTTPException(status_code=400, detail="period and range must match when both are set")
    selected = period or range_value or "week"
    return utc_now() - timedelta(days=_PERIOD_DAYS[selected])


def _get_session(request: Request):
    return request.app.state.session_factory()


def _close_session(session) -> None:
    with contextlib.suppress(Exception):
        session.close()


@router.get("/dashboard/summary")
async def dashboard_summary(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return dashboard summary with operational and data quality metrics from the DB."""
    settings = request.app.state.settings
    session = _get_session(request)
    try:
        from snoc_agent.api.queries import build_dashboard_payload

        payload = build_dashboard_payload(
            session,
            agent_active=request.app.state.agent_active,
            authorized_senders=settings.authorized_sender_set,
            since=_period_cutoff(period, range),
        )
        stats = payload.stats
        from snoc_agent.db.models import BusinessRequest, EmailMessage, Operation
        from snoc_agent.domain.enums import (
            Direction,
            OperationStatus,
            ProcessingStatus,
            RequestStatus,
        )

        cutoff = _period_cutoff(period, range)
        total_emails = (
            session.query(EmailMessage)
            .filter(
                EmailMessage.direction == Direction.INBOUND.value, EmailMessage.created_at >= cutoff
            )
            .count()
        )
        total_operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(BusinessRequest.created_at >= cutoff)
            .count()
        )
        completed_operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(
                BusinessRequest.created_at >= cutoff,
                Operation.status == OperationStatus.COMPLETED.value,
            )
            .count()
        )
        pending_operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(
                BusinessRequest.created_at >= cutoff,
                Operation.status.in_(
                    {
                        OperationStatus.NEW.value,
                        OperationStatus.NEEDS_INFORMATION.value,
                        OperationStatus.READY_FOR_VALIDATION.value,
                        OperationStatus.EXECUTING.value,
                    }
                ),
            )
            .count()
        )
        automatically_resolved_requests = (
            session.query(BusinessRequest)
            .filter(
                BusinessRequest.created_at >= cutoff,
                BusinessRequest.status == RequestStatus.COMPLETED.value,
            )
            .count()
        )
        rejected_emails = (
            session.query(EmailMessage)
            .filter(
                EmailMessage.direction == Direction.INBOUND.value,
                EmailMessage.created_at >= cutoff,
                EmailMessage.processing_status.in_(
                    {ProcessingStatus.QUARANTINED.value, ProcessingStatus.FAILED.value}
                ),
            )
            .count()
        )
        unauthorized_emails = (
            session.query(EmailMessage)
            .filter(
                EmailMessage.direction == Direction.INBOUND.value,
                EmailMessage.created_at >= cutoff,
                EmailMessage.authorization_allowed.is_(False),
            )
            .count()
        )
        failed_operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(
                BusinessRequest.created_at >= cutoff,
                Operation.status.in_(
                    {OperationStatus.FAILED.value, OperationStatus.CANCELLED.value}
                ),
            )
            .count()
        )

        # Compute data quality metrics from real data
        dq = _compute_data_quality(session)

        return {
            "generatedAt": _now_iso(),
            "operational": {
                "totalRequests": stats.total_requests,
                "totalEmailsReceived": total_emails,
                "uniqueBusinessRequests": stats.total_requests,
                "totalOperations": total_operations,
                "successfulExecutions": stats.successful_executions,
                "automaticallyResolvedRequests": automatically_resolved_requests,
                "completedOperations": completed_operations,
                "escalated": stats.escalated,
                "rejected": stats.rejected,
                "rejectedEmails": rejected_emails,
                "pendingRequests": stats.pending_requests,
                "pendingOperations": pending_operations,
                "inProgress": stats.in_progress,
                "failed": stats.failed,
                "unauthorized": unauthorized_emails,
                "unauthorizedEmails": unauthorized_emails,
                "failedOperations": failed_operations,
                "lowConfidence": stats.low_confidence,
            },
            "dataQuality": dq,
        }
    finally:
        _close_session(session)


@router.get("/dashboard/trends")
async def dashboard_trends(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return trend data computed from real request/operation counts over the past 7 days."""
    session = _get_session(request)
    try:
        from snoc_agent.db.models import BusinessRequest, EmailMessage
        from snoc_agent.domain.enums import Direction, ProcessingStatus, RequestStatus

        now = utc_now()
        cutoff = _period_cutoff(period, range)
        day_count = min(_PERIOD_DAYS[period or range or "week"], 31)
        items = []
        for i in builtins.range(day_count - 1, -1, -1):
            day_start = (now - timedelta(days=i)).replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = day_start + timedelta(days=1)
            day_requests = (
                session.query(BusinessRequest)
                .filter(
                    BusinessRequest.created_at >= max(day_start, cutoff),
                    BusinessRequest.created_at < day_end,
                )
                .count()
            )
            day_received = (
                session.query(EmailMessage)
                .filter(
                    EmailMessage.direction == Direction.INBOUND.value,
                    EmailMessage.created_at >= max(day_start, cutoff),
                    EmailMessage.created_at < day_end,
                )
                .count()
            )
            day_resolved = (
                session.query(BusinessRequest)
                .filter(
                    BusinessRequest.created_at >= max(day_start, cutoff),
                    BusinessRequest.created_at < day_end,
                    BusinessRequest.status == RequestStatus.COMPLETED.value,
                )
                .count()
            )
            day_escalated = (
                session.query(BusinessRequest)
                .filter(
                    BusinessRequest.created_at >= max(day_start, cutoff),
                    BusinessRequest.created_at < day_end,
                    BusinessRequest.status == RequestStatus.ESCALATED.value,
                )
                .count()
            )
            day_failed = (
                session.query(BusinessRequest)
                .filter(
                    BusinessRequest.created_at >= max(day_start, cutoff),
                    BusinessRequest.created_at < day_end,
                    BusinessRequest.status.in_(
                        {RequestStatus.FAILED.value, RequestStatus.CANCELLED.value}
                    ),
                )
                .count()
            )
            day_rejected_emails = (
                session.query(EmailMessage)
                .filter(
                    EmailMessage.direction == Direction.INBOUND.value,
                    EmailMessage.created_at >= max(day_start, cutoff),
                    EmailMessage.created_at < day_end,
                    EmailMessage.processing_status.in_(
                        {ProcessingStatus.QUARANTINED.value, ProcessingStatus.FAILED.value}
                    ),
                )
                .count()
            )
            items.append(
                {
                    "date": day_start.strftime("%Y-%m-%d"),
                    "received": day_received,
                    "requests": day_requests,
                    "resolved": day_resolved,
                    "escalated": day_escalated,
                    "failed": day_failed + day_rejected_emails,
                }
            )
        return {"items": items}
    finally:
        _close_session(session)


@router.get("/dashboard/intents")
async def dashboard_intents(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return intent distribution from real operation action data."""
    session = _get_session(request)
    try:
        from snoc_agent.db.models import BusinessRequest, Operation
        from snoc_agent.domain.enums import OperationAction

        cutoff = _period_cutoff(period, range)

        operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(BusinessRequest.created_at >= cutoff)
            .all()
        )

        intent_labels = {
            OperationAction.ACCOUNT_UNBLOCK: "Account Unblock",
            OperationAction.PASSWORD_RESET: "Password Reset",
            OperationAction.OTP_NUMBER_CHANGE: "OTP Change",
            OperationAction.VPN_ACCESS: "VPN Access",
            "unknown": "Other",
        }

        counts: dict[str, int] = {}
        for op in operations:
            label = intent_labels.get(op.action, "Other")
            counts[label] = counts.get(label, 0) + 1

        total = max(sum(counts.values()), 1)
        items = [
            {"intent": intent, "count": count, "percentage": round(count / total * 100, 1)}
            for intent, count in sorted(counts.items(), key=lambda x: -x[1])
        ]
        return {"items": items}
    finally:
        _close_session(session)


@router.get("/dashboard/recent")
async def dashboard_recent(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return recent requests."""
    session = _get_session(request)
    try:
        from snoc_agent.api.queries import _infer_zone, build_dashboard_payload
        from snoc_agent.api.schemas import DashboardEmailSecurityEvent
        from snoc_agent.db.models import BusinessRequest, EmailMessage
        from snoc_agent.domain.enums import Direction, ProcessingStatus

        settings = request.app.state.settings
        cutoff = _period_cutoff(period, range)
        payload = build_dashboard_payload(
            session,
            agent_active=request.app.state.agent_active,
            authorized_senders=settings.authorized_sender_set,
            since=cutoff,
        )
        security_emails = (
            session.query(EmailMessage)
            .outerjoin(
                BusinessRequest,
                BusinessRequest.initiating_email_id == EmailMessage.id,
            )
            .filter(
                BusinessRequest.id.is_(None),
                EmailMessage.direction == Direction.INBOUND.value,
                EmailMessage.created_at >= cutoff,
                or_(
                    EmailMessage.authorization_allowed.is_(False),
                    EmailMessage.processing_status.in_(
                        {
                            ProcessingStatus.QUARANTINED.value,
                            ProcessingStatus.FAILED.value,
                        }
                    ),
                ),
            )
            .order_by(EmailMessage.created_at.desc())
            .all()
        )
        security_events: list[DashboardEmailSecurityEvent] = []
        for email in security_emails:
            unauthorized = email.authorization_allowed is False
            recipients = list(email.recipients_json or [])
            persisted_reasons = [
                email.authorization_reason,
                email.quarantine_message,
                "; ".join(str(item) for item in (email.parsing_warnings or [])) or None,
            ]
            security_events.append(
                DashboardEmailSecurityEvent(
                    email_message_id=str(email.id),
                    request_status="UNAUTHORIZED" if unauthorized else "REJECTED",
                    decision="REJECT" if unauthorized else None,
                    sender=email.sender,
                    recipient=recipients[0] if recipients else None,
                    zone=_infer_zone(email),
                    created_at=utc_iso(
                        email.message_date or email.internal_date or email.created_at
                    ),
                    body_text=email.raw_text,
                    cleaned_text=email.latest_user_message,
                    subject=email.subject,
                    attachments=list(email.attachment_metadata or []),
                    validation_error=next(
                        (reason for reason in persisted_reasons if reason),
                        None,
                    ),
                    authorization_allowed=email.authorization_allowed,
                    authorization_reason=email.authorization_reason,
                    processing_status=email.processing_status,
                )
            )

        # ``build_dashboard_payload`` is already bounded to 200 business
        # requests. Returning the complete bounded set keeps audit-derived
        # charts aligned with the selected period instead of silently using
        # only the newest 20 operation rows.
        items = [
            *(req.model_dump() for req in payload.requests),
            *(event.model_dump() for event in security_events),
        ]
        items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return {"items": items}
    finally:
        _close_session(session)


def _compute_data_quality(session) -> dict[str, Any]:
    """Compute data quality metrics from real DB records."""
    from snoc_agent.db.models import Operation

    total_ops = session.query(Operation).count()
    if total_ops == 0:
        return {"completeness": 0.0, "accuracy": 0.0, "timeliness": 0.0, "consistency": 0.0}

    # Completeness: % of operations with non-null PDV and phone where required
    ops_with_pdv = session.query(Operation).filter(Operation.pdv_code.isnot(None)).count()
    completeness = round(ops_with_pdv / max(total_ops, 1) * 100, 1)

    # Accuracy: % of operations with analyzer confidence > 0.7 (proxy)
    ops_with_confidence = 0
    confident_ops = 0
    all_ops = session.query(Operation).all()
    for op in all_ops:
        if op.analyzer_confidence:
            ops_with_confidence += 1
            for key in (
                "raw_model_confidence",
                "raw_action_confidence",
                "raw_confidence",
                "confidence",
            ):
                val = op.analyzer_confidence.get(key)
                if val is not None:
                    try:
                        num = float(val)
                        if num > 0.7:
                            confident_ops += 1
                        break
                    except (TypeError, ValueError):
                        pass
    accuracy = round(confident_ops / max(ops_with_confidence, 1) * 100, 1)

    # Timeliness: % of operations completed (proxy for processing timeliness)
    completed = session.query(Operation).filter(Operation.status == "COMPLETED").count()
    timeliness = round(completed / max(total_ops, 1) * 100, 1)

    # Consistency: % of operations with no missing fields
    ops_with_missing = (
        session.query(Operation)
        .filter(
            Operation.missing_fields.isnot(None),
            func.json_array_length(Operation.missing_fields) > 0,
        )
        .count()
        if hasattr(func, "json_array_length")
        else 0
    )
    consistency = round((total_ops - ops_with_missing) / max(total_ops, 1) * 100, 1)

    return {
        "completeness": completeness,
        "accuracy": accuracy,
        "timeliness": timeliness,
        "consistency": consistency,
    }


@router.get("/dq/executive")
async def dq_executive(request: Request) -> dict[str, Any]:
    """Return data quality executive summary from real data."""
    session = _get_session(request)
    try:
        dq = _compute_data_quality(session)
        overall = round(sum(dq.values()) / max(len(dq), 1), 1)
        dq["overall"] = overall
        return dq
    finally:
        _close_session(session)


@router.get("/dq/dimensions")
async def dq_dimensions(request: Request) -> dict[str, Any]:
    """Return data quality dimensions from real data."""
    session = _get_session(request)
    try:
        dq = _compute_data_quality(session)
        items = [
            {"dimension": "Completeness", "score": dq["completeness"], "trend": "stable"},
            {"dimension": "Accuracy", "score": dq["accuracy"], "trend": "improving"},
            {"dimension": "Timeliness", "score": dq["timeliness"], "trend": "stable"},
            {"dimension": "Consistency", "score": dq["consistency"], "trend": "stable"},
        ]
        return {"items": items}
    finally:
        _close_session(session)


@router.get("/dq/rules")
async def dq_rules(request: Request) -> dict[str, Any]:
    """Return data quality rules computed from real validation data."""
    session = _get_session(request)
    try:
        from snoc_agent.db.models import Operation

        total = session.query(Operation).count()
        if total == 0:
            return {"items": []}

        all_ops = session.query(Operation).all()
        pdv_valid = sum(1 for o in all_ops if o.pdv_code and len(o.pdv_code) == 8)
        phone_applicable = [
            operation for operation in all_ops if operation.action == "otp_number_change"
        ]
        phone_valid = sum(
            1
            for operation in phone_applicable
            if operation.phone and 9 <= len(operation.phone.replace("+", "")) <= 15
        )
        confidence_ok = 0
        for o in all_ops:
            if o.analyzer_confidence:
                for key in ("raw_model_confidence", "raw_action_confidence", "confidence"):
                    val = o.analyzer_confidence.get(key)
                    if val is not None:
                        try:
                            if float(val) >= 0.7:
                                confidence_ok += 1
                        except (TypeError, ValueError):
                            pass
        missing_fields_count = sum(
            1 for o in all_ops if o.missing_fields and len(o.missing_fields) > 0
        )
        sla_ok = total - missing_fields_count

        items = [
            {
                "id": "R001",
                "name": "PDV Format Validation",
                "enabled": True,
                "threshold": 95,
                "currentScore": round(pdv_valid / max(total, 1) * 100, 1),
            },
            {
                "id": "R002",
                "name": "Phone Number Format",
                "enabled": True,
                "threshold": 90,
                "currentScore": (
                    round(phone_valid / len(phone_applicable) * 100, 1)
                    if phone_applicable
                    else None
                ),
            },
            {
                "id": "R003",
                "name": "Email Content Analysis",
                "enabled": True,
                "threshold": 85,
                "currentScore": round(confidence_ok / max(total, 1) * 100, 1),
            },
            {
                "id": "R004",
                "name": "Response Time SLA",
                "enabled": True,
                "threshold": 80,
                "currentScore": round(sla_ok / max(total, 1) * 100, 1),
            },
        ]
        return {"items": items}
    finally:
        _close_session(session)


@router.get("/model/snapshot")
async def model_snapshot(request: Request) -> dict[str, Any]:
    """Return model performance snapshot from real ModelRun data."""
    session = _get_session(request)
    try:
        from snoc_agent.ai.provider import LLMProvider
        from snoc_agent.db.models import EvaluationRun, ModelRun

        settings = request.app.state.settings
        provider = settings.effective_analyzer_provider
        if provider == LLMProvider.VLLM:
            configured_model = next(
                deployment.model_id
                for deployment in settings.vllm_deployments
                if deployment.name == settings.vllm_analyzer_deployment
            )
        else:
            configured_model = settings.analyzer_model

        latest_evaluation = (
            session.query(EvaluationRun)
            .filter(EvaluationRun.status == "complete")
            .order_by(EvaluationRun.completed_at.desc(), EvaluationRun.created_at.desc())
            .first()
        )
        dataset_rows = None
        if latest_evaluation is not None:
            configured_rows = (latest_evaluation.configuration or {}).get("selected_example_count")
            try:
                dataset_rows = int(configured_rows) if configured_rows is not None else None
            except (TypeError, ValueError):
                dataset_rows = None

        runs = session.query(ModelRun).order_by(ModelRun.created_at.desc()).limit(100).all()
        if not runs:
            return {
                "provider": provider.value,
                "modelName": configured_model,
                "available": False,
                "lastSuccessfulInference": None,
                "recentRuns": [],
                "errorCount": 0,
                "accuracy": None,
                "precision": None,
                "recall": None,
                "f1Score": None,
                "structuredOutputValidityRate": None,
                "successRate": None,
                "datasetRows": dataset_rows,
                "latencyMs": None,
                "throughputPerMinute": 0,
                "dryRun": settings.dry_run,
                "fallbackOccurred": False,
                "lastUpdated": _now_iso(),
            }

        valid_runs = [r for r in runs if r.structured_output_valid]
        total_runs = len(runs)
        valid_count = len(valid_runs)
        structured_output_validity = round(valid_count / max(total_runs, 1) * 100, 1)

        latencies = [r.latency_seconds for r in runs if r.latency_seconds is not None]
        avg_latency_ms = round(sum(latencies) / len(latencies) * 1000, 0) if latencies else None

        # Throughput: runs per minute in the last hour
        from datetime import timedelta

        cutoff = utc_now() - timedelta(hours=1)
        recent_runs = [
            run
            for run in runs
            if (created_at := ensure_utc(run.created_at)) is not None and created_at >= cutoff
        ]
        throughput = round(len(recent_runs) / 60, 2)
        successful_runs = [run for run in runs if run.structured_output_valid and not run.error]
        last_successful = successful_runs[0] if successful_runs else None
        latest = runs[0]
        fallback_occurred = any(bool(run.fallback_reason) for run in runs)

        return {
            "provider": latest.reported_provider or latest.backend or provider.value,
            "modelName": latest.resolved_model_id or latest.model_name or configured_model,
            "configuredModelName": configured_model,
            "available": last_successful is not None,
            "lastSuccessfulInference": (
                utc_iso(last_successful.created_at) if last_successful is not None else None
            ),
            "recentRuns": [
                {
                    "stage": run.stage,
                    "provider": run.reported_provider or run.backend,
                    "model": run.resolved_model_id or run.model_name,
                    "successful": bool(run.structured_output_valid and not run.error),
                    "errorCategory": run.error_category,
                    "latencyMs": (
                        round(run.latency_seconds * 1000, 2)
                        if run.latency_seconds is not None
                        else None
                    ),
                    "createdAt": utc_iso(run.created_at),
                    "fallbackOccurred": bool(run.fallback_reason),
                }
                for run in runs[:20]
            ],
            "errorCount": sum(1 for run in runs if run.error or not run.structured_output_valid),
            # Runtime telemetry has no labelled ground truth. Classification
            # metrics therefore remain unknown rather than presenting schema
            # validity as model quality.
            "accuracy": None,
            "precision": None,
            "recall": None,
            "f1Score": None,
            "structuredOutputValidityRate": structured_output_validity,
            "successRate": structured_output_validity,
            "datasetRows": dataset_rows,
            "latencyMs": int(avg_latency_ms) if avg_latency_ms is not None else None,
            "throughputPerMinute": throughput,
            "dryRun": settings.dry_run,
            "fallbackOccurred": fallback_occurred,
            "lastUpdated": _now_iso(),
        }
    finally:
        _close_session(session)


@router.get("/workflow/health")
async def workflow_health(request: Request) -> dict[str, Any]:
    """Return workflow health from real WorkflowRun/WorkflowEvent data."""
    session = _get_session(request)
    try:
        from snoc_agent.db.models import WorkflowEvent, WorkflowRun

        recent_runs = (
            session.query(WorkflowRun).order_by(WorkflowRun.started_at.desc()).limit(50).all()
        )

        if not recent_runs:
            return {
                "status": "unknown",
                "uptime": None,
                "lastFailure": None,
                "avgProcessingTime": None,
                "queueDepth": 0,
                "agents": {
                    "ingress": {
                        "status": "unknown",
                        "latencyMs": None,
                        "processed": 0,
                        "errors": 0,
                        "lastSuccess": None,
                    },
                    "security": {
                        "status": "unknown",
                        "latencyMs": None,
                        "processed": 0,
                        "errors": 0,
                        "lastSuccess": None,
                    },
                    "nlu": {
                        "status": "unknown",
                        "latencyMs": None,
                        "processed": 0,
                        "errors": 0,
                        "lastSuccess": None,
                    },
                    "policy": {
                        "status": "unknown",
                        "latencyMs": None,
                        "processed": 0,
                        "errors": 0,
                        "lastSuccess": None,
                    },
                    "fulfilment": {
                        "status": "unknown",
                        "latencyMs": None,
                        "processed": 0,
                        "errors": 0,
                        "lastSuccess": None,
                    },
                },
            }

        failed_runs = [r for r in recent_runs if r.status == "failed"]
        last_failure = None
        if failed_runs:
            last_failure = utc_iso(failed_runs[0].completed_at)

        completed_runs = [
            r for r in recent_runs if r.status == "completed" and r.started_at and r.completed_at
        ]
        avg_processing = 0.0
        if completed_runs:
            durations = [
                duration
                for run in completed_runs
                if (duration := elapsed_seconds(run.started_at, run.completed_at)) is not None
            ]
            avg_processing = round(sum(durations) / len(durations), 2)

        # Compute per-agent latency from events
        agent_names = ["ingress", "security", "nlu", "policy", "fulfilment"]
        agents = {}
        for agent_name in agent_names:
            events = (
                session.query(WorkflowEvent)
                .filter(WorkflowEvent.agent == agent_name)
                .order_by(WorkflowEvent.started_at.desc())
                .limit(20)
                .all()
            )
            latencies = [
                duration * 1000
                for e in events
                if (duration := elapsed_seconds(e.started_at, e.completed_at)) is not None
            ]
            avg_ms = int(sum(latencies) / len(latencies)) if latencies else None
            failed_events = [e for e in events if e.status == "failed"]
            if not events:
                status = "unknown"
            else:
                status = "degraded" if len(failed_events) > len(events) * 0.2 else "healthy"
            successful_events = [
                event
                for event in events
                if event.status in {"completed", "succeeded", "terminal"}
                and event.completed_at is not None
            ]
            agents[agent_name] = {
                "status": status,
                "latencyMs": avg_ms,
                "processed": len(events),
                "errors": len(failed_events),
                "lastSuccess": (
                    utc_iso(successful_events[0].completed_at) if successful_events else None
                ),
            }

        overall_status = "healthy"
        if len(failed_runs) > len(recent_runs) * 0.5:
            overall_status = "unhealthy"
        elif len(failed_runs) > len(recent_runs) * 0.3:
            overall_status = "degraded"

        # Estimate queue depth from running workflows
        running = session.query(WorkflowRun).filter(WorkflowRun.status == "running").count()

        return {
            "status": overall_status,
            "uptime": None,
            "lastFailure": last_failure,
            "avgProcessingTime": avg_processing,
            "queueDepth": running,
            "agents": agents,
        }
    finally:
        _close_session(session)


@router.get("/frontend/runtime")
async def frontend_runtime(request: Request) -> dict[str, Any]:
    """Expose non-secret effective runtime configuration."""

    settings = request.app.state.settings
    provider = settings.effective_llm_provider
    return {
        "mode": "dry_run" if settings.dry_run else "live",
        "provider": provider.value,
        "analyzer_model": (
            next(
                deployment.model_id
                for deployment in settings.vllm_deployments
                if deployment.name == settings.vllm_analyzer_deployment
            )
            if provider.value == "vllm"
            else settings.analyzer_model
        ),
        "verifier_model": (
            next(
                deployment.model_id
                for deployment in settings.vllm_deployments
                if deployment.name == settings.vllm_verifier_deployment
            )
            if provider.value == "vllm"
            else settings.verifier_model
        ),
        "imap_configured": bool(settings.imap_host and settings.imap_username),
        "inbox_address": settings.imap_username or None,
        "imap_mailbox": settings.imap_mailbox,
        "imap_search_criterion": settings.imap_search_criterion,
        "smtp_mode": (
            "disabled"
            if not settings.outbound_email_enabled
            else "configured"
            if settings.smtp_host
            else "fake"
        ),
        "business_api_mode": "simulated" if settings.dry_run else "live",
        "authentication_configured": bool(
            settings.dashboard_admin_username
            and settings.dashboard_admin_password.get_secret_value()
            and settings.auth_jwt_secret.get_secret_value()
        ),
        "analyzer_min_raw_confidence": settings.analyzer_min_raw_confidence,
        "verifier_min_raw_confidence": settings.verifier_min_raw_confidence,
        "workflow_engine": settings.workflow_engine,
    }


@router.get("/frontend/analytics/confidence")
async def confidence_analytics(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return operation-level measured confidence statistics."""

    from snoc_agent.db.models import BusinessRequest, Operation

    session = _get_session(request)
    try:
        operations = (
            session.query(Operation)
            .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
            .filter(BusinessRequest.created_at >= _period_cutoff(period, range))
            .all()
        )
        values = [
            value
            for operation in operations
            if (value := _operation_confidence(operation)) is not None
        ]
        threshold = request.app.state.settings.analyzer_min_raw_confidence
        effective_threshold = threshold if threshold is not None else 0.7
        return {
            "total_operations": len(operations),
            "measured_operations": len(values),
            "unmeasured_operations": len(operations) - len(values),
            "average_confidence": sum(values) / len(values) if values else None,
            "low_confidence_count": sum(value < effective_threshold for value in values),
            "threshold": threshold,
            "buckets": _confidence_buckets(values),
        }
    finally:
        _close_session(session)


def _operation_confidence(operation: Any) -> float | None:
    for source in (operation.analyzer_confidence, operation.verifier_confidence):
        for key in ("raw_action_confidence", "raw_model_confidence", "raw_confidence"):
            raw = (source or {}).get(key)
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if 0 <= value <= 1:
                return value
    return None


def _confidence_buckets(values: list[float]) -> list[dict[str, Any]]:
    boundaries = (
        ("0-25%", 0.0, 0.25),
        ("25-50%", 0.25, 0.5),
        ("50-75%", 0.5, 0.75),
        ("75-100%", 0.75, 1.0000001),
    )
    return [
        {
            "label": label,
            "count": sum(lower <= value < upper for value in values),
        }
        for label, lower, upper in boundaries
    ]


@router.get("/frontend/analytics/missing-entities")
async def missing_entity_analytics(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return operation-level missing-entity counts grouped by action."""

    from snoc_agent.db.models import BusinessRequest, Operation

    session = _get_session(request)
    try:
        cutoff = _period_cutoff(period, range)
        rows: list[dict[str, Any]] = []
        actions = sorted(
            action
            for (action,) in (
                session.query(Operation.action)
                .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
                .filter(BusinessRequest.created_at >= cutoff)
                .distinct()
                .all()
            )
            if action is not None
        )
        for action in actions:
            operations = (
                session.query(Operation)
                .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
                .filter(
                    BusinessRequest.created_at >= cutoff,
                    Operation.action == action,
                )
                .all()
            )
            total = len(operations)
            missing_pdv = sum(not operation.pdv_code for operation in operations)
            phone_applicable = action == "otp_number_change"
            missing_phone = (
                sum(not operation.phone for operation in operations) if phone_applicable else 0
            )
            rows.append(
                {
                    "action": action,
                    "total_requests": total,
                    "missing_pdv": missing_pdv,
                    "missing_phone": missing_phone,
                    "missing_pdv_percent": missing_pdv / total * 100 if total else 0,
                    "missing_phone_percent": (
                        missing_phone / total * 100 if total and phone_applicable else None
                    ),
                    "phone_applicable": phone_applicable,
                }
            )
        return {"rows": rows}
    finally:
        _close_session(session)


@router.get("/frontend/analytics/executions")
async def execution_analytics(
    request: Request,
    period: DashboardPeriod | None = None,
    range: DashboardPeriod | None = None,
) -> dict[str, Any]:
    """Return execution-level outcomes grouped by business action."""

    from snoc_agent.db.models import BusinessRequest, Execution, Operation
    from snoc_agent.domain.enums import ExecutionStatus

    session = _get_session(request)
    try:
        cutoff = _period_cutoff(period, range)
        rows: list[dict[str, Any]] = []
        actions = sorted(
            action
            for (action,) in (
                session.query(Operation.action)
                .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
                .filter(BusinessRequest.created_at >= cutoff)
                .distinct()
                .all()
            )
            if action is not None
        )
        for action in actions:
            executions = (
                session.query(Execution)
                .join(Operation, Execution.operation_id == Operation.id)
                .join(BusinessRequest, Operation.request_id == BusinessRequest.id)
                .filter(
                    BusinessRequest.created_at >= cutoff,
                    Operation.action == action,
                )
                .all()
            )
            attempts = len(executions)
            succeeded = sum(
                execution.status == ExecutionStatus.SUCCEEDED.value for execution in executions
            )
            failed = sum(
                execution.status == ExecutionStatus.FAILED.value for execution in executions
            )
            unknown = sum(
                execution.status
                not in {ExecutionStatus.SUCCEEDED.value, ExecutionStatus.FAILED.value}
                for execution in executions
            )
            latency_values = [
                duration * 1000
                for execution in executions
                if (duration := elapsed_seconds(execution.created_at, execution.updated_at))
                is not None
            ]
            rows.append(
                {
                    "action": action,
                    "attempts": attempts,
                    "succeeded": succeeded,
                    "failed": failed,
                    "unknown": unknown,
                    "success_rate": succeeded / attempts if attempts else None,
                    "average_latency_ms": (
                        sum(latency_values) / len(latency_values) if latency_values else None
                    ),
                }
            )
        return {"rows": rows}
    finally:
        _close_session(session)


@router.get("/frontend/requests/{public_reference}/trace")
async def request_trace(public_reference: str, request: Request) -> dict[str, Any]:
    """Return a bounded, secret-free audit trace for one public request reference."""

    from snoc_agent.db.models import (
        BusinessRequest,
        Clarification,
        EmailMessage,
        Escalation,
        Execution,
        ModelRun,
        Operation,
        OutboxMessage,
        ValidationDecision,
        WorkflowEvent,
        WorkflowRun,
    )

    session = _get_session(request)
    try:
        business_request = (
            session.query(BusinessRequest)
            .filter(BusinessRequest.public_reference == public_reference)
            .first()
        )
        if business_request is None:
            raise HTTPException(status_code=404, detail="request not found")
        email = session.get(EmailMessage, business_request.initiating_email_id)
        operations = (
            session.query(Operation)
            .filter(Operation.request_id == business_request.id)
            .order_by(Operation.sequence_number)
            .all()
        )
        operation_ids = [operation.id for operation in operations]
        executions = (
            session.query(Execution).filter(Execution.operation_id.in_(operation_ids)).all()
            if operation_ids
            else []
        )
        decisions = (
            session.query(ValidationDecision)
            .filter(ValidationDecision.operation_id.in_(operation_ids))
            .all()
            if operation_ids
            else []
        )
        model_runs = (
            session.query(ModelRun)
            .filter(
                (ModelRun.email_message_id == business_request.initiating_email_id)
                | (ModelRun.operation_id.in_(operation_ids))
            )
            .order_by(ModelRun.created_at)
            .all()
        )
        clarifications = (
            session.query(Clarification)
            .filter(Clarification.request_id == business_request.id)
            .order_by(Clarification.created_at)
            .all()
        )
        escalations = (
            session.query(Escalation)
            .filter(Escalation.request_id == business_request.id)
            .order_by(Escalation.created_at)
            .all()
        )
        outbox = (
            session.query(OutboxMessage)
            .filter(OutboxMessage.related_request_id == business_request.id)
            .order_by(OutboxMessage.created_at)
            .all()
        )
        workflow_run = (
            session.query(WorkflowRun)
            .filter(WorkflowRun.inbound_email_id == business_request.initiating_email_id)
            .order_by(WorkflowRun.started_at.desc())
            .first()
        )
        workflow_events = (
            session.query(WorkflowEvent)
            .filter(WorkflowEvent.workflow_run_id == workflow_run.id)
            .order_by(WorkflowEvent.sequence)
            .all()
            if workflow_run is not None
            else []
        )

        def normalized_stage_status(value: str | None) -> str:
            normalized = str(value or "unknown").lower()
            if normalized in {"success", "succeeded", "terminal"}:
                return "completed"
            return normalized

        return {
            "request": {
                "request_id": str(business_request.id),
                "public_reference": business_request.public_reference,
                "status": business_request.status,
                "kind": business_request.request_kind,
                "created_at": utc_iso(business_request.created_at),
            },
            "email": (
                {
                    "email_message_id": str(email.id),
                    "rfc_message_id": email.rfc_message_id,
                    "sender": email.sender,
                    "recipients": email.recipients_json,
                    "subject": email.subject,
                    "body_text": email.latest_user_message,
                    "processing_status": email.processing_status,
                    "authorization_allowed": email.authorization_allowed,
                    "authorization_reason": email.authorization_reason,
                    "created_at": utc_iso(
                        email.message_date or email.internal_date or email.created_at
                    ),
                    "attachments": email.attachment_metadata,
                    "parsing_warnings": email.parsing_warnings,
                }
                if email is not None
                else {}
            ),
            "operations": [
                {
                    "operation_id": str(operation.id),
                    "action": operation.action,
                    "status": operation.status,
                    "pdv_code": operation.pdv_code,
                    "phone": operation.phone,
                    "missing_fields": operation.missing_fields,
                    "analyzer_confidence": operation.analyzer_confidence,
                    "verifier_confidence": operation.verifier_confidence,
                    "final_decision": operation.final_decision,
                    "created_at": utc_iso(operation.created_at),
                    "updated_at": utc_iso(operation.updated_at),
                }
                for operation in operations
            ],
            "decisions": [
                {
                    "operation_id": str(decision.operation_id),
                    "decision": decision.decision,
                    "reasons": decision.reasons,
                    "policy_version": decision.policy_version,
                    "hard_invariant_results": decision.hard_invariant_results,
                    "created_at": utc_iso(decision.created_at),
                }
                for decision in decisions
            ],
            "executions": [
                {
                    "operation_id": str(execution.operation_id),
                    "status": execution.status,
                    "dry_run": execution.dry_run,
                    "attempt_count": execution.attempt_count,
                    "response_status": execution.response_status,
                    "endpoint": execution.endpoint,
                    "request_payload": execution.request_payload,
                    "response_body": execution.response_body,
                    "created_at": utc_iso(execution.created_at),
                    "updated_at": utc_iso(execution.updated_at),
                    "latency_ms": (
                        round(duration * 1000, 2)
                        if (duration := elapsed_seconds(execution.created_at, execution.updated_at))
                        is not None
                        else None
                    ),
                }
                for execution in executions
            ],
            "clarifications": [
                {
                    "status": clarification.status,
                    "requested_fields": clarification.requested_fields,
                    "round_number": clarification.round_number,
                    "created_at": utc_iso(clarification.created_at),
                }
                for clarification in clarifications
            ],
            "escalations": [
                {
                    "reason_code": escalation.reason_code,
                    "summary": escalation.summary,
                    "status": escalation.status,
                    "created_at": utc_iso(escalation.created_at),
                }
                for escalation in escalations
            ],
            "outbox": [
                {
                    "status": message.status,
                    "recipient": message.recipient,
                    "subject": message.subject,
                    "body": message.body,
                    "retry_count": message.retry_count,
                    "last_error": message.last_error,
                    "created_at": utc_iso(message.created_at),
                    "sent_at": utc_iso(message.sent_at),
                }
                for message in outbox
            ],
            "model_runs": [
                {
                    "stage": run.stage,
                    "provider": run.reported_provider or run.backend,
                    "model": run.resolved_model_id or run.model_name,
                    "prompt_version": run.prompt_version,
                    "structured_output_valid": run.structured_output_valid,
                    "latency_seconds": run.latency_seconds,
                    "error_category": run.error_category,
                    "fallback_reason": run.fallback_reason,
                    "created_at": utc_iso(run.created_at),
                }
                for run in model_runs
            ],
            "pipeline": [
                {
                    "stage": event.agent,
                    "agent": event.agent,
                    "status": normalized_stage_status(event.status),
                    "duration_ms": (
                        round(duration * 1000)
                        if (duration := elapsed_seconds(event.started_at, event.completed_at))
                        is not None
                        else None
                    ),
                    "error_category": event.error_category,
                    "started_at": utc_iso(event.started_at),
                    "completed_at": utc_iso(event.completed_at),
                }
                for event in workflow_events
            ],
        }
    finally:
        _close_session(session)


# Legacy endpoints that the old frontend used
@router.get("/dashboard")
async def legacy_dashboard(request: Request) -> dict[str, Any]:
    """Legacy dashboard endpoint for backward compatibility."""
    settings = request.app.state.settings
    session = _get_session(request)
    try:
        from snoc_agent.api.queries import build_dashboard_payload

        payload = build_dashboard_payload(
            session,
            agent_active=request.app.state.agent_active,
            authorized_senders=settings.authorized_sender_set,
        )
        return payload.model_dump()
    finally:
        _close_session(session)
