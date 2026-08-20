from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def force_offline_provider_for_normal_tests(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> None:
    """A developer's .env must never turn the deterministic suite into paid inference."""

    if request.node.get_closest_marker("vllm_live") or request.node.get_closest_marker(
        "local_model"
    ):
        return
    monkeypatch.setenv("APP_ENVIRONMENT", "development")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("DASHBOARD_AUTH_REQUIRED", "false")
    monkeypatch.setenv("LLM_PROVIDER", "demo")
    monkeypatch.setenv("DRY_RUN_SEND_EMAILS", "false")
