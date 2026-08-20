from __future__ import annotations

import json
from pathlib import Path

import pytest

from snoc_agent.config import LLMProvider, Settings

MATRIX_PATH = Path(__file__).parents[1] / "fixtures" / "emails" / "hardening_matrix.json"


def test_mandatory_hardening_matrix_has_exactly_50_traceable_cases() -> None:
    cases = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))

    assert len(cases) == 50
    assert [case["id"] for case in cases] == list(range(1, 51))
    assert len({case["name"] for case in cases}) == 50
    assert all(case["layer"] and case["expected"] for case in cases)


def test_matrix_contains_all_safety_critical_categories() -> None:
    cases = json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
    layers = {case["layer"] for case in cases}

    assert {
        "adapter",
        "correlation",
        "e2e",
        "execution",
        "limits",
        "outbox",
        "performance",
        "policy",
        "repository",
        "safety",
        "security",
    } <= layers


def _production_settings(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "app_environment": "production",
        "dry_run": False,
        "database_url": "postgresql+psycopg://snoc:secret@postgres/snoc",
        "imap_host": "imap.corp.internal",
        "imap_username": "agent@corp.test",
        "imap_password": "secret",
        "imap_search_criterion": "UNSEEN",
        "smtp_host": "smtp.corp.internal",
        "smtp_username": "agent@corp.test",
        "smtp_password": "secret",
        "authorized_senders": "operator@corp.test",
        "business_api_base_url": "https://business.corp.internal",
        "business_api_token": "secret",
        "llm_provider": LLMProvider.VLLM,
        "vllm_api_key": "secret",
        "smtp_from_address": "agent@corp.test",
        "escalation_recipient": "support@corp.test",
        "vllm_qwen_base_url": "https://qwen.corp.internal/v1",
        "vllm_gemma_base_url": "https://gemma.corp.internal/v1",
        "vllm_qwen3_30b_base_url": "https://qwen3.corp.internal/v1",
        "dashboard_admin_username": "snoc-admin",
        "dashboard_admin_password": "long-unit-test-password",
        "auth_jwt_secret": "unit-test-dashboard-signing-secret-at-least-32-chars",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"dry_run": True}, "DRY_RUN must be false"),
        ({"dry_run_send_emails": True}, "DRY_RUN_SEND_EMAILS"),
        ({"run_vllm_live_tests": True}, "live-test flags"),
        ({"database_url": "sqlite:///snoc.db"}, "PostgreSQL is required"),
        ({"imap_search_criterion": 'HEADER X-SNOC-Test-Run "acceptance"'}, "test-scoped"),
        ({"imap_search_criterion": "ALL"}, "must be bounded"),
        ({"vllm_api_key": "replace_me"}, "placeholder values"),
    ],
)
def test_production_configuration_rejects_test_and_unsafe_modes(
    override: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        Settings(**_production_settings(**override))


def test_production_configuration_accepts_bounded_live_mode() -> None:
    settings = Settings(**_production_settings())

    assert settings.app_environment == "production"
    assert settings.dry_run is False
    assert settings.imap_search_criterion == "UNSEEN"
