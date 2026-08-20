from __future__ import annotations

from snoc_agent.ai.schemas import (
    EmailAnalysis,
    FieldEvidence,
    ProposedOperation,
    SemanticVerification,
)
from snoc_agent.domain.enums import (
    CorrelationStrength,
    FinalDecision,
    OperationAction,
    OperationStatus,
)
from snoc_agent.workflow.decision_engine import DecisionContext, HybridDecisionEngine


def _proposal(
    *,
    action: str = "account_unblock",
    pdv_code: str | None = "12345678",
    phone: str | None = None,
    missing_fields: list[str] | None = None,
    evidence: list[FieldEvidence] | None = None,
) -> ProposedOperation:
    if evidence is None:
        evidence = [
            FieldEvidence(
                field_name="pdv_code",
                value=pdv_code,
                source="latest_user_message",
                evidence_text=f"PDV {pdv_code}" if pdv_code else None,
                support="supported",
            )
        ]
        if phone is not None:
            evidence.append(
                FieldEvidence(
                    field_name="phone",
                    value=phone,
                    source="latest_user_message",
                    evidence_text=phone,
                    support="supported",
                )
            )
    return ProposedOperation(
        local_operation_id="OP-01",
        action=action,  # type: ignore[arg-type]
        pdv_code=pdv_code,
        phone=phone,
        missing_fields=missing_fields or [],
        evidence=evidence,
    )


def _analysis(
    proposal: ProposedOperation,
    *,
    message_kind: str = "new_request",
    contradiction: bool = False,
) -> EmailAnalysis:
    return EmailAnalysis(
        message_kind=message_kind,  # type: ignore[arg-type]
        operations=[proposal],
        new_request_present=message_kind in {"new_request", "mixed"},
        contradiction_with_stored_state=contradiction,
    )


def _verification(
    *,
    action_supported: str = "yes",
    pdv_supported: str = "yes",
    phone_supported: str = "not_required",
    stored_state_compatible: str = "yes",
    missing_fields: list[str] | None = None,
    correction_detected: bool = False,
    contradiction_present: bool = False,
    new_request_detected: bool = False,
) -> SemanticVerification:
    return SemanticVerification(
        action_supported=action_supported,  # type: ignore[arg-type]
        pdv_supported=pdv_supported,  # type: ignore[arg-type]
        phone_supported=phone_supported,  # type: ignore[arg-type]
        stored_state_compatible=stored_state_compatible,  # type: ignore[arg-type]
        contradiction_present=contradiction_present,
        contradiction_type="field_conflict" if contradiction_present else None,
        missing_fields=missing_fields or [],
        correction_detected=correction_detected,
        new_request_detected=new_request_detected,
    )


def _context(**overrides: object) -> DecisionContext:
    values: dict[str, object] = {
        "authorized_sender": True,
        "correlation_strength": CorrelationStrength.NEW,
        "correlation_conflict": False,
        "structured_output_valid": True,
        "operation_previously_executed": False,
        "operation_status": OperationStatus.READY_FOR_VALIDATION,
        "api_available": True,
        "execution_explicitly_enabled_or_dry_run": True,
        "pdv_pattern": r"^\d{8}$",
        "phone_pattern": r"^\+?\d{9,15}$",
        "current_candidate_values": frozenset({"12345678"}),
    }
    values.update(overrides)
    return DecisionContext(**values)  # type: ignore[arg-type]


def test_complete_supported_operation_auto_executes() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE
    assert result.analyzer_verifier_agreement is True
    assert all(result.hard_invariants.values())


def test_expected_new_request_signal_does_not_block_a_new_request() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="new_request"),
        proposal=proposal,
        verification=_verification(new_request_detected=True),
        context=_context(),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE


def test_populated_otp_phone_overrides_stale_model_missing_field() -> None:
    proposal = _proposal(
        action="otp_number_change",
        phone="0770000001",
        missing_fields=["new_phone"],
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="clarification_reply"),
        proposal=proposal,
        verification=_verification(phone_supported="yes"),
        context=_context(
            correlation_strength=CorrelationStrength.STRONG,
            enforce_evidence_provenance=False,
        ),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE


def test_new_request_signal_inside_clarification_forces_escalation() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="clarification_reply"),
        proposal=proposal,
        verification=_verification(new_request_detected=True),
        context=_context(correlation_strength=CorrelationStrength.STRONG),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert "unexpected_new_request_detected" in result.reasons


