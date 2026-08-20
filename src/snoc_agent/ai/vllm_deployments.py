"""Configured Qwen/Gemma vLLM deployments and exact model discovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from snoc_agent.ai.errors import InferenceError, classify_http_failure
from snoc_agent.ai.provider import VLLMDeploymentName


@dataclass(frozen=True, slots=True)
class VLLMDeployment:
    name: VLLMDeploymentName
    base_url: str
    model_id: str

    @property
    def health_url(self) -> str:
        root = self.base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[:-3]
        return f"{root}/health"

    @property
    def models_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/models"


def resolve_vllm_deployment(
    requested: str,
    deployments: tuple[VLLMDeployment, ...],
) -> VLLMDeployment:
    """Resolve an alias or exact served model ID without silent substitution."""

    value = requested.strip()
    for deployment in deployments:
        if value.casefold() == deployment.name.value or value == deployment.model_id:
            return deployment
    choices = sorted(
        {
            item
            for deployment in deployments
            for item in (deployment.name.value, deployment.model_id)
        }
    )
    raise ValueError(f"unknown vLLM deployment/model {requested!r}; configured choices={choices}")


class VLLMModelCatalog:
    """Read the small model catalogs exposed by configured vLLM servers."""

    def __init__(
        self,
        *,
        deployments: tuple[VLLMDeployment, ...],
        api_key: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.deployments = deployments
        self._owns_client = client is None
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.Client(timeout=timeout_seconds, headers=headers)
        if client is not None and headers:
            self.client.headers.update(headers)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    @staticmethod
    def _payload(response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            raise classify_http_failure(response.status_code, response.text)
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError("vLLM model endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("vLLM model endpoint returned an unexpected payload")
        return payload

    def list_models(self, *, check_health: bool = True) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for deployment in self.deployments:
            healthy: bool | None = None
            if check_health:
                health = self.client.get(deployment.health_url)
                healthy = health.status_code == 200
                if health.is_error:
                    raise classify_http_failure(health.status_code, health.text)
            payload = self._payload(self.client.get(deployment.models_url))
            raw_models = payload.get("data", [])
            models = [item for item in raw_models if isinstance(item, dict)]
            served_ids = [str(item["id"]) for item in models if isinstance(item.get("id"), str)]
            rows.append(
                {
                    "deployment": deployment.name.value,
                    "base_url": deployment.base_url,
                    "configured_model_id": deployment.model_id,
                    "served_model_ids": served_ids,
                    "healthy": healthy,
                    "models": models,
                }
            )
        return rows

    def check_exact_models(self) -> list[dict[str, Any]]:
        rows = self.list_models()
        failures = [
            f"{row['deployment']} requires {row['configured_model_id']!r}, "
            f"served={row['served_model_ids']}"
            for row in rows
            if row["configured_model_id"] not in row["served_model_ids"]
        ]
        if failures:
            raise InferenceError(
                classify_http_failure(404, "model unavailable").category,
                "configured vLLM model mismatch: " + "; ".join(failures),
                status_code=404,
            )
        return rows
