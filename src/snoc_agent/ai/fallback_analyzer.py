"""Confidence-aware analyzer wrapper that prefers the SVM classifier and falls back to the LLM."""

from __future__ import annotations

import json
from typing import Literal

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import StructuredGenerationResult
from snoc_agent.ai.candidate_extractor import extract_numeric_candidates
from snoc_agent.ai.schemas import EmailAnalysis, FieldEvidence, ProposedOperation
from snoc_agent.ai.svm_classifier import SVMClassifier
from snoc_agent.domain.value_objects import canonical_action

ProposedAction = Literal[
    "vpn_access",
    "otp_number_change",
    "account_unblock",
    "password_reset",
    "unknown",
]

# Required field slots per action, in the vocabulary ProposedOperation uses
# (OTP change uses "new_phone" for the same phone slot other actions call "phone").
_ACTION_FIELD_SLOTS: dict[str, tuple[str, ...]] = {
    "vpn_access": ("pdv_code", "phone"),
    "otp_number_change": ("pdv_code", "new_phone"),
    "account_unblock": ("pdv_code",),
    "password_reset": ("pdv_code",),
}


class FallbackAnalyzer(EmailAnalyzer):
    """Wrap an existing analyzer so the SVM handles high-confidence cases first."""

    def __init__(
        self,
        *,
        analyzer: EmailAnalyzer,
        svm_classifier: SVMClassifier,
        fallback_threshold: float = 0.8,
        gemma_threshold: float = 0.8,
    ) -> None:
        self.analyzer = analyzer
        self.svm_classifier = svm_classifier
        self.fallback_threshold = fallback_threshold
        self.gemma_threshold = gemma_threshold
        self.backend = analyzer.backend
        self.config = analyzer.config
        self.prompt_version = analyzer.prompt_version

    def analyze(self, context: dict[str, object]) -> StructuredGenerationResult:
        svm_result = self.svm_classifier.predict(context)
        svm_confidence = float(svm_result.get("best_confidence", 0.0))

        if svm_confidence >= self.fallback_threshold and bool(svm_result.get("confident", False)):
            label = str(svm_result.get("labels", {}).get("action", "irrelevant"))
            if label in {"account_unblock", "password_reset", "otp_number_change", "vpn_access"}:
                operation = self._build_operation_from_svm(
                    action=label,
                    context=context,
                    svm_confidence=svm_confidence,
                )
                analysis = EmailAnalysis(
                    message_kind="new_request",
                    operations=[operation],
                    new_request_present=True,
                    contradiction_with_stored_state=False,
                )
            else:
                analysis = EmailAnalysis(
                    message_kind="irrelevant",
                    operations=[],
                    new_request_present=False,
                    contradiction_with_stored_state=False,
                )
            return StructuredGenerationResult(
                parsed=analysis,
                raw_output=json.dumps(svm_result, ensure_ascii=False),
                model_name=self.config.model,
                backend="svm",
                latency_seconds=0.0,
                fallback_reason=None,
            )

        llm_result = self.analyzer.analyze(self._with_candidates(context))
        gemma_confidence = self._gemma_confidence(llm_result)
        parsed = llm_result.parsed
        is_clear_irrelevant = getattr(parsed, "message_kind", None) == "irrelevant" and not getattr(
            parsed, "new_request_present", False
        )
        fallback_note = (
            f"svm_confidence={svm_confidence:.3f} below threshold={self.fallback_threshold}; "
            f"used LLM analyzer"
        )
        llm_result.fallback_reason = fallback_note
        if gemma_confidence is not None and gemma_confidence >= self.gemma_threshold:
            return llm_result
        if is_clear_irrelevant:
            return llm_result

        escalation = EmailAnalysis(
            message_kind="ambiguous",
            operations=[],
            new_request_present=False,
            contradiction_with_stored_state=False,
            unresolved_ambiguities=["hybrid_classifier_low_confidence"],
        )
        return StructuredGenerationResult(
            parsed=escalation,
            raw_output=json.dumps(
                {"svm": svm_result, "gemma": llm_result.parsed.model_dump()}, ensure_ascii=False
            ),
            model_name=self.config.model,
            backend="human-escalation",
            latency_seconds=0.0,
            fallback_reason=(
                f"svm_confidence={svm_confidence:.3f}, gemma_confidence={gemma_confidence}; "
                f"both below thresholds"
            ),
        )

    def _build_operation_from_svm(
        self,
        *,
        action: str,
        context: dict[str, object],
        svm_confidence: float,
    ) -> ProposedOperation:
        """Deterministically populate pdv_code/phone from the raw email text so a
        confident SVM classification can carry enough evidence to auto-execute,
        instead of always producing an empty operation that stalls on missing
        required fields.
        """
        latest = context.get("latest_user_message") if isinstance(context, dict) else None
        text = latest if isinstance(latest, str) else ""
        candidates = extract_numeric_candidates(text) if text else []

        pdv_candidate = next((c for c in candidates if c.kind_hint == "pdv_or_unknown"), None)
        phone_candidate = next((c for c in candidates if c.kind_hint == "phone_or_unknown"), None)

        needed = _ACTION_FIELD_SLOTS.get(action, ())
        pdv_code = pdv_candidate.value if "pdv_code" in needed and pdv_candidate else None
        phone_value = None
        if ("phone" in needed or "new_phone" in needed) and phone_candidate:
            phone_value = phone_candidate.value

        evidence: list[FieldEvidence] = []
        missing_fields: list[str] = []
        if "pdv_code" in needed:
            if pdv_candidate:
                evidence.append(
                    FieldEvidence(
                        field_name="pdv_code",
                        value=pdv_candidate.value,
                        source="latest_user_message",
                        evidence_text=pdv_candidate.context,
                        support="supported",
                    )
                )
            else:
                missing_fields.append("pdv_code")
        for slot in ("phone", "new_phone"):
            if slot in needed:
                if phone_candidate:
                    evidence.append(
                        FieldEvidence(
                            field_name=slot,
                            value=phone_candidate.value,
                            source="latest_user_message",
                            evidence_text=phone_candidate.context,
                            support="supported",
                        )
                    )
                else:
                    missing_fields.append(slot)

        return ProposedOperation(
            local_operation_id="svm-op-1",
            action=canonical_action(action).value,
            pdv_code=pdv_code,
            phone=phone_value,
            missing_fields=missing_fields,
            evidence=evidence,
            raw_action_confidence=svm_confidence,
        )

    def _gemma_confidence(self, llm_result: StructuredGenerationResult) -> float | None:
        parsed = llm_result.parsed
        if hasattr(parsed, "operations") and isinstance(parsed.operations, list):
            for operation in parsed.operations:
                confidence = getattr(operation, "raw_action_confidence", None)
                if confidence is not None:
                    return float(confidence)
        if hasattr(parsed, "operations") and isinstance(parsed.operations, list):
            return 1.0 if parsed.operations else 0.0
        return None

    def _with_candidates(self, context: dict[str, object]) -> dict[str, object]:
        if not isinstance(context, dict):
            return context
        latest = context.get("latest_user_message")
        if not isinstance(latest, str) or not latest:
            return context
        from snoc_agent.ai.candidate_extractor import extract_numeric_candidates

        candidates = extract_numeric_candidates(latest)
        context["numeric_candidates"] = [candidate.model_dump() for candidate in candidates]
        return context
