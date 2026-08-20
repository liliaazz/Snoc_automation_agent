"""FastAPI application serving the dashboard API and static frontend."""

from __future__ import annotations

import logging
import mimetypes
import os
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy.exc import SQLAlchemyError

from snoc_agent.api.auth import current_principal, require_admin
from snoc_agent.api.auth_router import router as auth_router
from snoc_agent.api.dashboard_router import router as dashboard_router
from snoc_agent.api.queries import build_dashboard_payload
from snoc_agent.api.schemas import (
    AgentToggleResponse,
    DashboardPayload,
    SimulateInboxResponse,
)
from snoc_agent.config import Settings, load_settings
from snoc_agent.datetime_utils import elapsed_seconds, utc_iso
from snoc_agent.db.session import create_engine_and_session
from snoc_agent.metrics import collector as metrics_collector

FRONTEND_DIR = Path(__file__).resolve().parents[3] / "frontend"
FRONTEND_DIST_DIR = Path(os.environ.get("FRONTEND_DIST_DIRECTORY", str(FRONTEND_DIR / "dist")))

logger = logging.getLogger(__name__)


class EscalationResolveRequest(BaseModel):
    decision: str  # "approve" or "reject"
    note: str = ""


class WhitelistEntry(BaseModel):
    email: str
    zone: str = "Unknown"


class AlertDismissResponse(BaseModel):
    dismissed: bool


class EscalationResolveResponse(BaseModel):
    resolved: bool
    request_id: str
    new_status: str


