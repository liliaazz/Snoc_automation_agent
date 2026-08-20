"""Deterministic fail-closed checks around untrusted intent classification."""

from __future__ import annotations

import re
from dataclasses import dataclass

from snoc_agent.ai.preprocessing import normalize_unicode
from snoc_agent.ai.schemas import EmailAnalysis, ProposedOperation
from snoc_agent.domain.enums import OperationAction
from snoc_agent.domain.value_objects import canonical_action

_CLAUSE_SPLIT_RE = re.compile(r"(?:[\n.!?;]+|\bbut\b|\bmais\b)", re.IGNORECASE)
_NEGATION_RE = re.compile(
    r"\b(?:not|never|no|without|don['\u2019]?t|do\s+not|does\s+not|"
    r"doesn['\u2019]?t|is\s+not|isn['\u2019]?t|must\s+not|should\s+not|"
    r"pas|aucun(?:e)?|sans|non|ne|n['\u2019])\b",
    re.IGNORECASE,
)
_HISTORICAL_RE = re.compile(
    r"\b(?:yesterday|previously|already|last\s+(?:week|month)|historical|history|"
    r"hier|auparavant|pr[ée]c[ée]demment|d[ée]j[àa]|historique|ancien(?:ne)?)\b",
    re.IGNORECASE,
)
_UNSUPPORTED_RE = re.compile(
    r"\b(?:reporting|report|stock|inventory|marketing|meeting|human\s+resources|hr|"
    r"notification|rapport|signalement|r[ée]union|ressources?\s+humaines?)\b",
    re.IGNORECASE,
)
_PROMPT_INJECTION_RE = re.compile(
    r"(?:ignore|disregard|forget)\s+(?:all\s+)?(?:previous|prior|system)\s+"
    r"(?:rules|instructions|prompts)|"
    r"(?:authorize|whitelist)\s+me|"
    r"execute\s+(?:all|every)\s+(?:operation|request)",
    re.IGNORECASE,
)

_ACTION_PATTERNS: dict[OperationAction, re.Pattern[str]] = {
    OperationAction.OTP_NUMBER_CHANGE: re.compile(
        r"\botp\b|one[- ]time\s+password|"
        r"(?:change|update|modify|changer|modifier|mise\s+[àa]\s+jour)"
        r".{0,35}(?:phone|number|mobile|t[ée]l[ée]phone|num[ée]ro)",
        re.IGNORECASE,
    ),
    OperationAction.PASSWORD_RESET: re.compile(
        r"(?:reset|change|forgot|r[ée]initialis|changer).{0,30}"
        r"(?:password|mot\s+de\s+passe)|"
        r"(?:password|mot\s+de\s+passe).{0,30}(?:reset|change|r[ée]initialis)",
        re.IGNORECASE,
    ),
    OperationAction.VPN_ACCESS: re.compile(r"\bvpn\b", re.IGNORECASE),
    OperationAction.ACCOUNT_UNBLOCK: re.compile(
        r"(?:account|compte).{0,30}(?:unblock|unlock|locked|bloqu|d[ée]bloqu)|"
        r"(?:unblock|unlock|d[ée]bloqu).{0,30}(?:account|compte|pdv)|"
        r"\b(?:unblock|unlock|d[ée]bloquer)\b",
        re.IGNORECASE,
    ),
}


@dataclass(frozen=True, slots=True)
class IntentSafetyResult:
    analysis: EmailAnalysis
    blocked_operation_ids: tuple[str, ...]
    reasons: tuple[str, ...]


def _operation_reasons(text: str, proposal: ProposedOperation) -> tuple[str, ...]:
    action = canonical_action(proposal.action)
    pattern = _ACTION_PATTERNS.get(action)
    clauses = [clause.strip() for clause in _CLAUSE_SPLIT_RE.split(text) if clause.strip()]
    matching_clauses = [clause for clause in clauses if pattern and pattern.search(clause)]

    reasons: list[str] = []
    if _PROMPT_INJECTION_RE.search(text):
        reasons.append("prompt_injection_attempt")
    if any(_NEGATION_RE.search(clause) for clause in matching_clauses):
        reasons.append("negated_operation")
    if matching_clauses and all(_HISTORICAL_RE.search(clause) for clause in matching_clauses):
        reasons.append("historical_operation_only")
    if action == OperationAction.OTP_NUMBER_CHANGE and re.search(
        r"(?:do\s+not|don['\u2019]?t|ne|n['\u2019]).{0,25}"
        r"(?:change|modify|changer|modifier).{0,30}\+?\d",
        text,
        re.IGNORECASE,
    ):
        reasons.append("negated_operation")
    if _UNSUPPORTED_RE.search(text) and not matching_clauses:
        reasons.append("unsupported_business_intent")
    return tuple(dict.fromkeys(reasons))


def apply_intent_safety(analysis: EmailAnalysis, latest_user_message: str) -> IntentSafetyResult:
    """Remove proposals supported only by unsafe contexts.

    If every proposed operation is blocked, the normalized analysis is
    irrelevant. This happens before request/operation persistence, ensuring a
    model false positive cannot reach execution or inflate dashboard counts.
    """

    text = normalize_unicode(latest_user_message)
    blocked_ids: list[str] = []
    reasons: list[str] = []
    safe_operations: list[ProposedOperation] = []
    for proposal in analysis.operations:
        proposal_reasons = _operation_reasons(text, proposal)
        if proposal_reasons:
            blocked_ids.append(proposal.local_operation_id)
            reasons.extend(proposal_reasons)
        else:
            safe_operations.append(proposal)

    if not blocked_ids:
        return IntentSafetyResult(analysis, (), ())
    if safe_operations:
        normalized = analysis.model_copy(update={"operations": safe_operations})
    else:
        normalized = analysis.model_copy(
            update={
                "message_kind": "irrelevant",
                "operations": [],
                "new_request_present": False,
            }
        )
    return IntentSafetyResult(
        normalized,
        tuple(blocked_ids),
        tuple(dict.fromkeys(reasons)),
    )
