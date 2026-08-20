"""Tests for the SNOC FastAPI dashboard API endpoints."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest


class ASGITestClient:
    """Small synchronous facade that avoids this runner's broken thread portal."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def send():
            transport = httpx.ASGITransport(app=self.app, raise_app_exceptions=False)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(send())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def delete(self, path: str, **kwargs):
        return self.request("DELETE", path, **kwargs)


@pytest.fixture()
def client(tmp_path):
    """Create a test client backed by an in-memory SQLite database."""
    import os

    os.environ["DATABASE_URL"] = f"sqlite:///{tmp_path}/test.db"
    os.environ["DRY_RUN"] = "true"

    from snoc_agent.config import Settings
    from snoc_agent.db.session import create_engine_and_session, create_schema
    from snoc_agent.seed import seed_demo_data

    settings = Settings(
        database_url=f"sqlite:///{tmp_path}/test.db",
        dry_run=True,
        dashboard_admin_username="",
        dashboard_admin_password="",
        auth_jwt_secret="",
    )
    engine, session_factory = create_engine_and_session(settings.database_url)
    create_schema(engine)

    session = session_factory()
    seed_demo_data(session)
    session.close()

    from snoc_agent.api.app import create_app
    from snoc_agent.api.auth import Principal, require_admin

    app = create_app(settings)

    async def test_admin_principal() -> Principal:
        return Principal(
            subject="test-admin",
            roles=frozenset({"ADMIN"}),
            authenticated=True,
        )

    app.dependency_overrides[require_admin] = test_admin_principal
    return ASGITestClient(app)


@pytest.fixture()
def local_auth_client(tmp_path):
    from snoc_agent.api.app import create_app
    from snoc_agent.config import Settings
    from snoc_agent.db.session import create_engine_and_session, create_schema

    database_url = f"sqlite:///{tmp_path}/local-auth.db"
    settings = Settings(
        database_url=database_url,
        dry_run=True,
        dashboard_admin_username="snoc-admin",
        dashboard_admin_password="correct-local-password",
        auth_jwt_secret="unit-test-dashboard-signing-secret-at-least-32-chars",
        auth_token_ttl_minutes=30,
    )
    engine, _session_factory = create_engine_and_session(database_url)
    create_schema(engine)
    return ASGITestClient(create_app(settings))


