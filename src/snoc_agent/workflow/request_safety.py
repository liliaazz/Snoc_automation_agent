"""Deterministic request-wide safety checks for model-independent invariants."""

from __future__ import annotations

import re
from dataclasses import dataclass

from snoc_agent.ai.candidate_extractor import extract_numeric_candidates
from snoc_agent.ai.preprocessing import normalize_unicode
from snoc_agent.ai.schemas import EmailAnalysis
from snoc_agent.domain.enums import CorrelationStrength, OperationAction

_CLAUSE_RE = re.compile(r"(?:[\n.!?;]+|\bmais\b|\bbut\b)", re.IGNORECASE)
_NEGATION_RE = re.compile(
    r"\b(?:ne|n['\u2019]?|pas|non|sans|not|never|no|don['\u2019]?t|do\s+not)\b",
    re.IGNORECASE,
)
_UNCERTAINTY_RE = re.compile(
    r"\b(?:ou\s+peut[- ]être|peut[- ]être|pas\s+s[uû]r|"
    r"not\s+sure|perhaps|maybe|possibly|either)\b",
    re.IGNORECASE,
)
_CONDITIONAL_RE = re.compile(
    r"(?:\bsi\b|\bif\b|\b(?:ila|ida)\b|إذا|لو)",
    re.IGNORECASE,
)
_MODAL_OR_QUESTION_RE = re.compile(
    r"(?:\?|est[- ]ce|pourr(?:ait|iez|ons)|serait|"
    r"\b(?:could|would|might|may|can)\b|هل|واش|\bwach\b)",
    re.IGNORECASE,
)
_FORWARDED_RE = re.compile(
    r"(?im)^(?:-{2,}\s*(?:forwarded message|message transféré|message transfere)|"
    r"begin forwarded message:)"
)
_WORKFLOW_MARKER_RE = re.compile(
    r"\bSNOC-(?:REQ-[A-Z0-9_-]{4,}|COMPLETED\s*:)",
    re.IGNORECASE,
)
_INDEPENDENT_REQUEST_RE = re.compile(
    r"(?:\b(?:independent|separate|distinct)\s+request\b|"
    r"\b(?:nouvelle|new)\s+(?:demande|request)\s+(?:indépendante|independent)\b|"
    r"(?:طلب|تذكرة).{0,20}(?:مستقل|منفصل))",
    re.IGNORECASE,
)
_EXAMPLE_CONTEXT_RE = re.compile(
    r"\b(?:exemple|example|documentation|formation|training|sample|modèle|template)\b",
    re.IGNORECASE,
)
_NOT_A_REQUEST_RE = re.compile(
    r"(?:\bpas\s+une\s+demande\b|\bnot\s+(?:a\s+)?(?:real\s+)?request\b|"
    r"\bne\s+(?:pas\s+)?(?:exécutez|executez|traitez)\s+pas\b|"
    r"\bdo\s+not\s+(?:execute|process)\b)",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?:"
    r"\b(?:ignore|oublie[rz]?).{0,35}\b(?:règles?|regles?|instructions?|prompt)\b|"
    r"\btu\s+es\s+maintenant\s+(?:administrateur|admin)\b|"
    r"\byou\s+are\s+now\s+(?:an?\s+)?(?:administrator|admin)\b|"
    r"\b(?:réponds?|respond|return).{0,20}\bAUTO_EXECUTE\b|"
    r"\b(?:marque|mark).{0,35}\b(?:expéditeur|sender).{0,20}\bautor(?:isé|ized)\b|"
    r"\b(?:cache|hide).{0,30}\b(?:instruction|logs?)\b|"
    r"\b(?:system|developer)\s+(?:message|prompt|instruction)\b"
    r")",
    re.IGNORECASE,
)
_SIGNATURE_ROLE_RE = re.compile(
    r"(?im)^(?:équipe|equipe|team|service|department|département|departement)\b"
)
_ACTION_PATTERNS: dict[OperationAction, re.Pattern[str]] = {
    OperationAction.OTP_NUMBER_CHANGE: re.compile(
        r"\botp\b|one[- ]time\s+password|"
        r"(?:change|update|modify|changer|modifier).{0,35}"
        r"(?:phone|number|mobile|t[ée]l[ée]phone|num[ée]ro)",
        re.IGNORECASE,
    ),
    OperationAction.PASSWORD_RESET: re.compile(
        r"(?:reset|forgot|r[ée]initialis).{0,30}(?:password|mot\s+de\s+passe)|"
        r"(?:password|mot\s+de\s+passe).{0,30}(?:reset|r[ée]initialis)",
        re.IGNORECASE,
    ),
    OperationAction.VPN_ACCESS: re.compile(r"\bvpn\b", re.IGNORECASE),
    OperationAction.ACCOUNT_UNBLOCK: re.compile(
        r"(?:account|compte).{0,30}(?:unblock|unlock|locked|bloqu|d[ée]bloqu)|"
        r"(?:unblock|unlock|d[ée]bloqu).{0,30}(?:account|compte|pdv)|"
        r"\b(?:unblock|unlock|d[ée]bloquer|déblocage)\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True, slots=True)
class RequestSafetyAssessment:
    reasons: tuple[str, ...]
    policy_overrides: tuple[str, ...] = ()

    @property
    def automatic_execution_allowed(self) -> bool:
        return not self.reasons


def _positive_actions(text: str) -> frozenset[OperationAction]:
    actions: set[OperationAction] = set()
    for clause in _CLAUSE_RE.split(text):
        if _NEGATION_RE.search(clause):
            continue
        for action, pattern in _ACTION_PATTERNS.items():
            if pattern.search(clause):
                actions.add(action)
    return frozenset(actions)


def _numeric_values(text: str) -> tuple[set[str], set[str]]:
    pdvs: set[str] = set()
    phones: set[str] = set()
    for candidate in extract_numeric_candidates(text):
        digits = candidate.value.removeprefix("+")
        if len(digits) == 8:
            pdvs.add(candidate.value)
        elif 9 <= len(digits) <= 15:
            phones.add(candidate.value)
    return pdvs, phones


def _mapping_is_ambiguous(text: str) -> bool:
    pdvs, phones = _numeric_values(text)
    if len(pdvs) > 1 and _UNCERTAINTY_RE.search(text):
        return True
    if len(pdvs) < 2 and len(phones) < 2:
        return False

    logical_lines = [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]
    line_values = [_numeric_values(line) for line in logical_lines]
    if any(len(line_pdvs) > 1 or len(line_phones) > 1 for line_pdvs, line_phones in line_values):
        return True
    if len(pdvs) > 1 and len(phones) > 1:
        explicitly_paired = {
            (next(iter(line_pdvs)), next(iter(line_phones)))
            for line_pdvs, line_phones in line_values
            if len(line_pdvs) == len(line_phones) == 1
        }
        return len(explicitly_paired) != len(phones)
    return False


def _analysis_ambiguity_is_fully_scoped(analysis: EmailAnalysis) -> bool:
    if not analysis.unresolved_ambiguities:
        return False
    operation_ids = {operation.local_operation_id.casefold() for operation in analysis.operations}
    return all(
        any(operation_id in reason.casefold() for operation_id in operation_ids)
        for reason in analysis.unresolved_ambiguities
    )


def assess_request_safety(
    *,
    subject: str,
    latest_user_message: str,
    full_visible_body: str,
    correlation_strength: CorrelationStrength,
    analysis: EmailAnalysis,
    parsing_warnings: tuple[str, ...] = (),
    allow_subject_body_conflict_auto_execution: bool = False,
    allow_forwarded_content_auto_execution: bool = False,
    allow_untrusted_workflow_marker_auto_execution: bool = False,
) -> RequestSafetyAssessment:
    """Return stable blockers that no analyzer/verifier agreement can override."""

    subject = normalize_unicode(subject)
    latest = normalize_unicode(latest_user_message)
    full_body = normalize_unicode(full_visible_body)
    reasons: list[str] = []
    policy_overrides: list[str] = []

    subject_pdvs, subject_phones = _numeric_values(subject)
    body_pdvs, body_phones = _numeric_values(latest)
    explicit_independent_follow_up = (
        correlation_strength == CorrelationStrength.STRONG
        and analysis.new_request_present
        and _INDEPENDENT_REQUEST_RE.search(latest) is not None
    )
    compare_current_subject = correlation_strength == CorrelationStrength.NEW
    if compare_current_subject and not explicit_independent_follow_up:
        if subject_pdvs and body_pdvs and subject_pdvs.isdisjoint(body_pdvs):
            reasons.append("subject_body_pdv_conflict")
        if subject_phones and body_phones and subject_phones.isdisjoint(body_phones):
            reasons.append("subject_body_phone_conflict")

    subject_actions = _positive_actions(subject)
    body_actions = _positive_actions(latest)
    subject_action_conflict = (
        compare_current_subject
        and not explicit_independent_follow_up
        and subject_actions
        and body_actions
        and subject_actions.isdisjoint(body_actions)
    )
    subject_action_override_applied = bool(
        subject_action_conflict
        and allow_subject_body_conflict_auto_execution
        and _NEGATION_RE.search(latest)
    )
    if subject_action_conflict:
        if subject_action_override_applied:
            policy_overrides.append("subject_body_conflict_explicit_body_override")
        else:
            reasons.append("subject_body_action_conflict")

    pdvs, phones = _numeric_values(latest)
    proposed_pdvs = {
        operation.pdv_code for operation in analysis.operations if operation.pdv_code is not None
    }
    proposed_phones = {
        operation.phone for operation in analysis.operations if operation.phone is not None
    }
    if correlation_strength == CorrelationStrength.STRONG:
        if body_pdvs and proposed_pdvs and body_pdvs.isdisjoint(proposed_pdvs):
            reasons.extend(
                (
                    "correlated_reply_pdv_conflict",
                    "correction_identifier_conflict",
                )
            )
        if body_phones and proposed_phones and body_phones.isdisjoint(proposed_phones):
            reasons.extend(
                (
                    "correlated_reply_phone_conflict",
                    "correction_identifier_conflict",
                )
            )
    globally_ambiguous_pairing = len(pdvs) == len(phones) and len(pdvs) > 1
    uncertainty_is_global = len(pdvs) > 1 and _UNCERTAINTY_RE.search(latest) is not None
    if _mapping_is_ambiguous(latest) and (
        globally_ambiguous_pairing
        or uncertainty_is_global
        or not _analysis_ambiguity_is_fully_scoped(analysis)
    ):
        if len(pdvs) > 1:
            reasons.append("multiple_pdv_candidates")
        if len(phones) > 1:
            reasons.append("multiple_phone_candidates")
        reasons.append("ambiguous_identifier_attribution")
        if globally_ambiguous_pairing:
            reasons.append("positional_pairing_not_explicit")
        reasons.append("request_wide_identifier_mapping_ambiguous")
    if _CONDITIONAL_RE.search(latest) and _MODAL_OR_QUESTION_RE.search(latest):
        reasons.append("hypothetical_or_conditional_request")
    if _EXAMPLE_CONTEXT_RE.search(latest) and _NOT_A_REQUEST_RE.search(latest):
        reasons.append("reporting_or_example_context")
    signature_role = _SIGNATURE_ROLE_RE.search(latest)
    if (
        signature_role is not None
        and (pdvs or phones)
        and len(_positive_actions(latest[signature_role.start() :])) > 1
    ):
        reasons.append("signature_like_operational_text")
    if _PROMPT_INJECTION_RE.search(latest):
        reasons.append("prompt_injection_pattern")
    forwarded_content_detected = (
        _FORWARDED_RE.search(full_body) is not None
        or "forwarded_content_detected" in parsing_warnings
    )
    forwarded_override_applied = bool(
        allow_forwarded_content_auto_execution
        and forwarded_content_detected
        and analysis.direct_current_instruction is True
    )
    if (
        not explicit_independent_follow_up
        and forwarded_content_detected
        and not forwarded_override_applied
    ):
        reasons.extend(
            (
                "forwarded_content_requires_review",
                "forwarded_third_party_content",
            )
        )
    elif forwarded_override_applied:
        policy_overrides.append("forwarded_third_party_instruction_explicitly_adopted")
    untrusted_marker = correlation_strength in {
        CorrelationStrength.NEW,
        CorrelationStrength.WEAK,
    } and (
        _WORKFLOW_MARKER_RE.search(subject) is not None
        or _WORKFLOW_MARKER_RE.search(full_body) is not None
    )
    if untrusted_marker and not allow_untrusted_workflow_marker_auto_execution:
        reasons.append("untrusted_workflow_marker")
        marker_text = f"{subject}\n{full_body}"
        if re.search(r"\bSNOC-REQ-", marker_text, re.IGNORECASE):
            reasons.append("unknown_request_reference")
        if re.search(r"\bSNOC-COMPLETED\s*:", marker_text, re.IGNORECASE):
            reasons.append("forged_completion_marker")
    elif untrusted_marker:
        policy_overrides.append("untrusted_workflow_marker_ignored_for_fresh_request")
    if subject_actions and (subject_pdvs or subject_phones) and not latest.strip():
        reasons.append("subject_only_operational_evidence")

    if analysis.hypothetical_or_conditional:
        reasons.extend(
            (
                "hypothetical_or_conditional_request",
                "analyzer_hypothetical_or_conditional",
            )
        )
    if analysis.direct_current_instruction is False:
        reasons.extend(
            (
                "no_direct_current_instruction",
                "analyzer_no_direct_current_instruction",
            )
        )
    if analysis.subject_body_conflict and not subject_action_override_applied:
        reasons.append("analyzer_subject_body_conflict")
    if analysis.candidate_mapping_explicit is False:
        # A model flag must not turn a deterministically simple request into a
        # false escalation. With at most one PDV and one phone candidate there
        # is no same-type candidate choice to attribute by position. Exact
        # field evidence and the per-operation verifier still have to pass.
        # Keep the model blocker for every multi-candidate request because
        # wording and cross-operation attribution can still be ambiguous.
        if len(pdvs) <= 1 and len(phones) <= 1:
            policy_overrides.append("single_identifier_mapping_deterministically_unambiguous")
        else:
            reasons.append("analyzer_identifier_mapping_not_explicit")
    if analysis.forwarded_content and not forwarded_override_applied:
        reasons.append("forwarded_content_requires_review")
        reasons.append("analyzer_forwarded_content")
    if analysis.message_kind in {
        "cancellation",
        "hypothetical_or_question",
        "reporting_or_example",
    } or (analysis.message_kind == "forwarded_request" and not forwarded_override_applied):
        if analysis.message_kind == "reporting_or_example":
            reasons.append("reporting_or_example_context")
        if analysis.message_kind == "cancellation":
            reasons.append("negated_instruction")
        reasons.append(f"non_executable_message_kind:{analysis.message_kind}")

    return RequestSafetyAssessment(
        tuple(dict.fromkeys(reasons)),
        tuple(dict.fromkeys(policy_overrides)),
    )
