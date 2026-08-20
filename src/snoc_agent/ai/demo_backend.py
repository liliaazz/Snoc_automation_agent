"""Deterministic offline demonstration backend.

This backend is deliberately not a production semantic authority. It exists so replay, state,
outbox, and idempotency behavior can be demonstrated without model weights or credentials.
Production deployments select ``OpenAICompatibleBackend``.
"""

from __future__ import annotations

import json
import re
from typing import Any, TypeVar

from pydantic import BaseModel

from snoc_agent.ai.backend import ChatMessage, GenerationConfig, StructuredGenerationResult
from snoc_agent.ai.candidate_extractor import extract_numeric_candidates
from snoc_agent.ai.schemas import EmailAnalysis, SemanticVerification

ResponseT = TypeVar("ResponseT", bound=BaseModel)

ACTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "otp_number_change",
        re.compile(
            r"\b(?:otp|token|sms)\b.*\b(?:chang|modif|remplac|nouveau)|"
            r"\b(?:chang|modif|remplac|nouveau).*\b(?:otp|token|sms)\b",
            re.I,
        ),
    ),
    (
        "password_reset",
        re.compile(
            r"\b(?:r[ée]initial|reset).*(?:mot de passe|password|mdp)|(?:mot de passe|password|mdp).*\b(?:r[ée]initial|reset)",
            re.I,
        ),
    ),
    ("account_unblock", re.compile(r"\b(?:d[ée]bloqu|unblock|locked|bloqu[ée])", re.I)),
    (
        "vpn_access",
        re.compile(
            r"\b(?:vpn|snoc|web)\b.*\b(?:acc[èe]s|cr[ée]|ouvr|activ)|\b(?:acc[èe]s|cr[ée]|ouvr|activ).*\b(?:vpn|snoc|web)",
            re.I,
        ),
    ),
]


def _context(messages: list[ChatMessage]) -> dict[str, Any]:
    content = messages[-1].content
    match = re.search(
        r"<(?:APPLICATION|VERIFICATION)_CONTEXT>(.*)</(?:APPLICATION|VERIFICATION)_CONTEXT>",
        content,
        re.S,
    )
    if not match:
        raise ValueError("demo backend could not find labelled context")
    return json.loads(match.group(1))


def _digits(text: str) -> list[str]:
    return [candidate.value for candidate in extract_numeric_candidates(text)]


def _evidence(field: str, value: str | None, source: str, text: str) -> dict[str, Any]:
    return {
        "field_name": field,
        "value": value,
        "source": source,
        "evidence_text": text[:180] if value else None,
        "support": "supported" if value else "unclear",
    }


def _operation(action: str, text: str, index: int) -> dict[str, Any]:
    numbers = _digits(text)
    pdv = next(
        (value for value in numbers if len(value.lstrip("+")) == 8 and not value.startswith("+")),
        None,
    )
    phone = next((value for value in numbers if 9 <= len(value.lstrip("+")) <= 15), None)
    required = ["pdv_code"]
    if action == "vpn_access":
        required.append("phone")
    elif action == "otp_number_change":
        required.append("new_phone")
    missing = [
        field
        for field in required
        if (field == "pdv_code" and not pdv) or (field in {"phone", "new_phone"} and not phone)
    ]
    evidence = [_evidence("pdv_code", pdv, "latest_user_message", text)]
    if action in {"vpn_access", "otp_number_change"}:
        evidence.append(_evidence("phone", phone, "latest_user_message", text))
    return {
        "local_operation_id": f"OP-{index:02d}",
        "action": action,
        "pdv_code": pdv,
        "phone": phone,
        "additional_fields": {},
        "missing_fields": missing,
        "evidence": evidence,
        "ambiguity_reasons": [],
        "raw_action_confidence": None,
        "raw_field_confidence": {},
    }


