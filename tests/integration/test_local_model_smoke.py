"""Opt-in smoke test for configured OpenAI-compatible Qwen endpoints."""

from __future__ import annotations

import os

import pytest

from snoc_agent.ai.schemas import EmailAnalysis, FieldEvidence, ProposedOperation
from snoc_agent.cli.runtime import build_model_services
from snoc_agent.config import Settings

pytestmark = pytest.mark.local_model


def test_configured_qwen_analyzer_and_verifier_return_strict_schemas() -> None:
    base_url = os.getenv("LLM_BASE_URL")
    if not base_url:
        pytest.skip("set LLM_BASE_URL to run the opt-in local-model smoke test")

    settings = Settings(
        dry_run=True,
        llm_base_url=base_url,
        analyzer_model=os.getenv("ANALYZER_MODEL", "Qwen2.5-7B-Instruct"),
        verifier_model=os.getenv("VERIFIER_MODEL", "Qwen3-8B"),
    )
    backend, analyzer, verifier = build_model_services(settings)
    try:
        analysis_result = analyzer.analyze(
            {
                "mode": "new_request",
                "subject": "Déblocage PDV",
                "latest_user_message": "Merci de débloquer le PDV 12345678.",
                "text_since_last_closed_request": "Merci de débloquer le PDV 12345678.",
                "numeric_candidates": [
                    {
                        "value": "12345678",
                        "raw_value": "12345678",
                        "kind_hint": "pdv_or_unknown",
                        "section": "latest_user_message",
                        "start": 26,
                        "end": 34,
                        "context": "débloquer le PDV 12345678",
                    }
                ],
                "closed_history_summary": None,
            }
        )
        assert isinstance(analysis_result.parsed, EmailAnalysis)

        proposal = ProposedOperation(
            local_operation_id="OP-01",
            action="account_unblock",
            pdv_code="12345678",
            phone=None,
            evidence=[
                FieldEvidence(
                    field_name="pdv_code",
                    value="12345678",
                    source="latest_user_message",
                    evidence_text="PDV 12345678",
                    support="supported",
                )
            ],
        )
        verification_result = verifier.verify(
            context_mode="new_request",
            latest_user_message="Merci de débloquer le PDV 12345678.",
            stored_operation_state={},
            proposed_operation=proposal,
            candidate_evidence=proposal.model_dump(mode="json")["evidence"],
            correlation_strength="new",
        )
        assert verification_result.parsed.action_supported in {"yes", "no", "unclear"}
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()