class WhitelistResponse(BaseModel):
    entries: list[dict[str, str]]


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        settings = load_settings()

    if not settings.dry_run:
        auth_values = {
            "DASHBOARD_ADMIN_USERNAME": settings.dashboard_admin_username,
            "DASHBOARD_ADMIN_PASSWORD": settings.dashboard_admin_password.get_secret_value(),
            "AUTH_JWT_SECRET": settings.auth_jwt_secret.get_secret_value(),
        }
        missing_auth = sorted(name for name, value in auth_values.items() if not value.strip())
        if missing_auth:
            raise ValueError("live API authentication is incomplete: " + ", ".join(missing_auth))

    engine, session_factory = create_engine_and_session(settings.database_url)

    limiter = Limiter(key_func=get_remote_address)

    app = FastAPI(
        title="SNOC Integrated Agent API",
        version="1.0.0",
        docs_url="/docs" if settings.dry_run else None,
        redoc_url=None,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.agent_active = True
    app.state.limiter = limiter

    # Include the dashboard router for the React frontend
    app.include_router(auth_router)
    app.include_router(dashboard_router)

    # --- Rate limit error handler -----------------------------------------
    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        metrics_collector.increment_counter(
            "snoc_rate_limit_exceeded", labels={"path": request.url.path}
        )
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limit_exceeded", "detail": str(exc.detail)},
        )

    # --- CORS ---------------------------------------------------------------
    if settings.dry_run:
        cors_origins = ["*"]
    else:
        cors_origins = [
            origin.strip() for origin in settings.cors_allowed_origins.split(",") if origin.strip()
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        allow_credentials=False,
    )

    # --- Request ID + timing middleware --------------------------------------
    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next: Any) -> Any:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        start = time.monotonic()
        try:
            response = await call_next(request)
        except SQLAlchemyError:
            metrics_collector.increment_counter("snoc_http_errors", labels={"code": "503"})
            return JSONResponse(
                status_code=503,
                content={"error": "database_unavailable", "request_id": request_id},
            )
        elapsed_ms = (time.monotonic() - start) * 1000
        metrics_collector.observe_histogram(
            "snoc_http_request_duration_ms",
            elapsed_ms,
            labels={
                "method": request.method,
                "path": request.url.path,
                "status": str(response.status_code),
            },
        )
        metrics_collector.increment_counter(
            "snoc_http_requests_total",
            labels={
                "method": request.method,
                "status": str(response.status_code),
            },
        )
        response.headers["x-request-id"] = request_id
        response.headers["cache-control"] = "no-store"
        response.headers["x-content-type-options"] = "nosniff"
        return response

    # --- Routes -------------------------------------------------------------

    async def require_development_feature() -> None:
        if not settings.dry_run:
            raise HTTPException(
                status_code=501, detail="this development-only control is disabled in production"
            )

    # Dashboard
    @app.get(
        "/api/dashboard", response_model=DashboardPayload, dependencies=[Depends(current_principal)]
    )
    @limiter.limit("30/minute")
    async def get_dashboard(request: Request) -> DashboardPayload:
        session = session_factory()
        try:
            return build_dashboard_payload(
                session,
                agent_active=app.state.agent_active,
                authorized_senders=settings.authorized_sender_set,
            )
        finally:
            session.close()

    # Agent toggle
    @app.post(
        "/api/agent-toggle",
        response_model=AgentToggleResponse,
        dependencies=[Depends(require_admin), Depends(require_development_feature)],
    )
    @limiter.limit("5/minute")
    async def toggle_agent(request: Request) -> AgentToggleResponse:
        app.state.agent_active = not app.state.agent_active
        metrics_collector.set_gauge("snoc_agent_active", 1.0 if app.state.agent_active else 0.0)
        return AgentToggleResponse(agent_active=app.state.agent_active)

    # Simulate inbox (marks pending emails as processed)
    @app.post(
        "/api/simulate-inbox",
        response_model=SimulateInboxResponse,
        dependencies=[Depends(require_admin), Depends(require_development_feature)],
    )
    @limiter.limit("2/minute")
    async def simulate_inbox(request: Request) -> SimulateInboxResponse:
        from snoc_agent.db.models import EmailMessage
        from snoc_agent.domain.enums import ProcessingStatus

        session = session_factory()
        try:
            pending = (
                session.query(EmailMessage)
                .filter(
                    EmailMessage.direction == "inbound",
                    EmailMessage.processing_status.in_(
                        [
                            ProcessingStatus.STORED.value,
                            ProcessingStatus.PROCESSING.value,
                        ]
                    ),
                )
                .all()
            )
            count = len(pending)
            for msg in pending:
                msg.processing_status = ProcessingStatus.PROCESSED.value
            session.commit()
            metrics_collector.increment_counter("snoc_simulate_inbox_count", float(count))
            return SimulateInboxResponse(processed=count)
        except Exception:
            session.rollback()
            return SimulateInboxResponse(processed=0)
        finally:
            session.close()

    # --- Escalation management ----------------------------------------------

    @app.post(
        "/api/escalations/{request_id}/resolve",
        response_model=EscalationResolveResponse,
        dependencies=[Depends(require_admin)],
    )
    async def resolve_escalation(
        request_id: str,
        body: EscalationResolveRequest,
    ) -> EscalationResolveResponse:
        from snoc_agent.db.models import BusinessRequest, Escalation, Operation
        from snoc_agent.domain.enums import OperationStatus, RequestStatus

        if body.decision not in ("approve", "reject"):
            raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")
        if not settings.dry_run and body.decision == "approve":
            raise HTTPException(
                status_code=501,
                detail="production approval execution is not implemented; operation was unchanged",
            )

        session = session_factory()
        try:
            req = (
                session.query(BusinessRequest)
                .filter(BusinessRequest.public_reference == request_id)
                .first()
            )
            if not req:
                raise HTTPException(status_code=404, detail="request not found")

            new_op_status = (
                OperationStatus.COMPLETED.value
                if body.decision == "approve"
                else OperationStatus.CANCELLED.value
            )
            new_req_status = (
                RequestStatus.COMPLETED.value
                if body.decision == "approve"
                else RequestStatus.CANCELLED.value
            )

            ops = session.query(Operation).filter(Operation.request_id == req.id).all()
            for op in ops:
                if op.status == OperationStatus.ESCALATED.value:
                    op.status = new_op_status

            req.status = new_req_status

            esc = (
                session.query(Escalation)
                .filter(
                    Escalation.request_id == req.id,
                    Escalation.status == "open",
                )
                .first()
            )
            if esc:
                esc.status = "resolved"

            session.commit()
            metrics_collector.increment_counter(
                "snoc_escalations_resolved", labels={"decision": body.decision}
            )
            return EscalationResolveResponse(
                resolved=True,
                request_id=request_id,
                new_status=new_req_status,
            )
        except HTTPException:
            raise
        except Exception:
            session.rollback()
            raise HTTPException(status_code=500, detail="failed to resolve escalation") from None
        finally:
            session.close()

    @app.get("/api/escalations", dependencies=[Depends(current_principal)])
    async def list_escalations() -> dict[str, Any]:
        from snoc_agent.api.queries import _extract_confidence, _infer_zone
        from snoc_agent.db.models import BusinessRequest, EmailMessage, Escalation, Operation

        session = session_factory()
        try:
            escalations = (
                session.query(Escalation)
                .filter(Escalation.status == "open")
                .order_by(Escalation.created_at.desc())
                .all()
            )
            rows: list[dict[str, Any]] = []
            for escalation in escalations:
                business_request = (
                    session.get(BusinessRequest, escalation.request_id)
                    if escalation.request_id is not None
                    else None
                )
                email = session.get(EmailMessage, escalation.email_message_id)
                operations = (
                    session.query(Operation)
                    .filter(Operation.request_id == business_request.id)
                    .order_by(Operation.sequence_number)
                    .all()
                    if business_request is not None
                    else []
                )
                operation = next(
                    (item for item in operations if item.status == "ESCALATED"),
                    operations[0] if operations else None,
                )
                rows.append(
                    {
                        "id": str(escalation.id),
                        "request_id": (
                            str(escalation.request_id)
                            if escalation.request_id is not None
                            else None
                        ),
                        "public_reference": (
                            business_request.public_reference
                            if business_request is not None
                            else None
                        ),
                        "email_message_id": str(escalation.email_message_id),
                        "sender": email.sender if email is not None else None,
                        "zone": _infer_zone(email),
                        "request_type": operation.action if operation is not None else None,
                        "confidence": (
                            _extract_confidence(operation) if operation is not None else None
                        ),
                        "pdv_code": operation.pdv_code if operation is not None else None,
                        "phone": operation.phone if operation is not None else None,
                        "decision": (operation.final_decision if operation is not None else None),
                        "reason_code": escalation.reason_code,
                        "summary": escalation.summary,
                        "status": escalation.status,
                        "created_at": utc_iso(escalation.created_at),
                        "resolved_at": utc_iso(escalation.resolved_at),
                    }
                )
            return {"escalations": rows}
        finally:
            session.close()

    # --- Whitelist management -----------------------------------------------

    @app.get(
        "/api/whitelist",
        response_model=WhitelistResponse,
        dependencies=[Depends(current_principal)],
    )
    async def get_whitelist() -> WhitelistResponse:
        return WhitelistResponse(
            entries=[
                {"email": e, "zone": "Unknown"} for e in sorted(settings.authorized_sender_set)
            ]
        )

    @app.post(
        "/api/whitelist",
        dependencies=[Depends(require_admin), Depends(require_development_feature)],
    )
    async def add_to_whitelist(entry: WhitelistEntry) -> dict[str, str]:
        # In production, this would persist to DB
        return {"status": "added", "email": entry.email, "zone": entry.zone}

    @app.delete(
        "/api/whitelist/{email}",
        dependencies=[Depends(require_admin), Depends(require_development_feature)],
    )
    async def remove_from_whitelist(email: str) -> dict[str, str]:
        # In production, this would remove from DB
        return {"status": "removed", "email": email}

    # --- Alert management ---------------------------------------------------

    @app.post(
        "/api/alerts/{alert_id}/dismiss",
        response_model=AlertDismissResponse,
        dependencies=[Depends(require_admin)],
    )
    async def dismiss_alert(alert_id: str) -> AlertDismissResponse:
        from snoc_agent.db.models import Escalation

        session = session_factory()
        try:
            esc = session.query(Escalation).filter(Escalation.id == alert_id).first()
            if not esc:
                return AlertDismissResponse(dismissed=False)
            esc.status = "resolved"
            session.commit()
            metrics_collector.increment_counter("snoc_alerts_dismissed")
            return AlertDismissResponse(dismissed=True)
        except Exception:
            session.rollback()
            return AlertDismissResponse(dismissed=False)
        finally:
            session.close()

    # --- Request pipeline detail --------------------------------------------

    @app.get("/api/requests/{request_id}/pipeline", dependencies=[Depends(current_principal)])
    async def get_request_pipeline(request_id: str) -> dict[str, Any]:
        from snoc_agent.db.models import BusinessRequest, WorkflowEvent, WorkflowRun

        session = session_factory()
        try:
            req = (
                session.query(BusinessRequest)
                .filter(BusinessRequest.public_reference == request_id)
                .first()
            )
            if not req:
                raise HTTPException(status_code=404, detail="request not found")

            run = (
                session.query(WorkflowRun)
                .filter(WorkflowRun.inbound_email_id == req.initiating_email_id)
                .order_by(WorkflowRun.started_at.desc())
                .first()
            )
            if not run:
                return {"stages": [], "pipeline": []}

            events = (
                session.query(WorkflowEvent)
                .filter(WorkflowEvent.workflow_run_id == run.id)
                .order_by(WorkflowEvent.sequence)
                .all()
            )
            return {
                "stages": [
                    {
                        "agent": e.agent,
                        "status": e.status,
                        "started_at": utc_iso(e.started_at),
                        "completed_at": utc_iso(e.completed_at),
                        "error_category": e.error_category,
                    }
                    for e in events
                ],
                "pipeline": [
                    {
                        "node": e.agent,
                        "active": e.status == "completed",
                        "duration_ms": (
                            int(duration * 1000)
                            if (duration := elapsed_seconds(e.started_at, e.completed_at))
                            is not None
                            else None
                        ),
                    }
                    for e in events
                ],
            }
        finally:
            session.close()

    # --- Health + Metrics ---------------------------------------------------

    @app.get("/health/live")
    async def health_live() -> dict[str, str]:
        return {"status": "ok", "mode": "demo" if settings.dry_run else "live"}

    @app.get("/health/ready", response_model=None)
    async def health_ready() -> dict[str, str] | JSONResponse:
        session = session_factory()
        try:
            from sqlalchemy import text

            session.execute(text("SELECT 1"))
            return {"status": "ok", "mode": "demo" if settings.dry_run else "live"}
        except Exception:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "detail": "database readiness check failed"},
            )
        finally:
            session.close()

    @app.get("/metrics")
    async def metrics_endpoint() -> PlainTextResponse:
        """Prometheus-compatible metrics endpoint."""
        return PlainTextResponse(
            content=metrics_collector.format_prometheus(),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    @app.get("/metrics/summary")
    async def metrics_summary() -> dict[str, Any]:
        """JSON summary of all collected metrics."""
        return metrics_collector.get_summary()

    # --- Static frontend files ----------------------------------------------
    if FRONTEND_DIST_DIR.exists():

        def frontend_file(path: Path) -> Response:
            resolved = path.resolve()
            root = FRONTEND_DIST_DIR.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise HTTPException(status_code=404, detail="frontend asset not found")
            media_type = mimetypes.guess_type(resolved.name)[0] or "application/octet-stream"
            return Response(resolved.read_bytes(), media_type=media_type)

        @app.get("/", include_in_schema=False)
        async def serve_index() -> Response:
            return frontend_file(FRONTEND_DIST_DIR / "index.html")

        @app.get("/assets/{asset_path:path}", include_in_schema=False)
        async def serve_asset(asset_path: str) -> Response:
            return frontend_file(FRONTEND_DIST_DIR / "assets" / asset_path)

        @app.get("/{full_path:path}", include_in_schema=False)
        async def serve_spa(full_path: str) -> Response:
            file_path = FRONTEND_DIST_DIR / full_path
            if file_path.is_file():
                return frontend_file(file_path)
            return frontend_file(FRONTEND_DIST_DIR / "index.html")

    return app
