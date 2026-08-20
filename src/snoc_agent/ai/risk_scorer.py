"""ML-based risk scoring module.

Combines multiple signals (SVM confidence, LLM confidence, correlation strength,
field completeness, sender authorization) into a unified risk score for each operation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import ClassVar

logger = logging.getLogger(__name__)


@dataclass
class RiskSignals:
    """Individual risk signals extracted from an operation and its context."""

    svm_confidence: float = 0.0
    llm_confidence: float = 0.0
    verifier_confidence: float = 0.0
    correlation_strength: str = "none"
    sender_authorized: bool = True
    has_missing_fields: bool = False
    missing_field_count: int = 0
    model_agreement: bool = True
    contradiction_detected: bool = False
    operation_action: str = ""
    prior_escalations: int = 0


@dataclass
class RiskScore:
    """Combined risk score with breakdown."""

    overall: float = 0.0
    risk_level: str = "low"
    signals: RiskSignals = field(default_factory=RiskSignals)
    factors: dict[str, float] = field(default_factory=dict)
    recommendation: str = "auto_execute"


class RiskScorer:
    """Compute a unified risk score from multiple classification signals.

    The risk score is a weighted combination of:
    - Confidence signals (SVM, LLM, verifier): higher confidence = lower risk
    - Correlation strength: strong = lower risk
    - Sender authorization: authorized = lower risk
    - Field completeness: complete = lower risk
    - Model agreement: agreement = lower risk
    - Contradictions: contradiction = higher risk
    """

    # Weights for each risk factor (higher = more impact on risk)
    WEIGHTS: ClassVar[dict[str, float]] = {
        "confidence": 0.30,
        "correlation": 0.15,
        "authorization": 0.15,
        "completeness": 0.15,
        "agreement": 0.15,
        "contradiction": 0.10,
    }

    # Risk thresholds
    LOW_RISK_THRESHOLD: ClassVar[float] = 0.3
    MEDIUM_RISK_THRESHOLD: ClassVar[float] = 0.6
    HIGH_RISK_THRESHOLD: ClassVar[float] = 0.8

    def score(self, signals: RiskSignals) -> RiskScore:
        """Compute the combined risk score from individual signals."""
        factors: dict[str, float] = {}

        # 1. Confidence factor: combine SVM, LLM, and verifier confidence
        #    Lower confidence -> higher risk (inverted to 0-1 scale where 1 = high risk)
        avg_confidence = self._average_confidence(signals)
        factors["confidence"] = 1.0 - avg_confidence

        # 2. Correlation factor
        correlation_map = {"strong": 0.1, "weak": 0.5, "none": 0.9}
        factors["correlation"] = correlation_map.get(signals.correlation_strength, 0.9)

        # 3. Authorization factor
        factors["authorization"] = 0.0 if signals.sender_authorized else 1.0

        # 4. Completeness factor
        if signals.missing_field_count == 0:
            factors["completeness"] = 0.0
        elif signals.missing_field_count <= 2:
            factors["completeness"] = 0.4
        else:
            factors["completeness"] = 0.8

        # 5. Agreement factor
        factors["agreement"] = 0.0 if signals.model_agreement else 0.7

        # 6. Contradiction factor
        factors["contradiction"] = 0.9 if signals.contradiction_detected else 0.0

        # Compute weighted overall score
        overall = sum(factors[k] * self.WEIGHTS[k] for k in self.WEIGHTS)
        overall = max(0.0, min(1.0, overall))

        # Determine risk level
        if overall <= self.LOW_RISK_THRESHOLD:
            risk_level = "low"
        elif overall <= self.MEDIUM_RISK_THRESHOLD:
            risk_level = "medium"
        elif overall <= self.HIGH_RISK_THRESHOLD:
            risk_level = "high"
        else:
            risk_level = "critical"

        # Determine recommendation
        recommendation = self._recommend(overall, risk_level, signals)

        return RiskScore(
            overall=round(overall, 4),
            risk_level=risk_level,
            signals=signals,
            factors={k: round(v, 4) for k, v in factors.items()},
            recommendation=recommendation,
        )

    def score_from_operation_data(
        self,
        *,
        analyzer_confidence: dict | None = None,
        verifier_confidence: dict | None = None,
        correlation: dict | None = None,
        sender_authorized: bool = True,
        missing_fields: list[str] | None = None,
        model_agreement: bool | None = None,
        contradiction_data: dict | None = None,
        action: str = "",
        prior_escalations: int = 0,
    ) -> RiskScore:
        """Build RiskScore from raw operation data (DB-friendly interface)."""
        signals = RiskSignals(
            svm_confidence=self._extract_confidence(analyzer_confidence, "svm"),
            llm_confidence=self._extract_confidence(analyzer_confidence, "llm"),
            verifier_confidence=self._extract_raw_confidence(verifier_confidence),
            correlation_strength=(correlation or {}).get("strength", "none"),
            sender_authorized=sender_authorized,
            has_missing_fields=bool(missing_fields),
            missing_field_count=len(missing_fields or []),
            model_agreement=model_agreement if model_agreement is not None else True,
            contradiction_detected=bool(contradiction_data),
            operation_action=action,
            prior_escalations=prior_escalations,
        )
        return self.score(signals)

    def _average_confidence(self, signals: RiskSignals) -> float:
        """Compute average confidence across available signals (0-1 scale)."""
        values = []
        if signals.svm_confidence > 0:
            values.append(signals.svm_confidence)
        if signals.llm_confidence > 0:
            values.append(signals.llm_confidence)
        if signals.verifier_confidence > 0:
            values.append(signals.verifier_confidence)
        return sum(values) / max(len(values), 1)

    def _extract_confidence(self, conf: dict | None, source: str) -> float:
        """Extract confidence for a specific source from analyzer_confidence dict."""
        if not conf:
            return 0.0
        # Check source-specific keys
        for key in (
            f"raw_{source}_confidence",
            f"{source}_confidence",
            "raw_model_confidence",
            "confidence",
        ):
            val = conf.get(key)
            if val is not None:
                try:
                    num = float(val)
                    return num if num <= 1.0 else num / 100.0
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _extract_raw_confidence(self, conf: dict | None) -> float:
        """Extract raw confidence from verifier_confidence dict."""
        if not conf:
            return 0.0
        for key in ("raw_model_confidence", "raw_confidence", "confidence"):
            val = conf.get(key)
            if val is not None:
                try:
                    num = float(val)
                    return num if num <= 1.0 else num / 100.0
                except (TypeError, ValueError):
                    pass
        return 0.0

    def _recommend(self, overall: float, risk_level: str, signals: RiskSignals) -> str:
        """Determine action recommendation based on risk score."""
        if risk_level == "critical":
            return "escalate"
        if risk_level == "high":
            return "escalate"
        if risk_level == "medium":
            if signals.contradiction_detected or not signals.sender_authorized:
                return "escalate"
            return "manual_review"
        # Low risk
        if not signals.sender_authorized:
            return "escalate"
        if signals.has_missing_fields and signals.missing_field_count > 1:
            return "clarify"
        return "auto_execute"