def _analyze(context: dict[str, Any]) -> EmailAnalysis:
    mode = str(context.get("mode", "new_request"))
    latest = str(context.get("latest_user_message", ""))
    if mode == "clarification_reply":
        candidates = context.get("numeric_candidates_from_latest_reply", [])
        operations: list[dict[str, Any]] = []
        referenced: list[str] = []
        for target in context.get("target_operations", []):
            known = dict(target.get("known_fields", {}))
            missing = list(target.get("missing_fields", []))
            pdv = known.get("pdv_code")
            phone = known.get("phone") or known.get("new_phone")
            for candidate in candidates:
                if "pdv_code" in missing and candidate.get("kind_hint") == "pdv_or_unknown":
                    pdv = candidate.get("value")
                if {"phone", "new_phone"}.intersection(missing) and candidate.get(
                    "kind_hint"
                ) == "phone_or_unknown":
                    phone = candidate.get("value")
            action = str(target["action"])
            still_missing: list[str] = []
            for field in missing:
                if (field == "pdv_code" and not pdv) or (
                    field in {"phone", "new_phone"} and not phone
                ):
                    still_missing.append(field)
            operation_id = str(target["operation_id"])
            referenced.append(operation_id)
            operations.append(
                {
                    "local_operation_id": operation_id,
                    "action": action,
                    "pdv_code": pdv,
                    "phone": phone,
                    "additional_fields": {},
                    "missing_fields": still_missing,
                    "evidence": [
                        _evidence("pdv_code", pdv, "stored_request_state", latest),
                        _evidence(
                            "phone",
                            phone,
                            "latest_user_message"
                            if phone != known.get("phone")
                            else "stored_request_state",
                            latest,
                        ),
                    ],
                    "ambiguity_reasons": [],
                    "raw_action_confidence": None,
                    "raw_field_confidence": {},
                }
            )
        new_request_text = ""
        if match := re.search(
            r"\b(?:par ailleurs|nouvelle demande|autre demande)\b.*", latest, re.I | re.S
        ):
            new_request_text = match.group(0)
        if new_request_text:
            for action, pattern in ACTION_PATTERNS:
                if pattern.search(new_request_text):
                    operations.append(_operation(action, new_request_text, len(operations) + 1))
        return EmailAnalysis.model_validate(
            {
                "message_kind": "mixed" if new_request_text else "clarification_reply",
                "referenced_existing_operation_ids": referenced,
                "operations": operations,
                "new_request_present": bool(new_request_text),
                "contradiction_with_stored_state": False,
                "contradiction_details": [],
                "unresolved_ambiguities": [],
            }
        )

    if mode == "correlated_request_reply" and re.search(
        r"\b(?:correction|corrig|rectific|erron)", latest, re.I
    ):
        numbers = _digits(latest)
        current_phone = next(
            (value for value in numbers if 9 <= len(value.lstrip("+")) <= 15), None
        )
        operations = []
        for target in context.get("stored_operations", []):
            action = str(target["action"])
            known = dict(target.get("known_fields", {}))
            phone = (
                current_phone
                if action in {"otp_number_change", "vpn_access"}
                else known.get("phone")
            )
            operations.append(
                {
                    "local_operation_id": str(target["operation_id"]),
                    "action": action,
                    "pdv_code": known.get("pdv_code"),
                    "phone": phone,
                    "additional_fields": {},
                    "missing_fields": [],
                    "evidence": [
                        _evidence(
                            "pdv_code",
                            known.get("pdv_code"),
                            "stored_request_state",
                            latest,
                        ),
                        _evidence("phone", phone, "latest_user_message", latest),
                    ],
                    "ambiguity_reasons": [],
                    "raw_action_confidence": None,
                    "raw_field_confidence": {},
                }
            )
        return EmailAnalysis.model_validate(
            {
                "message_kind": "correction",
                "referenced_existing_operation_ids": [
                    str(target["operation_id"]) for target in context.get("stored_operations", [])
                ],
                "operations": operations,
                "new_request_present": False,
                "contradiction_with_stored_state": bool(operations),
                "contradiction_details": ["latest message corrects stored operation fields"],
                "unresolved_ambiguities": [],
            }
        )

    operations = []
    segments = [segment.strip() for segment in re.split(r"[\n;]+", latest) if segment.strip()]
    for segment in segments:
        for action, pattern in ACTION_PATTERNS:
            if pattern.search(segment):
                operations.append(_operation(action, segment, len(operations) + 1))
                break
    if not operations:
        # A multiline value may be separated from its action; use the complete latest message.
        for action, pattern in ACTION_PATTERNS:
            if pattern.search(latest):
                operations.append(_operation(action, latest, len(operations) + 1))
    elif len(operations) == 1:
        # Keep section boundaries but allow fields to be supplied on the following line.
        operations = [_operation(str(operations[0]["action"]), latest, 1)]
    if not operations:
        return EmailAnalysis(
            message_kind="irrelevant",
            referenced_existing_operation_ids=[],
            operations=[],
            new_request_present=False,
            contradiction_with_stored_state=False,
            contradiction_details=[],
            unresolved_ambiguities=[],
        )
    kind = (
        "correction"
        if re.search(r"\b(?:correction|corrig|rectific)", latest, re.I)
        else "new_request"
    )
    return EmailAnalysis.model_validate(
        {
            "message_kind": kind,
            "referenced_existing_operation_ids": [],
            "operations": operations,
            "new_request_present": kind == "new_request",
            "contradiction_with_stored_state": False,
            "contradiction_details": [],
            "unresolved_ambiguities": [],
        }
    )


def _verify(context: dict[str, Any]) -> SemanticVerification:
    proposal = context["proposed_operation"]
    action = proposal["action"]
    pdv = proposal.get("pdv_code")
    phone = proposal.get("phone")
    return SemanticVerification(
        action_supported="yes" if action != "unknown" else "unclear",
        pdv_supported="yes" if pdv else "unclear",
        phone_supported=(
            "yes"
            if phone
            else "unclear"
            if action in {"vpn_access", "otp_number_change"}
            else "not_required"
        ),
        stored_state_compatible="yes",
        contradiction_present=False,
        contradiction_type=None,
        missing_fields=list(proposal.get("missing_fields", [])),
        additional_fields_supported={key: "yes" for key in proposal.get("additional_fields", {})},
        correction_detected=False,
        new_request_detected=False,
        evidence_summary=["offline demo verifier mirrors explicit structured evidence"],
        raw_confidence=None,
    )


class DemoLLMBackend:
    def generate_structured(
        self,
        *,
        messages: list[ChatMessage],
        response_model: type[ResponseT],
        config: GenerationConfig,
    ) -> StructuredGenerationResult:
        context = _context(messages)
        parsed: BaseModel
        if response_model is EmailAnalysis:
            parsed = _analyze(context)
        elif response_model is SemanticVerification:
            parsed = _verify(context)
        else:
            raise TypeError(f"demo backend does not support {response_model.__name__}")
        return StructuredGenerationResult(
            parsed=parsed,
            raw_output=parsed.model_dump_json(),
            model_name=f"demo:{config.model}",
            backend="deterministic_demo",
            latency_seconds=0.0,
            base_model_id=config.base_model or config.model,
            resolved_model_id=config.model,
            requested_route=config.model,
            structured_output_mode="json_schema",
            json_schema=response_model.model_json_schema(),
            schema_name=response_model.__name__,
        )
