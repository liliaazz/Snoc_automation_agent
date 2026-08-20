from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import BaseModel, ConfigDict

from snoc_agent.ai.backend import ChatMessage
from snoc_agent.ai.vllm_deployments import VLLMModelCatalog
from snoc_agent.cli.runtime import build_model_services
from snoc_agent.config import Settings

LIVE_SETTINGS = Settings()

pytestmark = [
    pytest.mark.vllm_live,
    pytest.mark.skipif(
        not LIVE_SETTINGS.effective_vllm_api_key or not LIVE_SETTINGS.run_vllm_live_tests,
        reason="set VLLM_API_KEY and RUN_VLLM_LIVE_TESTS=true for tiny live vLLM probes",
    ),
]


class LiveProbe(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool


def test_vllm_exact_models_and_structured_outputs_are_compatible() -> None:
    catalog = VLLMModelCatalog(
        deployments=LIVE_SETTINGS.vllm_deployments,
        api_key=LIVE_SETTINGS.effective_vllm_api_key,
        timeout_seconds=LIVE_SETTINGS.vllm_request_timeout_seconds,
    )
    analyzer_backend = verifier_backend = None
    try:
        rows = catalog.check_exact_models()
        assert len(rows) == 2
        analyzer_backend, verifier_backend, analyzer, verifier = build_model_services(
            LIVE_SETTINGS,
            analyzer_model="qwen",
            verifier_model="gemma",
        )
        messages = [
            ChatMessage(
                role="system",
                content="Return only the requested JSON object, without explanations.",
            ),
            ChatMessage(role="user", content='Return {"ok": true}.'),
        ]
        for backend, config in (
            (analyzer_backend, analyzer.config),
            (verifier_backend, verifier.config),
        ):
            result = backend.generate_structured(
                messages=messages,
                response_model=LiveProbe,
                config=replace(config, max_output_tokens=128, max_retries=0),
            )
            assert result.parsed == LiveProbe(ok=True)
            assert result.structured_output_mode == "json_schema"
            assert result.cost_basis == "unknown"
            assert result.prompt_tokens is None or result.prompt_tokens >= 0
            assert result.completion_tokens is None or result.completion_tokens >= 0
    finally:
        if analyzer_backend is not None:
            analyzer_backend.close()
        if verifier_backend is not None:
            verifier_backend.close()
        catalog.close()