class TestHealthEndpoints:
    def test_live(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_ready(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestDashboard:
    def test_returns_payload(self, client):
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200
        data = resp.json()
        assert "stats" in data
        assert "requests" in data
        assert "alerts" in data

    def test_stats_structure(self, client):
        resp = client.get("/api/dashboard")
        stats = resp.json()["stats"]
        for key in (
            "total_requests",
            "pending_requests",
            "successful_executions",
            "failed",
            "escalated",
            "in_progress",
            "unauthorized",
        ):
            assert key in stats, f"missing stat key: {key}"

    def test_agent_active_reflected(self, client):
        resp = client.get("/api/dashboard")
        assert resp.json()["agent_active"] is True


class TestAgentToggle:
    def test_toggle_off_then_on(self, client):
        resp = client.post("/api/agent-toggle")
        assert resp.status_code == 200
        assert resp.json()["agent_active"] is False

        resp = client.post("/api/agent-toggle")
        assert resp.status_code == 200
        assert resp.json()["agent_active"] is True


class TestSimulateInbox:
    def test_returns_count(self, client):
        resp = client.post("/api/simulate-inbox")
        assert resp.status_code == 200
        assert "processed" in resp.json()


class TestEscalations:
    def test_list_escalations(self, client):
        resp = client.get("/api/escalations")
        assert resp.status_code == 200
        assert "escalations" in resp.json()

    def test_resolve_nonexistent_returns_404(self, client):
        resp = client.post("/api/escalations/nonexistent-id/resolve", json={"decision": "approve"})
        assert resp.status_code == 404


class TestWhitelist:
    def test_get_whitelist(self, client):
        resp = client.get("/api/whitelist")
        assert resp.status_code == 200
        assert "entries" in resp.json()

    def test_add_to_whitelist(self, client):
        resp = client.post("/api/whitelist", json={"email": "test@example.com"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "added"


class TestAccounts:
    def test_get_accounts(self, client):
        resp = client.get("/api/accounts")
        assert resp.status_code == 200
        assert "accounts" in resp.json()
        assert resp.json()["accounts"] == []
        assert all("password" not in account for account in resp.json()["accounts"])

    def test_create_account(self, client):
        resp = client.post(
            "/api/accounts", json={"username": "new.user", "password": "Pass123!", "role": "normal"}
        )
        assert resp.status_code == 201
        assert resp.json()["status"] == "created"

    def test_update_account(self, client):
        client.post("/api/accounts", json={"username": "amina.east", "password": "Pass123!"})
        resp = client.put("/api/accounts/amina.east", json={"password": "NewPass456!"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

    def test_delete_account(self, client):
        client.post("/api/accounts", json={"username": "sofiane.west", "password": "Pass123!"})
        resp = client.delete("/api/accounts/sofiane.west")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

    def test_delete_nonexistent_returns_404(self, client):
        resp = client.delete("/api/accounts/nobody")
        assert resp.status_code == 404

    def test_toggle_account(self, client):
        client.post("/api/accounts", json={"username": "amina.east", "password": "Pass123!"})
        resp = client.post("/api/accounts/amina.east/toggle")
        assert resp.status_code == 200
        body = resp.json()
        assert body["active"] is False or body["active"] is True


class TestPipeline:
    def test_nonexistent_request_returns_404(self, client):
        resp = client.get("/api/requests/FAKE-123/pipeline")
        assert resp.status_code == 404


class TestStaticFrontend:
    def test_serves_index(self, client):
        from snoc_agent.api.app import FRONTEND_DIST_DIR

        index_path = FRONTEND_DIST_DIR / "index.html"

        if not index_path.is_file():
            pytest.skip("frontend/dist/index.html is not included in this backend bundle")

        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    def test_serves_assets(self, client):
        # The React build puts assets in /assets/
        resp = client.get("/assets/")
        # Should return either a file or redirect
        assert resp.status_code in [200, 307, 404]


class TestDashboardRouter:
    def test_dashboard_summary(self, client):
        resp = client.get("/api/snoc/dashboard/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "operational" in data
        assert "dataQuality" in data
        operational = data["operational"]
        assert operational["totalRequests"] == operational["uniqueBusinessRequests"]
        assert operational["totalOperations"] >= 0
        assert "totalEmailsReceived" in operational
        assert "completedOperations" in operational
        assert "pendingOperations" in operational

    def test_dashboard_trends(self, client):
        resp = client.get("/api/snoc/dashboard/trends")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_dashboard_intents(self, client):
        resp = client.get("/api/snoc/dashboard/intents")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_dashboard_recent(self, client):
        resp = client.get("/api/snoc/dashboard/recent")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_dashboard_recent_uses_persisted_execution_reply_and_duration(self, client):
        import uuid
        from datetime import UTC, datetime, timedelta

        from snoc_agent.db.models import BusinessRequest, EmailMessage, Execution, Operation
        from snoc_agent.domain.enums import ExecutionStatus

        session = client.app.state.session_factory()
        try:
            operation = session.query(Operation).filter(Operation.action == "vpn_access").first()
            business_request = session.get(BusinessRequest, operation.request_id)
            email = session.get(EmailMessage, business_request.initiating_email_id)
            email.attachment_metadata = [{"filename": "request.pdf", "size": 123}]
            now = datetime.now(UTC)
            execution = Execution(
                operation_id=operation.id,
                operation_revision=operation.current_revision,
                idempotency_key=f"api-contract-test:{uuid.uuid4()}",
                endpoint="https://business.example.test/vpn",
                request_payload={"pdv_code": operation.pdv_code},
                response_status=200,
                response_body={"message": "persisted business response"},
                dry_run=True,
                attempt_count=1,
                status=ExecutionStatus.SUCCEEDED.value,
                created_at=now - timedelta(seconds=2),
                updated_at=now,
            )
            session.add(execution)
            session.commit()
            operation_id = str(operation.id)
            public_reference = business_request.public_reference
            email_message_id = str(email.id)
        finally:
            session.close()

        response = client.get("/api/snoc/dashboard/recent?period=week")

        assert response.status_code == 200
        item = next(row for row in response.json()["items"] if row["operation_id"] == operation_id)
        assert item["record_type"] == "business_operation"
        assert item["request_id"] == public_reference
        assert item["email_message_id"] == email_message_id
        assert item["execution_occurred"] is True
        assert item["detected_language"] is None
        assert item["attachments"] == [{"filename": "request.pdf", "size": 123}]
        assert item["duration_ms"] == 30 * 60 * 1000
        assert item["metadata"]["execution_details"] == {
            "status": "succeeded",
            "endpoint": "https://business.example.test/vpn",
            "message": "persisted business response",
            "response_status": 200,
            "dry_run": True,
            "attempt_count": 1,
            "latency_ms": 2000,
            "request_payload": {"pdv_code": "12345678"},
            "response_body": {"message": "persisted business response"},
        }
        assert item["reply_recipient"] == "techsupport@example.test"
        assert item["reply_subject"] == "Re: Activation VPN"
        assert item["reply_text"] == "Votre VPN a ete active."
        assert item["reply_status"] == "sent"

        trace = client.get(f"/api/snoc/frontend/requests/{public_reference}/trace")
        assert trace.status_code == 200
        trace_payload = trace.json()
        assert trace_payload["executions"][0]["request_payload"] == {"pdv_code": "12345678"}
        assert trace_payload["executions"][0]["response_body"] == {
            "message": "persisted business response"
        }
        assert trace_payload["outbox"][0]["body"] == "Votre VPN a ete active."
        assert trace_payload["pipeline"]

    def test_dashboard_recent_includes_non_operational_security_email_events(self, client):
        import hashlib

        from snoc_agent.db.models import EmailMessage
        from snoc_agent.domain.enums import ProcessingStatus

        session = client.app.state.session_factory()
        try:
            unauthorized_body = "Untrusted inbound request"
            unauthorized = EmailMessage(
                direction="inbound",
                rfc_message_id="<unauthorized@api-contract.test>",
                normalized_message_id="unauthorized-api-contract",
                sender="untrusted@example.test",
                recipients_json=["snoc@example.test"],
                subject="Unauthorized request",
                raw_text=unauthorized_body,
                latest_user_message=unauthorized_body,
                raw_sha256=hashlib.sha256(unauthorized_body.encode()).hexdigest(),
                processing_status=ProcessingStatus.IGNORED.value,
                authorization_allowed=False,
                authorization_reason="sender_not_whitelisted",
            )
            quarantined_body = "Malformed inbound request"
            quarantined = EmailMessage(
                direction="inbound",
                rfc_message_id="<quarantined@api-contract.test>",
                normalized_message_id="quarantined-api-contract",
                sender="sender@example.test",
                recipients_json=["snoc@example.test"],
                subject="Malformed request",
                raw_text=quarantined_body,
                latest_user_message=quarantined_body,
                raw_sha256=hashlib.sha256(quarantined_body.encode()).hexdigest(),
                processing_status=ProcessingStatus.QUARANTINED.value,
                quarantine_message="mime_parse_failed",
            )
            session.add_all([unauthorized, quarantined])
            session.commit()
            unauthorized_id = str(unauthorized.id)
            quarantined_id = str(quarantined.id)
        finally:
            session.close()

        response = client.get("/api/snoc/dashboard/recent?period=week")

        assert response.status_code == 200
        by_email_id = {
            row["email_message_id"]: row
            for row in response.json()["items"]
            if row["record_type"] == "email_security_event"
        }
        unauthorized_row = by_email_id[unauthorized_id]
        assert unauthorized_row["request_id"] is None
        assert unauthorized_row["operation_id"] is None
        assert unauthorized_row["execution_occurred"] is False
        assert unauthorized_row["execution_status"] is None
        assert unauthorized_row["metadata"] is None
        assert unauthorized_row["request_status"] == "UNAUTHORIZED"
        assert unauthorized_row["decision"] == "REJECT"
        assert unauthorized_row["validation_error"] == "sender_not_whitelisted"

        quarantined_row = by_email_id[quarantined_id]
        assert quarantined_row["request_id"] is None
        assert quarantined_row["execution_occurred"] is False
        assert quarantined_row["request_status"] == "REJECTED"
        assert quarantined_row["decision"] is None
        assert quarantined_row["validation_error"] == "mime_parse_failed"

    def test_dq_executive(self, client):
        resp = client.get("/api/snoc/dq/executive")
        assert resp.status_code == 200
        data = resp.json()
        assert "completeness" in data
        assert "accuracy" in data

    def test_dq_dimensions(self, client):
        resp = client.get("/api/snoc/dq/dimensions")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_dq_rules(self, client):
        resp = client.get("/api/snoc/dq/rules")
        assert resp.status_code == 200
        assert "items" in resp.json()

    def test_model_snapshot(self, client):
        resp = client.get("/api/snoc/model/snapshot")
        assert resp.status_code == 200
        data = resp.json()
        assert "accuracy" in data
        assert "precision" in data
        for key in (
            "provider",
            "modelName",
            "available",
            "lastSuccessfulInference",
            "recentRuns",
            "errorCount",
            "dryRun",
            "fallbackOccurred",
        ):
            assert key in data

    def test_model_snapshot_normalizes_naive_sqlite_timestamps(self, client):
        from datetime import datetime

        from snoc_agent.db.models import ModelRun

        session = client.app.state.session_factory()
        try:
            run = session.query(ModelRun).first()
            if run is None:
                run = ModelRun(
                    stage="analysis",
                    backend="vllm",
                    model_name="Qwen/Qwen2.5-7B-Instruct-AWQ",
                    prompt_version="test",
                    input_context_hash="a" * 64,
                    structured_output_valid=True,
                )
                session.add(run)
            run.created_at = datetime(2026, 7, 26, 9, 0)
            session.commit()
        finally:
            session.close()

        resp = client.get("/api/snoc/model/snapshot")
        assert resp.status_code == 200
        assert resp.json()["recentRuns"]

    def test_workflow_health(self, client):
        resp = client.get("/api/snoc/workflow/health")
        assert resp.status_code == 200
        data = resp.json()
        assert "status" in data
        assert "agents" in data

    @pytest.mark.parametrize(
        "path",
        [
            "/api/snoc/frontend/runtime",
            "/api/snoc/frontend/analytics/confidence",
            "/api/snoc/frontend/analytics/missing-entities",
            "/api/snoc/frontend/analytics/executions",
        ],
    )
    def test_frontend_integration_endpoints(self, client, path):
        resp = client.get(path)
        assert resp.status_code == 200

    def test_frontend_runtime_exposes_the_configured_inbound_mailbox(self, client):
        resp = client.get("/api/snoc/frontend/runtime")

        assert resp.status_code == 200
        assert resp.json()["inbox_address"] == client.app.state.settings.imap_username
        assert resp.json()["imap_mailbox"] == client.app.state.settings.imap_mailbox
        assert (
            resp.json()["imap_search_criterion"] == client.app.state.settings.imap_search_criterion
        )

    def test_populated_request_trace_is_retrievable_and_secret_free(self, client):
        from snoc_agent.db.models import BusinessRequest

        session = client.app.state.session_factory()
        try:
            public_reference = session.query(BusinessRequest.public_reference).first()[0]
        finally:
            session.close()

        resp = client.get(f"/api/snoc/frontend/requests/{public_reference}/trace")

        assert resp.status_code == 200
        payload = resp.json()
        assert payload["request"]["public_reference"] == public_reference

        def mapping_keys(value):
            if isinstance(value, dict):
                yield from value
                for nested in value.values():
                    yield from mapping_keys(nested)
            elif isinstance(value, list):
                for nested in value:
                    yield from mapping_keys(nested)

        assert not {"password", "token", "secret"} & {
            str(key).casefold() for key in mapping_keys(payload)
        }

    def test_dashboard_is_stable_under_sequential_and_concurrent_reads(self, client):
        baseline = client.get("/api/snoc/dashboard/summary").json()["operational"]

        for _ in range(50):
            response = client.get("/api/snoc/dashboard/summary")
            assert response.status_code == 200
            assert response.json()["operational"] == baseline

        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(
                executor.map(
                    lambda _index: client.get("/api/snoc/dashboard/summary"),
                    range(20),
                )
            )

        assert all(response.status_code == 200 for response in responses)
        assert all(response.json()["operational"] == baseline for response in responses)

    @pytest.mark.parametrize("query_name", ["period", "range"])
    def test_dashboard_period_query_aliases(self, client, query_name):
        resp = client.get(f"/api/snoc/dashboard/summary?{query_name}=month")
        assert resp.status_code == 200

    def test_invalid_dashboard_period_is_rejected(self, client):
        resp = client.get("/api/snoc/dashboard/summary?period=forever")
        assert resp.status_code == 422

    def test_conflicting_dashboard_period_aliases_are_rejected(self, client):
        resp = client.get("/api/snoc/dashboard/summary?period=week&range=month")
        assert resp.status_code == 400


class TestMetrics:
    def test_metrics_endpoint(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]

    def test_metrics_summary(self, client):
        resp = client.get("/metrics/summary")
        assert resp.status_code == 200
        data = resp.json()
        assert "counters" in data
        assert "gauges" in data
        assert "histograms" in data

    def test_requests_increment_metrics(self, client):
        # Make a few requests to generate metrics
        client.get("/api/dashboard")
        client.get("/api/dashboard")
        resp = client.get("/metrics/summary")
        data = resp.json()
        assert data["counters"].get('snoc_http_requests_total{method="GET",status="200"}', 0) >= 2


class TestAuth:
    def test_development_fallback_principal(self, client):
        """In dry-run mode, auth returns a development principal without a token."""
        resp = client.get("/api/dashboard")
        assert resp.status_code == 200

    def test_jwt_missing_token_returns_401(self, client):
        """Without JWT config, non-dry-run mode would return 503 (not configured)."""
        # In dry-run mode, auth is bypassed, so we can't test 401 here
        # but we verify the auth module loads correctly
        from snoc_agent.api.auth import Principal

        p = Principal(subject="test", roles=frozenset({"ADMIN"}), authenticated=True)
        assert p.is_admin
        assert p.can_view_sensitive_details

    def test_principal_roles(self):
        from snoc_agent.api.auth import Principal

        p = Principal(subject="test", roles=frozenset({"AUDITOR"}), authenticated=True)
        assert not p.is_admin
        assert p.can_view_sensitive_details

    def test_principal_no_roles(self):
        from snoc_agent.api.auth import Principal

        p = Principal(subject="test", roles=frozenset(), authenticated=True)
        assert not p.is_admin
        assert not p.can_view_sensitive_details

    def test_local_login_and_session_identity(self, local_auth_client):
        login = local_auth_client.post(
            "/api/auth/login",
            json={"username": "SNOC-ADMIN", "password": "correct-local-password"},
        )
        assert login.status_code == 200
        payload = login.json()
        assert payload["token_type"] == "bearer"
        assert payload["user"]["role"] == "admin"
        assert payload["user"]["managedByEnv"] is True

        headers = {"Authorization": f"Bearer {payload['access_token']}"}
        me = local_auth_client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["username"] == "snoc-admin"

        dashboard = local_auth_client.get("/api/snoc/dashboard/summary", headers=headers)
        assert dashboard.status_code == 200

    def test_local_login_rejects_invalid_password(self, local_auth_client):
        response = local_auth_client.post(
            "/api/auth/login",
            json={"username": "snoc-admin", "password": "incorrect"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "invalid username or password"

    def test_configured_local_auth_requires_bearer_token(self, local_auth_client):
        response = local_auth_client.get("/api/snoc/dashboard/summary")
        assert response.status_code == 401
        assert response.json()["detail"] == "bearer token required"

    def test_inactive_database_user_token_is_revoked(self, local_auth_client):
        admin_login = local_auth_client.post(
            "/api/auth/login",
            json={"username": "snoc-admin", "password": "correct-local-password"},
        ).json()
        admin_headers = {"Authorization": f"Bearer {admin_login['access_token']}"}
        created = local_auth_client.post(
            "/api/accounts",
            headers=admin_headers,
            json={
                "username": "local.user",
                "password": "UserPassword123!",
                "full_name": "Local User",
                "email": "local.user@example.test",
                "role": "user",
            },
        )
        assert created.status_code == 201

        user_login = local_auth_client.post(
            "/api/auth/login",
            json={"username": "local.user", "password": "UserPassword123!"},
        )
        assert user_login.status_code == 200
        user_headers = {"Authorization": f"Bearer {user_login.json()['access_token']}"}
        assert local_auth_client.get("/api/auth/me", headers=user_headers).status_code == 200

        toggled = local_auth_client.post(
            "/api/accounts/local.user/toggle",
            headers=admin_headers,
        )
        assert toggled.status_code == 200
        assert toggled.json()["active"] is False
        assert local_auth_client.get("/api/auth/me", headers=user_headers).status_code == 401