def test_verifier_disagreement_on_required_field_escalates() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(pdv_supported="no"),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.analyzer_verifier_agreement is False
    assert "analyzer_verifier_disagreement" in result.reasons


def test_irrelevant_phone_verdict_does_not_block_account_unblock() -> None:
    proposal = _proposal(action="account_unblock", phone=None)

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(phone_supported="no"),
        context=_context(),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE
    assert result.analyzer_verifier_agreement is True


def test_unsupported_analyzer_evidence_cannot_auto_execute() -> None:
    proposal = _proposal(
        evidence=[
            FieldEvidence(
                field_name="pdv_code",
                value="12345678",
                source="latest_user_message",
                evidence_text="number appears without attribution",
                support="unsupported",
            )
        ]
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.analyzer_verifier_agreement is False
    assert "required_field_evidence_incomplete" in result.reasons


def test_value_missing_from_allowed_current_candidates_cannot_auto_execute() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(
            current_candidate_values=frozenset({"87654321"}),
            enforce_evidence_provenance=True,
        ),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["required_evidence_from_allowed_context"] is False
    assert "required_field_evidence_not_in_allowed_context" in result.reasons


def test_strongly_correlated_stored_value_can_satisfy_provenance() -> None:
    proposal = _proposal(
        evidence=[
            FieldEvidence(
                field_name="pdv_code",
                value="12345678",
                source="stored_request_state",
                evidence_text="stored unresolved PDV",
                support="supported",
            )
        ]
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="clarification_reply"),
        proposal=proposal,
        verification=_verification(),
        context=_context(
            correlation_strength=CorrelationStrength.STRONG,
            stored_field_values=frozenset({"12345678"}),
            enforce_evidence_provenance=True,
        ),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE


def test_configured_raw_confidence_thresholds_fail_closed() -> None:
    proposal = _proposal().model_copy(update={"raw_action_confidence": 0.7})

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(analyzer_min_raw_confidence=0.8),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["configured_confidence_thresholds"] is False
    assert "analyzer_confidence_below_configured_threshold" in result.reasons


def test_model_cannot_add_unapproved_business_api_fields() -> None:
    proposal = _proposal(
        action="vpn_access",
        phone="0770000001",
    ).model_copy(update={"additional_fields": {"privileged_role": "administrator"}})

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(phone_supported="yes"),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["additional_fields_allowlisted"] is False
    assert "unapproved_additional_fields:privileged_role" in result.reasons


def test_existing_operation_action_cannot_be_changed_by_model_agreement() -> None:
    proposal = _proposal(
        action="vpn_access",
        phone="0770000001",
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="clarification_reply"),
        proposal=proposal,
        verification=_verification(phone_supported="yes"),
        context=_context(
            correlation_strength=CorrelationStrength.STRONG,
            expected_existing_action=OperationAction.OTP_NUMBER_CHANGE,
        ),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["proposal_matches_stored_action"] is False
    assert "proposal_action_differs_from_stored_operation" in result.reasons


def test_clear_missing_phone_asks_only_for_information() -> None:
    proposal = _proposal(
        action="otp_number_change",
        phone=None,
        missing_fields=["new_phone"],
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(
            phone_supported="unclear",
            missing_fields=["new_phone"],
        ),
        context=_context(correlation_strength=CorrelationStrength.STRONG),
    )

    assert result.decision is FinalDecision.ASK_FOR_INFORMATION
    assert "missing_required_field:new_phone" in result.reasons
    assert "required_field_evidence_incomplete" in result.reasons


def test_missing_field_with_scoped_candidate_ambiguity_asks_for_clarification() -> None:
    proposal = _proposal(
        action="otp_number_change",
        phone=None,
        missing_fields=["new_phone"],
    ).model_copy(update={"ambiguity_reasons": ["two phone candidates require confirmation"]})

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(
            phone_supported="unclear",
            missing_fields=["new_phone"],
        ),
        context=_context(correlation_strength=CorrelationStrength.STRONG),
    )

    assert result.decision is FinalDecision.ASK_FOR_INFORMATION
    assert "ambiguous_operation_or_attribution" in result.reasons


