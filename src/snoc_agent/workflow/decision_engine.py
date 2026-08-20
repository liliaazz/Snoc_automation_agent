"""Hybrid semantic-verification and deterministic safety decision policy."""

from __future__ import annotations

from dataclasses import dataclass

from snoc_agent.ai.schemas import (
    EmailAnalysis,
    ProposedOperation,
    SemanticVerification,
)
from snoc_agent.domain.enums import (
    CorrelationStrength,
    FinalDecision,
    OperationAction,
    OperationStatus,
)
from snoc_agent.domain.value_objects import (
    canonical_action,
    normalize_numeric,
    required_fields,
    validate_operation_fields,
)

POLICY_VERSION = "hybrid-v3-request-safety"


@dataclass(frozen=True, slots=True)
class DecisionContext:
    authorized_sender: bool
    correlation_strength: CorrelationStrength
    correlation_conflict: bool
    structured_output_valid: bool
    operation_previously_executed: bool
    operation_status: OperationStatus
    api_available: bool
    execution_explicitly_enabled_or_dry_run: bool
    pdv_pattern: str
    phone_pattern: str
    current_candidate_values: frozenset[str] = frozenset()
    stored_field_values: frozenset[str] = frozenset()
    enforce_evidence_provenance: bool = True
    analyzer_min_raw_confidence: float | None = None
    verifier_min_raw_confidence: float | None = None
    vpn_allowed_additional_fields: frozenset[str] = frozenset()
    expected_existing_action: OperationAction | None = None
    latest_user_message: str = ""
    stored_additional_field_values: frozenset[tuple[str, str]] = frozenset()
    input_context_complete: bool = True
    request_level_ambiguity: bool = False
    request_safety_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: FinalDecision
    reasons: tuple[str, ...]
    hard_invariants: dict[str, bool]
    analyzer_verifier_agreement: bool


