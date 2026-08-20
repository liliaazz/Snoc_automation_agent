"""Adapter from production analyzer/verifier contracts to offline metric payloads."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import StructuredGenerationResult
from snoc_agent.ai.candidate_extractor import extract_numeric_candidates
from snoc_agent.ai.context_builder import ContextBuilder
from snoc_agent.ai.schemas import EmailAnalysis, ProposedOperation, SemanticVerification
from snoc_agent.ai.verifier import SemanticVerifier
from snoc_agent.config import Settings
from snoc_agent.domain.enums import CorrelationStrength, OperationStatus
from snoc_agent.domain.value_objects import canonical_action, normalize_numeric
from snoc_agent.evaluation.dataset_loader import EvaluationExample
from snoc_agent.workflow.decision_engine import DecisionContext, HybridDecisionEngine


def _known_total_cost(results: list[StructuredGenerationResult]) -> Decimal | None:
    costs = [result.total_cost_usd for result in results]
    if any(cost is None for cost in costs):
        return None
    return sum((cost for cost in costs if cost is not None), Decimal("0"))


def evaluation_context_builder(settings: Settings) -> ContextBuilder:
    return ContextBuilder(
        max_context_characters=settings.max_model_context_characters,
        max_latest_characters=settings.max_latest_message_characters,
        max_relevant_thread_characters=settings.max_relevant_thread_characters,
    )


def evaluation_context(
    example: EvaluationExample,
    *,
    context_builder: ContextBuilder | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    builder = context_builder or ContextBuilder()
    latest = "\n".join(value for value in (example.subject, example.body) if value)
    candidates = [
        candidate.model_dump(mode="json") for candidate in extract_numeric_candidates(latest)
    ]
    bounded_body, warnings = builder.bound_latest_text(example.body)
    clarification = example.metadata.get("clarification_state")
    if isinstance(clarification, Mapping):
        target = dict(clarification)
        return (
            builder.finalize_context(
                {
                    "mode": "clarification_reply",
                    "request_reference": target.get("request_reference"),
                    "latest_user_message": bounded_body,
                    "previous_agent_question": target.get("previous_agent_question"),
                    "target_operations": [
                        {
                            "operation_id": target.get("operation_id"),
                            "action": target.get("action"),
                            "known_fields": dict(target.get("known_fields") or {}),
                            "missing_fields": list(target.get("missing_fields") or []),
                        }
                    ],
                    "numeric_candidates_from_latest_reply": candidates,
                },
                warnings,
            ),
            candidates,
        )
    return (
        builder.finalize_context(
            {
                "mode": "new_request",
                "subject": example.subject,
                "latest_user_message": bounded_body,
                "text_since_last_closed_request": bounded_body,
                "numeric_candidates": candidates,
                "closed_history_summary": None,
            },
            warnings,
        ),
        candidates,
    )


def evaluation_verifier_inputs(
    example: EvaluationExample,
    proposal: ProposedOperation,
    *,
    context_builder: ContextBuilder | None = None,
) -> dict[str, Any]:
    context, _source_candidates = evaluation_context(example, context_builder=context_builder)
    mode = str(context.get("mode") or "new_request")
    targets = context.get("target_operations") or context.get("stored_operations") or []
    stored_state: dict[str, Any] = {}
    if isinstance(targets, list):
        for target in targets:
            if isinstance(target, Mapping) and target.get("action") == proposal.action:
                stored_state = dict(target)
                break
    candidates = context.get("numeric_candidates") or context.get(
        "numeric_candidates_from_latest_reply", []
    )
    if not isinstance(candidates, list):
        candidates = []
    return {
        "context_mode": mode,
        "latest_user_message": str(context.get("latest_user_message") or ""),
        "stored_operation_state": stored_state,
        "candidate_evidence": candidates,
        "correlation_strength": (
            CorrelationStrength.STRONG.value
            if mode != "new_request"
            else CorrelationStrength.NEW.value
        ),
    }


def materialize_prediction(
    example: EvaluationExample,
    *,
    analyzer_result: StructuredGenerationResult,
    verifier_results: list[StructuredGenerationResult],
    context_builder: ContextBuilder | None = None,
) -> dict[str, Any]:
    analysis = analyzer_result.parsed
    if not isinstance(analysis, EmailAnalysis):
        raise TypeError("analyzer returned the wrong schema")
    if len(verifier_results) != len(analysis.operations):
        raise ValueError("every analyzer proposal must have one verifier result")
    context, candidates = evaluation_context(example, context_builder=context_builder)
    current_candidate_values = frozenset(
        normalized
        for candidate in candidates
        if (normalized := normalize_numeric(str(candidate["value"]), keep_leading_plus=True))
    )
    decision_engine = HybridDecisionEngine()
    operations: list[dict[str, Any]] = []
    decisions: list[str] = []
    agreements: list[bool] = []
    source_results = [analyzer_result, *verifier_results]
    prompt_tokens = (
        sum(result.prompt_tokens for result in source_results if result.prompt_tokens is not None)
        if all(result.prompt_tokens is not None for result in source_results)
        else None
    )
    completion_tokens = (
        sum(
            result.completion_tokens
            for result in source_results
            if result.completion_tokens is not None
        )
        if all(result.completion_tokens is not None for result in source_results)
        else None
    )
    total_cost = _known_total_cost(source_results)
    modes = [analyzer_result.structured_output_mode]
    logprob_margins = [
        float(value)
        for key in ("label_margin", "minimum_token_margin")
        if isinstance((value := analyzer_result.logprob_metrics.get(key)), int | float)
    ]
    confidence_values = [
        value
        for proposal in analysis.operations
        if (value := proposal.raw_action_confidence) is not None
    ]
    for proposal, verifier_result in zip(analysis.operations, verifier_results, strict=True):
        verification = verifier_result.parsed
        if not isinstance(verification, SemanticVerification):
            raise TypeError("verifier returned the wrong schema")
        modes.append(verifier_result.structured_output_mode)
        logprob_margins.extend(
            float(value)
            for key in ("label_margin", "minimum_token_margin")
            if isinstance((value := verifier_result.logprob_metrics.get(key)), int | float)
        )
        if verification.raw_confidence is not None:
            confidence_values.append(verification.raw_confidence)
        verifier_inputs = evaluation_verifier_inputs(
            example, proposal, context_builder=context_builder
        )
        stored_state = verifier_inputs["stored_operation_state"]
        known_fields = (
            dict(stored_state.get("known_fields") or {})
            if isinstance(stored_state, Mapping)
            else {}
        )
        stored_field_values = frozenset(
            normalized
            for raw_value in known_fields.values()
            if isinstance(raw_value, str)
            and (normalized := normalize_numeric(raw_value, keep_leading_plus=True))
        )
        mode = str(context.get("mode") or "new_request")
        decision = decision_engine.decide(
            analysis=analysis,
            proposal=proposal,
            verification=verification,
            context=DecisionContext(
                authorized_sender=True,
                correlation_strength=(
                    CorrelationStrength.STRONG if mode != "new_request" else CorrelationStrength.NEW
                ),
                correlation_conflict=False,
                structured_output_valid=True,
                operation_previously_executed=False,
                operation_status=OperationStatus.NEW,
                api_available=True,
                execution_explicitly_enabled_or_dry_run=True,
                pdv_pattern=r"^\d{8}$",
                phone_pattern=r"^\+?\d{9,15}$",
                current_candidate_values=current_candidate_values,
                stored_field_values=stored_field_values,
                enforce_evidence_provenance=True,
                expected_existing_action=(
                    canonical_action(str(stored_state.get("action")))
                    if isinstance(stored_state, Mapping) and stored_state.get("action")
                    else None
                ),
                latest_user_message=str(context.get("latest_user_message") or ""),
                input_context_complete=not bool(context.get("context_limit_warnings")),
            ),
        )
        agreements.append(decision.analyzer_verifier_agreement)
        decisions.append(decision.decision.value)
        operations.append(
            {
                "action": proposal.action,
                "pdv_code": proposal.pdv_code,
                "phone": proposal.phone,
                "additional_fields": proposal.additional_fields,
                "decision": decision.decision.value,
            }
        )
    predicted_label = (
        operations[0]["action"]
        if len(operations) == 1
        else "multiple"
        if operations
        else analysis.message_kind
    )
    incremental_results = [result for result in source_results if not result.cache_hit]
    incremental_cost = _known_total_cost(incremental_results)
    return {
        "predicted_label": predicted_label,
        "operations": operations,
        "decisions": decisions,
        "structured_output_valid": True,
        "structured_output_modes": modes,
        "structured_output_schema_guaranteed": all(mode == "json_schema" for mode in modes),
        "analyzer_verifier_agreement": all(agreements) if agreements else True,
        "validation_passed": (
            all(decision == "auto_execute" for decision in decisions) if operations else None
        ),
        "contradiction_present": analysis.contradiction_with_stored_state,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": (
            prompt_tokens + completion_tokens
            if prompt_tokens is not None and completion_tokens is not None
            else None
        ),
        "total_cost_usd": str(total_cost) if total_cost is not None else None,
        "cost_bases": [result.cost_basis for result in source_results],
        "incremental_prompt_tokens": (
            0 if analyzer_result.cache_hit else analyzer_result.prompt_tokens or 0
        )
        + sum(0 if result.cache_hit else result.prompt_tokens or 0 for result in verifier_results),
        "incremental_completion_tokens": (
            0 if analyzer_result.cache_hit else analyzer_result.completion_tokens or 0
        )
        + sum(
            0 if result.cache_hit else result.completion_tokens or 0 for result in verifier_results
        ),
        "incremental_total_cost_usd": (
            str(incremental_cost) if incremental_cost is not None else None
        ),
        "incremental_cost_known": incremental_cost is not None,
        "usage_cost_semantics": (
            "prompt/completion/total_cost describe the source model runs; incremental fields "
            "exclude cache and resume reuse"
        ),
        "cache_hits": int(analyzer_result.cache_hit)
        + sum(int(result.cache_hit) for result in verifier_results),
        "raw_confidence": min(confidence_values) if confidence_values else None,
        "logprob_margin": min(logprob_margins) if logprob_margins else None,
    }


class PipelineEvaluationPredictor:
    """Run models without persistence, authorization data, or ground-truth access."""

    def __init__(
        self,
        analyzer: EmailAnalyzer,
        verifier: SemanticVerifier,
        *,
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.analyzer = analyzer
        self.verifier = verifier
        self.context_builder = context_builder or ContextBuilder()

    def predict(self, example: EvaluationExample) -> dict[str, Any]:
        context, _candidates = evaluation_context(example, context_builder=self.context_builder)
        analyzer_result = self.analyzer.analyze(context)
        analysis = analyzer_result.parsed
        if not isinstance(analysis, EmailAnalysis):
            raise TypeError("analyzer returned the wrong schema")
        verifier_results = []
        for proposal in analysis.operations:
            verifier_results.append(
                self.verifier.verify(
                    proposed_operation=proposal,
                    **evaluation_verifier_inputs(
                        example,
                        proposal,
                        context_builder=self.context_builder,
                    ),
                )
            )
        return materialize_prediction(
            example,
            analyzer_result=analyzer_result,
            verifier_results=verifier_results,
            context_builder=self.context_builder,
        )