def test_scoped_ambiguity_does_not_block_an_independent_complete_operation() -> None:
    complete = _proposal()
    ambiguous = _proposal(
        action="vpn_access",
        pdv_code="87654321",
        phone=None,
        missing_fields=["phone"],
    ).model_copy(
        update={
            "local_operation_id": "OP-02",
            "ambiguity_reasons": ["phone candidates are ambiguous"],
        }
    )
    analysis = EmailAnalysis(
        message_kind="new_request",
        operations=[complete, ambiguous],
        new_request_present=True,
        contradiction_with_stored_state=False,
        unresolved_ambiguities=["OP-02 phone requires clarification"],
    )

    result = HybridDecisionEngine().decide(
        analysis=analysis,
        proposal=complete,
        verification=_verification(),
        context=_context(),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE


def test_explicit_request_level_ambiguity_blocks_every_operation() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(request_level_ambiguity=True),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert "ambiguous_operation_or_attribution" in result.reasons


def test_missing_data_with_weak_correlation_escalates_instead_of_asking() -> None:
    proposal = _proposal(pdv_code=None, missing_fields=["pdv_code"])

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(pdv_supported="unclear", missing_fields=["pdv_code"]),
        context=_context(correlation_strength=CorrelationStrength.WEAK),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert "weak_request_correlation" in result.reasons


def test_uncorrelated_new_operation_ignores_unsupported_model_correction_label() -> None:
    proposal = _proposal()
    analysis = _analysis(proposal, message_kind="correction").model_copy(
        update={"new_request_present": True}
    )

    result = HybridDecisionEngine().decide(
        analysis=analysis,
        proposal=proposal,
        verification=_verification(),
        context=_context(
            correlation_strength=CorrelationStrength.NEW,
            expected_existing_action=None,
        ),
    )

    assert result.decision is FinalDecision.AUTO_EXECUTE


def test_new_request_missing_field_takes_priority_over_false_correction_signal() -> None:
    proposal = _proposal(pdv_code=None, missing_fields=["pdv_code"])
    verification = _verification(
        pdv_supported="no",
        missing_fields=["pdv_code"],
        correction_detected=True,
        contradiction_present=True,
        new_request_detected=True,
    ).model_copy(update={"contradiction_type": "missing_field"})

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=verification,
        context=_context(
            correlation_strength=CorrelationStrength.NEW,
            current_candidate_values=frozenset(),
            expected_existing_action=None,
        ),
    )

    assert result.decision is FinalDecision.ASK_FOR_INFORMATION
    assert "missing_required_field:pdv_code" in result.reasons
    assert "semantic_contradiction" not in result.reasons


def test_correction_before_execution_is_sent_to_human_review() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, message_kind="correction"),
        proposal=proposal,
        verification=_verification(correction_detected=True),
        context=_context(
            correlation_strength=CorrelationStrength.STRONG,
            expected_existing_action=OperationAction.ACCOUNT_UNBLOCK,
        ),
    )

    assert result.decision is FinalDecision.REVIEW_CORRECTION


def test_request_wide_deterministic_safety_blocker_overrides_model_agreement() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(request_safety_reasons=("subject_body_pdv_conflict",)),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["request_safety_checks_passed"] is False
    assert "subject_body_pdv_conflict" in result.reasons


def test_explicit_verifier_hypothetical_signal_blocks_execution() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification().model_copy(
            update={
                "hypothetical_or_conditional": True,
                "direct_current_instruction": "no",
            }
        ),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert "verifier_hypothetical_or_conditional" in result.reasons


def test_completed_operation_is_never_executed_again() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(
            operation_status=OperationStatus.COMPLETED,
            operation_previously_executed=True,
        ),
    )

    assert result.decision is FinalDecision.REVIEW_CORRECTION
    assert "operation_revision_already_executed" in result.reasons
    assert "operation_already_completed" in result.reasons


def test_required_value_from_closed_history_forces_escalation() -> None:
    proposal = _proposal(
        evidence=[
            FieldEvidence(
                field_name="pdv_code",
                value="12345678",
                source="quoted_closed_history",
                evidence_text="Ancienne demande PDV 12345678",
                support="supported",
            )
        ]
    )

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal),
        proposal=proposal,
        verification=_verification(),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert result.hard_invariants["no_closed_history_field_source"] is False
    assert "required_value_only_in_closed_history" in result.reasons


def test_semantic_contradiction_forces_escalation() -> None:
    proposal = _proposal()

    result = HybridDecisionEngine().decide(
        analysis=_analysis(proposal, contradiction=True),
        proposal=proposal,
        verification=_verification(contradiction_present=True),
        context=_context(),
    )

    assert result.decision is FinalDecision.ESCALATE
    assert "semantic_contradiction" in result.reasons