class HybridDecisionEngine:
    policy_version = POLICY_VERSION

    def decide(
        self,
        *,
        analysis: EmailAnalysis,
        proposal: ProposedOperation,
        verification: SemanticVerification,
        context: DecisionContext,
    ) -> DecisionResult:
        action = canonical_action(proposal.action)
        field_check = validate_operation_fields(
            action=action,
            pdv_code=proposal.pdv_code,
            phone=proposal.phone,
            pdv_pattern=context.pdv_pattern,
            phone_pattern=context.phone_pattern,
        )
        required = required_fields(action)
        evidence_by_field = {
            evidence.field_name: evidence
            for evidence in proposal.evidence
            if evidence.value is not None and evidence.support == "supported"
        }
        forbidden_history = any(
            evidence.source == "quoted_closed_history"
            and evidence.field_name in {"pdv_code", "phone", "new_phone"}
            and evidence.value is not None
            for evidence in proposal.evidence
        )
        required_evidence_complete = (not context.enforce_evidence_provenance) or all(
            field in evidence_by_field or (field == "new_phone" and "phone" in evidence_by_field)
            for field in required
        )

        def proposal_value(field: str) -> str | None:
            value = proposal.phone if field in {"phone", "new_phone"} else proposal.pdv_code
            return normalize_numeric(value, keep_leading_plus=field in {"phone", "new_phone"})

        def evidence_allowed(field: str) -> bool:
            expected = proposal_value(field)
            if expected is None:
                return True
            field_names = {field, "phone"} if field == "new_phone" else {field}
            for evidence in proposal.evidence:
                evidence_value = normalize_numeric(
                    evidence.value,
                    keep_leading_plus=field in {"phone", "new_phone"},
                )
                if (
                    evidence.field_name not in field_names
                    or evidence.support != "supported"
                    or evidence_value != expected
                ):
                    continue
                if (
                    evidence.source == "latest_user_message"
                    and expected in context.current_candidate_values
                ):
                    return True
                if (
                    evidence.source == "stored_request_state"
                    and context.correlation_strength == CorrelationStrength.STRONG
                    and expected in context.stored_field_values
                ):
                    return True
            return False

        present_required_evidence_allowed = not context.enforce_evidence_provenance or all(
            evidence_allowed(field) for field in required if proposal_value(field) is not None
        )
        analyzer_confidence_passed = context.analyzer_min_raw_confidence is None or (
            proposal.raw_action_confidence is not None
            and proposal.raw_action_confidence >= context.analyzer_min_raw_confidence
        )
        verifier_confidence_passed = context.verifier_min_raw_confidence is None or (
            verification.raw_confidence is not None
            and verification.raw_confidence >= context.verifier_min_raw_confidence
        )
        configured_confidence_thresholds_passed = (
            analyzer_confidence_passed and verifier_confidence_passed
        )
        allowed_additional_fields = (
            context.vpn_allowed_additional_fields
            if action == OperationAction.VPN_ACCESS
            else frozenset()
        )
        unexpected_additional_fields = set(proposal.additional_fields) - allowed_additional_fields
        additional_fields_evidence_supported = True
        for field_name, raw_value in proposal.additional_fields.items():
            if raw_value is None or not raw_value.strip():
                additional_fields_evidence_supported = False
                break
            normalized_value = raw_value.strip().casefold()
            matching_evidence = [
                evidence
                for evidence in proposal.evidence
                if evidence.field_name == field_name
                and evidence.value is not None
                and evidence.value.strip().casefold() == normalized_value
                and evidence.support == "supported"
            ]
            analyzer_support = any(
                (
                    evidence.source == "latest_user_message"
                    and normalized_value in context.latest_user_message.casefold()
                )
                or (
                    evidence.source == "stored_request_state"
                    and context.correlation_strength == CorrelationStrength.STRONG
                    and (field_name, normalized_value) in context.stored_additional_field_values
                )
                for evidence in matching_evidence
            )
            if (
                not analyzer_support
                or verification.additional_fields_supported.get(field_name) != "yes"
            ):
                additional_fields_evidence_supported = False
                break
        proposal_matches_stored_action = (
            context.expected_existing_action is None or action == context.expected_existing_action
        )
        phone_is_required = action in {
            OperationAction.VPN_ACCESS,
            OperationAction.OTP_NUMBER_CHANGE,
        }
        semantic_fields_supported = verification.pdv_supported == "yes" and (
            verification.phone_supported == "yes" if phone_is_required else True
        )
        agreement = (
            verification.action_supported == "yes"
            and semantic_fields_supported
            and required_evidence_complete
            and present_required_evidence_allowed
        )
        contradiction = (
            analysis.contradiction_with_stored_state
            or (
                verification.contradiction_present
                and verification.contradiction_type
                not in {"missing_field", "missing_required_field"}
            )
            or (
                verification.stored_state_compatible == "no"
                and context.correlation_strength != CorrelationStrength.NEW
            )
        )
        unexpected_new_request = (
            verification.new_request_detected
            and analysis.message_kind
            not in {
                "new_request",
                "mixed",
            }
        )
        local_operation_ids = {
            operation.local_operation_id.casefold() for operation in analysis.operations
        }
        proposal_id = proposal.local_operation_id.casefold()
        scoped_analysis_ambiguity = any(
            proposal_id in reason.casefold() for reason in analysis.unresolved_ambiguities
        )
        unscoped_analysis_ambiguity = any(
            not any(operation_id in reason.casefold() for operation_id in local_operation_ids)
            for reason in analysis.unresolved_ambiguities
        )
        major_ambiguity = analysis.message_kind == "ambiguous" or unscoped_analysis_ambiguity
        ambiguous = bool(
            proposal.ambiguity_reasons
            or scoped_analysis_ambiguity
            or major_ambiguity
            or context.request_level_ambiguity
        )
        semantic_safety_reasons: list[str] = []
        if verification.hypothetical_or_conditional:
            semantic_safety_reasons.append("verifier_hypothetical_or_conditional")
        if verification.direct_current_instruction == "no":
            semantic_safety_reasons.append("verifier_no_direct_current_instruction")
        if verification.subject_body_conflict:
            semantic_safety_reasons.append("verifier_subject_body_conflict")
        if verification.ambiguity_detected:
            semantic_safety_reasons.append("verifier_ambiguity_detected")
        if verification.evidence_sources_valid is False:
            semantic_safety_reasons.append("verifier_evidence_source_invalid")
        if verification.candidate_mapping_explicit is False:
            semantic_safety_reasons.append("verifier_identifier_mapping_not_explicit")
        deterministic_safety_passed = not (
            context.request_safety_reasons or semantic_safety_reasons
        )
        # Models occasionally return a populated value while leaving its field in
        # ``missing_fields``. Resolve that internal contradiction deterministically:
        # a locally valid present value is not missing. Unknown/custom missing keys
        # remain conservative and continue to block execution.
        reported_missing = set(proposal.missing_fields) | set(verification.missing_fields)
        missing = {
            field
            for field in reported_missing
            if field not in {"pdv_code", "phone", "new_phone"} or proposal_value(field) is None
        }
        hard_invariants = {
            "authorized_sender": context.authorized_sender,
            "structured_output_valid": context.structured_output_valid,
            "known_action": action != OperationAction.UNKNOWN,
            "field_formats_and_completeness": field_check.passed,
            "no_correlation_conflict": not context.correlation_conflict,
            "no_previous_execution": not context.operation_previously_executed,
            "operation_open": context.operation_status
            not in {OperationStatus.COMPLETED, OperationStatus.CANCELLED},
            "no_closed_history_field_source": not forbidden_history,
            "required_evidence_from_allowed_context": present_required_evidence_allowed,
            "configured_confidence_thresholds": configured_confidence_thresholds_passed,
            "additional_fields_allowlisted": not unexpected_additional_fields,
            "additional_fields_evidence_supported": additional_fields_evidence_supported,
            "proposal_matches_stored_action": proposal_matches_stored_action,
            "execution_mode_configured": context.execution_explicitly_enabled_or_dry_run,
            "input_context_complete": context.input_context_complete,
            "request_safety_checks_passed": deterministic_safety_passed,
        }

        reasons: list[str] = [
            *field_check.reasons,
            *context.request_safety_reasons,
            *semantic_safety_reasons,
        ]
        if not context.authorized_sender:
            reasons.append("unauthorized_sender")
        if context.correlation_conflict:
            reasons.append("correlation_conflict")
        if context.operation_previously_executed:
            reasons.append("operation_revision_already_executed")
        if context.operation_status == OperationStatus.COMPLETED:
            reasons.append("operation_already_completed")
        if forbidden_history:
            reasons.append("required_value_only_in_closed_history")
        if contradiction:
            reasons.append("semantic_contradiction")
        if ambiguous:
            reasons.append("ambiguous_operation_or_attribution")
        if not agreement:
            reasons.append("analyzer_verifier_disagreement")
        if not required_evidence_complete:
            reasons.append("required_field_evidence_incomplete")
        if not present_required_evidence_allowed:
            reasons.append("required_field_evidence_not_in_allowed_context")
        if not analyzer_confidence_passed:
            reasons.append("analyzer_confidence_below_configured_threshold")
        if not verifier_confidence_passed:
            reasons.append("verifier_confidence_below_configured_threshold")
        if unexpected_additional_fields:
            reasons.append(
                "unapproved_additional_fields:" + ",".join(sorted(unexpected_additional_fields))
            )
        if not additional_fields_evidence_supported:
            reasons.append("additional_field_evidence_or_verifier_support_missing")
        if not proposal_matches_stored_action:
            reasons.append("proposal_action_differs_from_stored_operation")
        if context.correlation_strength == CorrelationStrength.WEAK:
            reasons.append("weak_request_correlation")
        if unexpected_new_request:
            reasons.append("unexpected_new_request_detected")
        if not context.input_context_complete:
            reasons.extend(
                (
                    "context_truncated",
                    "safety_context_incomplete",
                    "model_context_limit",
                    "email_or_model_context_limit_exceeded",
                )
            )

        if analysis.message_kind in {"irrelevant", "automated"}:
            return DecisionResult(FinalDecision.IGNORE, tuple(reasons), hard_invariants, agreement)
        if context.operation_status == OperationStatus.COMPLETED:
            return DecisionResult(
                FinalDecision.REVIEW_CORRECTION, tuple(reasons), hard_invariants, agreement
            )
        correction_signal = (
            verification.correction_detected or analysis.message_kind == "correction"
        )
        # Correlation is application-owned. A model cannot turn an independent
        # message into a correction when no existing operation was correlated.
        unsupported_new_correction = (
            correction_signal
            and context.correlation_strength == CorrelationStrength.NEW
            and context.expected_existing_action is None
        )
        if missing or not field_check.passed:
            only_missing = all(
                reason.startswith("missing_required_field:") for reason in field_check.reasons
            )
            sufficiently_correlated = context.correlation_strength in {
                CorrelationStrength.NEW,
                CorrelationStrength.STRONG,
            }
            if (
                only_missing
                and sufficiently_correlated
                and context.authorized_sender
                and (not correction_signal or unsupported_new_correction)
            ):
                return DecisionResult(
                    FinalDecision.ASK_FOR_INFORMATION,
                    tuple(dict.fromkeys(reasons)),
                    hard_invariants,
                    agreement,
                )
            if correction_signal and not unsupported_new_correction:
                return DecisionResult(
                    FinalDecision.REVIEW_CORRECTION,
                    tuple(dict.fromkeys(reasons)),
                    hard_invariants,
                    agreement,
                )
            return DecisionResult(
                FinalDecision.ESCALATE,
                tuple(dict.fromkeys(reasons)),
                hard_invariants,
                agreement,
            )
        if correction_signal and not unsupported_new_correction:
            return DecisionResult(
                FinalDecision.REVIEW_CORRECTION, tuple(reasons), hard_invariants, agreement
            )

        can_execute = (
            all(hard_invariants.values())
            and context.correlation_strength
            in {
                CorrelationStrength.NEW,
                CorrelationStrength.STRONG,
            }
            and agreement
            and required_evidence_complete
            and not contradiction
            and not ambiguous
            and not unexpected_new_request
            and context.api_available
        )
        if can_execute:
            return DecisionResult(
                FinalDecision.AUTO_EXECUTE,
                tuple(dict.fromkeys(reasons)),
                hard_invariants,
                agreement,
            )
        if not context.api_available:
            reasons.append("business_api_unavailable")
        return DecisionResult(
            FinalDecision.ESCALATE,
            tuple(dict.fromkeys(reasons)),
            hard_invariants,
            agreement,
        )
