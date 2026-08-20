"""Decimal-safe inference pricing and per-run budget enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from snoc_agent.ai.errors import InferenceError, InferenceErrorCategory
from snoc_agent.ai.provider import CostBasis

MILLION = Decimal("1000000")


def decimal_or_none(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= 0 else None


@dataclass(frozen=True, slots=True)
class CostCalculation:
    input_cost_usd: Decimal | None
    output_cost_usd: Decimal | None
    total_cost_usd: Decimal | None
    basis: CostBasis


def calculate_cost(
    *,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    pricing_metadata: dict[str, Any] | None,
    provider_reported_cost: object = None,
    provider_reported_input_cost: object = None,
    provider_reported_output_cost: object = None,
) -> CostCalculation:
    """Use reported cost when present, otherwise estimate only from explicit prices."""

    reported_input = decimal_or_none(provider_reported_input_cost)
    reported_output = decimal_or_none(provider_reported_output_cost)
    if reported_input is not None and reported_output is not None:
        return CostCalculation(
            reported_input,
            reported_output,
            reported_input + reported_output,
            CostBasis.EXACT,
        )
    reported = decimal_or_none(provider_reported_cost)
    if reported is not None:
        return CostCalculation(None, None, reported, CostBasis.PROVIDER_REPORTED)
    pricing = pricing_metadata or {}
    input_rate = decimal_or_none(
        next(
            (
                pricing[key]
                for key in ("input", "input_cost_per_million_tokens", "prompt")
                if key in pricing and pricing[key] is not None
            ),
            None,
        )
    )
    output_rate = decimal_or_none(
        next(
            (
                pricing[key]
                for key in ("output", "output_cost_per_million_tokens", "completion")
                if key in pricing and pricing[key] is not None
            ),
            None,
        )
    )
    if (
        prompt_tokens is None
        or completion_tokens is None
        or input_rate is None
        or output_rate is None
    ):
        return CostCalculation(None, None, None, CostBasis.UNKNOWN)
    input_cost = Decimal(prompt_tokens) * input_rate / MILLION
    output_cost = Decimal(completion_tokens) * output_rate / MILLION
    return CostCalculation(input_cost, output_cost, input_cost + output_cost, CostBasis.ESTIMATED)


@dataclass(slots=True)
class BudgetTracker:
    budget_usd: Decimal
    stop_before_usd: Decimal
    allow_unknown_cost: bool = True
    cost_so_far_usd: Decimal = Decimal("0")
    request_count: int = 0
    unknown_cost_request_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def before_attempt(self, projected_cost_usd: Decimal | None = None) -> None:
        if not self.allow_unknown_cost and self.unknown_cost_request_count:
            raise InferenceError(
                InferenceErrorCategory.BUDGET_EXHAUSTED,
                "provider pricing is unknown and EVALUATION_ALLOW_UNKNOWN_COST=false",
            )
        projected = projected_cost_usd or Decimal("0")
        if self.cost_so_far_usd + projected >= self.stop_before_usd:
            raise InferenceError(
                InferenceErrorCategory.BUDGET_EXHAUSTED,
                "inference budget stop threshold reached",
            )

    def record_attempt(self) -> None:
        """Count every request that crosses the transport boundary, including retries."""

        self.request_count += 1

    def record(
        self,
        *,
        total_cost_usd: Decimal | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> None:
        self.prompt_tokens += prompt_tokens or 0
        self.completion_tokens += completion_tokens or 0
        if total_cost_usd is None:
            self.unknown_cost_request_count += 1
            return
        self.cost_so_far_usd += total_cost_usd

    @property
    def status(self) -> str:
        if self.unknown_cost_request_count and not self.allow_unknown_cost:
            return "stopped_unknown_cost"
        if self.cost_so_far_usd >= self.stop_before_usd:
            return "stopped"
        if self.unknown_cost_request_count:
            return "unknown_cost_allowed"
        return "within_budget"

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "budget_usd": str(self.budget_usd),
            "stop_before_usd": str(self.stop_before_usd),
            "cost_so_far_usd": str(self.cost_so_far_usd),
            "request_count": self.request_count,
            "unknown_cost_request_count": self.unknown_cost_request_count,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "status": self.status,
        }
        if self.unknown_cost_request_count:
            payload["warning"] = (
                "One or more calls have unknown USD cost. Token and request counts are audited, "
                "but the local USD ceiling cannot be guaranteed."
                if self.allow_unknown_cost
                else "Inference stopped because provider pricing is unknown."
            )
        return payload
