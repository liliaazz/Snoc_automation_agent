from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel, ConfigDict

from snoc_agent.ai.backend import ChatMessage, GenerationConfig
from snoc_agent.ai.errors import InferenceError, InferenceErrorCategory
from snoc_agent.ai.openai_compatible_backend import OpenAICompatibleBackend
from snoc_agent.ai.provider import LLMProvider, VLLMDeploymentName
from snoc_agent.ai.vllm_deployments import VLLMDeployment, VLLMModelCatalog
from snoc_agent.cli.runtime import build_model_services
from snoc_agent.config import Settings


class _ProbeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    ok: bool


def _generation_config(**updates: object) -> GenerationConfig:
    values: dict[str, object] = {
        "model": "test-model",
        "max_output_tokens": 4096,
        "max_retries": 0,
        "use_json_schema": True,
    }
    values.update(updates)
    return GenerationConfig(**values)  # type: ignore[arg-type]


def _generate(
    transport: httpx.MockTransport,
    *,
    config: GenerationConfig,
):
    backend = OpenAICompatibleBackend(
        base_url="https://vllm.example/v1",
        client=httpx.Client(transport=transport),
        sleep=lambda _seconds: None,
    )
    return backend.generate_structured(
        messages=[ChatMessage(role="user", content="Return true.")],
        response_model=_ProbeSchema,
        config=config,
    )


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "llm_provider": "vllm",
        "vllm_api_key": "unit-vllm-secret",
        "vllm_qwen_base_url": "https://qwen.example/v1",
        "vllm_qwen_model": "Qwen/Qwen2.5-7B-Instruct-AWQ",
        "vllm_gemma_base_url": "https://gemma.example/v1",
        "vllm_gemma_model": "google/gemma-4-12B-it",
        "vllm_qwen3_30b_base_url": "https://qwen3.example/v1",
        "vllm_qwen3_30b_model": "stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


def test_vllm_runtime_routes_analyzer_and_verifier_to_separate_deployments() -> None:
    settings = _settings(vllm_analyzer_deployment="qwen", vllm_verifier_deployment="gemma")

    analyzer_backend, verifier_backend, analyzer, verifier = build_model_services(settings)
    try:
        assert isinstance(analyzer_backend, OpenAICompatibleBackend)
        assert isinstance(verifier_backend, OpenAICompatibleBackend)
        assert analyzer_backend.base_url == "https://qwen.example/v1"
        assert verifier_backend.base_url == "https://gemma.example/v1"
        assert analyzer_backend.backend_name == LLMProvider.VLLM.value
        assert verifier_backend.backend_name == LLMProvider.VLLM.value
        assert analyzer_backend.reported_provider_fallback == "qwen"
        assert verifier_backend.reported_provider_fallback == "gemma"
        assert analyzer_backend.send_thinking_parameters is False
        assert verifier_backend.send_thinking_parameters is False
        assert analyzer.config.model == settings.vllm_qwen_model
        assert verifier.config.model == settings.vllm_gemma_model
        assert analyzer.config.max_output_tokens == 4096
        assert verifier.config.max_output_tokens == 4096
        assert analyzer.config.allow_json_object_fallback is True
        assert verifier.config.allow_prompt_json_fallback is True
        assert analyzer.config.enable_thinking is None
    finally:
        analyzer_backend.close()
        verifier_backend.close()


def test_vllm_exact_model_discovery_and_gemma_served_id() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/health"):
            return httpx.Response(200, text="ok", request=request)
        model = {
            "qwen.example": "Qwen/Qwen2.5-7B-Instruct-AWQ",
            "gemma.example": "google/gemma-4-12B-it",
            "qwen3.example": "stelterlab/Qwen3-30B-A3B-Instruct-2507-AWQ",
        }[request.url.host]
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": model}]}, request=request
        )

    deployments = _settings().vllm_deployments
    catalog = VLLMModelCatalog(
        deployments=deployments,
        api_key="unit-vllm-secret",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    rows = catalog.check_exact_models()

    assert [row["deployment"] for row in rows] == ["qwen", "gemma", "qwen3_30b"]
    assert rows[1]["configured_model_id"] == "google/gemma-4-12B-it"
    assert all(
        request.headers["Authorization"] == "Bearer unit-vllm-secret" for request in requests
    )


def test_vllm_model_discovery_rejects_stale_model_without_substitution() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/health"):
            return httpx.Response(200, request=request)
        return httpx.Response(
            200,
            json={"data": [{"id": "google/gemma-4-12B-it"}]},
            request=request,
        )

    catalog = VLLMModelCatalog(
        deployments=(
            VLLMDeployment(
                name=VLLMDeploymentName.GEMMA,
                base_url="https://gemma.example/v1",
                model_id="google/gemma-4-E4B-it",
            ),
        ),
        api_key="unit-vllm-secret",
        timeout_seconds=1,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(InferenceError) as caught:
        catalog.check_exact_models()

    assert caught.value.category == InferenceErrorCategory.MODEL_UNAVAILABLE
    assert "google/gemma-4-12B-it" in str(caught.value)
    assert "google/gemma-4-E4B-it" in str(caught.value)


def test_vllm_secret_is_not_exposed_by_settings_or_generation_configuration() -> None:
    settings = _settings()

    assert "unit-vllm-secret" not in repr(settings)
    dumped = settings.model_dump(mode="json")
    assert json.dumps(dumped, default=str).count("unit-vllm-secret") == 0


def test_vllm_context_budget_reduces_output_reservation_before_transport() -> None:
    payloads: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"ok":true}'}}],
            },
            request=request,
        )

    result = _generate(
        httpx.MockTransport(handler),
        config=_generation_config(
            context_window_tokens=1024,
            context_safety_margin_tokens=128,
        ),
    )

    assert result.parsed == _ProbeSchema(ok=True)
    assert 64 <= int(payloads[0]["max_tokens"]) < 4096


def test_vllm_context_budget_rejects_oversized_prompt_without_transport() -> None:
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(500, request=request)

    backend = OpenAICompatibleBackend(
        base_url="https://vllm.example/v1",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(InferenceError) as caught:
        backend.generate_structured(
            messages=[ChatMessage(role="user", content="x" * 10_000)],
            response_model=_ProbeSchema,
            config=_generation_config(
                context_window_tokens=512,
                context_safety_margin_tokens=128,
            ),
        )

    assert caught.value.category == InferenceErrorCategory.INVALID_REQUEST
    assert "model_context_limit" in str(caught.value)
    assert request_count == 0


def test_vllm_provider_context_rejection_is_audited_and_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            400,
            json={"error": {"message": "maximum context length exceeded"}},
            request=request,
        )

    with pytest.raises(InferenceError) as caught:
        _generate(
            httpx.MockTransport(handler),
            config=_generation_config(max_retries=3),
        )

    assert caught.value.category == InferenceErrorCategory.INVALID_REQUEST
    assert "provider_rejected_context" in str(caught.value)
    assert calls == 1
