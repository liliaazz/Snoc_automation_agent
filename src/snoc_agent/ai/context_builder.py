"""Build bounded, labelled model context instead of flattening whole threads."""

from __future__ import annotations

import copy
import json
import re
from typing import Any

from snoc_agent.ai.candidate_extractor import NumericCandidate, extract_numeric_candidates
from snoc_agent.ai.preprocessing import clean_high_confidence_artifacts
from snoc_agent.domain.entities import OperationSnapshot
from snoc_agent.mail.parser import ParsedEmail

MIN_CONTEXT_CHARACTERS = 256
TRUNCATION_MARKER = "\n[...]\n"
PROTECTED_TOKEN_RE = re.compile(
    r"\+?\d(?:[ \t()./\-]*\d){7,14}|[\w@.+:/\-]+",
    re.UNICODE,
)


class ContextBuilder:
    def __init__(
        self,
        *,
        max_context_characters: int = 24_000,
        max_latest_characters: int = 12_000,
        max_relevant_thread_characters: int = 4_000,
    ) -> None:
        if max_context_characters < MIN_CONTEXT_CHARACTERS:
            raise ValueError(f"max_context_characters must be at least {MIN_CONTEXT_CHARACTERS}")
        if max_latest_characters < 1 or max_relevant_thread_characters < 1:
            raise ValueError("text context limits must be at least one character")
        self.max_context_characters = max_context_characters
        self.max_latest_characters = max_latest_characters
        self.max_relevant_thread_characters = max_relevant_thread_characters

    @staticmethod
    def _bounded_text(value: str, limit: int) -> tuple[str, bool]:
        if len(value) <= limit:
            return value, False
        if limit <= 0:
            return "", True
        if limit < len(TRUNCATION_MARKER):
            return "…"[:limit], True
        available = limit - len(TRUNCATION_MARKER)
        head = available // 2
        tail = available - head

        # Moving a cut to the edge of a protected span avoids emitting a
        # partial PDV, phone number, address-like token, or ordinary word. A
        # very long indivisible token is omitted instead of being sliced.
        for match in PROTECTED_TOKEN_RE.finditer(value):
            if match.start() < head < match.end():
                head = match.start()
                break
        tail_start = len(value) - tail
        for match in PROTECTED_TOKEN_RE.finditer(value):
            if match.start() < tail_start < match.end():
                tail_start = match.end()
                break
        prefix = value[:head].rstrip()
        suffix = value[tail_start:].lstrip()
        bounded = prefix + TRUNCATION_MARKER + suffix
        if len(bounded) > limit:  # pragma: no cover - arithmetic safety invariant
            raise RuntimeError("bounded text exceeded its requested character limit")
        return bounded, True

    @staticmethod
    def _serialized_length(context: dict[str, Any]) -> int:
        return len(json.dumps(context, ensure_ascii=False, sort_keys=True))

    @staticmethod
    def _candidate_keys(context: dict[str, Any]) -> tuple[str, ...]:
        return tuple(
            key
            for key in ("numeric_candidates", "numeric_candidates_from_latest_reply")
            if isinstance(context.get(key), list)
        )

    @staticmethod
    def _apply_warnings(context: dict[str, Any], warnings: list[str]) -> None:
        if not warnings:
            return
        context["context_limit_warnings"] = list(
            dict.fromkeys([*warnings, "context_truncated", "safety_context_incomplete"])
        )
        context["automatic_execution_allowed"] = False

    def _fits(self, context: dict[str, Any]) -> bool:
        return self._serialized_length(context) <= self.max_context_characters

    def _shrink_top_level_text(self, context: dict[str, Any], key: str) -> bool:
        value = context.get(key)
        if not isinstance(value, str) or not value:
            return self._fits(context)
        original = value
        context[key] = ""
        if not self._fits(context):
            return False

        low = 0
        high = len(original)
        best = ""
        while low <= high:
            candidate_limit = (low + high) // 2
            candidate, _truncated = self._bounded_text(original, candidate_limit)
            context[key] = candidate
            if self._fits(context):
                best = candidate
                low = candidate_limit + 1
            else:
                high = candidate_limit - 1
        context[key] = best
        return True

    def bound_latest_text(self, value: str) -> tuple[str, list[str]]:
        bounded, truncated = self._bounded_text(value, self.max_latest_characters)
        return bounded, ["latest_message_character_limit_exceeded"] if truncated else []

    def _latest(self, value: str) -> tuple[str, list[str]]:
        return self.bound_latest_text(value)

    def finalize_context(
        self, context: dict[str, Any], warnings: list[str] | None = None
    ) -> dict[str, Any]:
        """Return a fail-closed context whose serialized JSON never exceeds the cap.

        Free text is shortened only at safe token boundaries. Structured identifiers
        and state entries are either retained whole or omitted as whole entries; they
        are never substring-truncated.
        """

        result = copy.deepcopy(context)
        audit_warnings = list(warnings or [])
        self._apply_warnings(result, audit_warnings)
        if self._fits(result):
            return result

        audit_warnings.extend(
            ["model_context_character_limit_exceeded", "model_context_sections_omitted"]
        )
        self._apply_warnings(result, audit_warnings)
        result["context_sections_omitted_due_to_limit"] = True

        # Remove duplicated or low-trust prose before reducing current evidence.
        for key in (
            "relevant_thread_context",
            "closed_history_summary",
            "preprocessing_notes",
            "text_since_last_closed_request",
        ):
            result.pop(key, None)
            if self._fits(result):
                return result

        # Candidate snippets are explanatory prose. Candidate values, raw values,
        # offsets, and kinds remain intact.
        for key in self._candidate_keys(result):
            candidates = result[key]
            for candidate in candidates:
                if isinstance(candidate, dict) and candidate.get("context"):
                    candidate["context"] = ""
            if self._fits(result):
                return result

        # Prefer current-message evidence and structured request state over prose.
        for key in ("subject", "previous_agent_question", "latest_user_message"):
            if self._shrink_top_level_text(result, key):
                return result

        # If structured collections alone exceed the cap, remove complete entries
        # from the least critical end. The warning disables automatic execution.
        for key in (
            *self._candidate_keys(result),
            "possible_open_requests",
            "target_operations",
            "stored_operations",
        ):
            entries = result.get(key)
            if not isinstance(entries, list):
                continue
            while entries and not self._fits(result):
                entries.pop()
            if not entries:
                result.pop(key, None)
            if self._fits(result):
                return result

        # A bounded minimal envelope is preferable to leaking an over-limit prompt.
        # Whole request references and candidate records are added only when they fit.
        mode = result.get("mode", "bounded_context")
        if not isinstance(mode, str) or len(mode) > 64:
            mode = "bounded_context"
        essential_warnings = [
            "model_context_character_limit_exceeded",
            "model_context_sections_omitted",
        ]
        minimal: dict[str, Any] = {
            "mode": mode,
            "latest_user_message": "",
            "automatic_execution_allowed": False,
            "context_sections_omitted_due_to_limit": True,
            "context_limit_warnings": essential_warnings,
        }
        if not self._fits(minimal):
            minimal["mode"] = "bounded_context"
        for warning in dict.fromkeys(audit_warnings):
            if warning in essential_warnings:
                continue
            candidate = copy.deepcopy(minimal)
            candidate["context_limit_warnings"] = [
                *minimal["context_limit_warnings"],
                warning,
            ]
            if self._fits(candidate):
                minimal = candidate
        for key in ("request_reference", "request_status"):
            value = context.get(key)
            if value is None:
                continue
            candidate = copy.deepcopy(minimal)
            candidate[key] = value
            if self._fits(candidate):
                minimal = candidate
        for key in self._candidate_keys(context):
            retained: list[Any] = []
            for entry in context[key]:
                compact_entry = copy.deepcopy(entry)
                if isinstance(compact_entry, dict):
                    compact_entry["context"] = ""
                candidate = copy.deepcopy(minimal)
                candidate[key] = [*retained, compact_entry]
                if not self._fits(candidate):
                    break
                retained.append(compact_entry)
                minimal = candidate
        original_latest = str(context.get("latest_user_message", ""))
        self._shrink_top_level_text(minimal, "latest_user_message")
        if original_latest:
            minimal["latest_user_message"] = original_latest
            self._shrink_top_level_text(minimal, "latest_user_message")
        if not self._fits(minimal):
            raise RuntimeError("failed to construct a bounded model context")
        return minimal

    def _finalize(self, context: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
        return self.finalize_context(context, warnings)

    def _uncertain_thread_context(self, parsed: ParsedEmail) -> tuple[dict[str, Any], list[str]]:
        quoted = parsed.segmentation.quoted_thread_candidate.strip()
        if parsed.segmentation.segmentation_confidence >= 0.8 or not quoted:
            return {}, []
        bounded, truncated = self._bounded_text(quoted, self.max_relevant_thread_characters)
        return {
            "relevant_thread_context": {
                "section": "relevant_thread_context",
                "trust": "untrusted_segmentation_candidate",
                "text": bounded,
            }
        }, ["relevant_thread_character_limit_exceeded"] if truncated else []

    def new_request(self, parsed: ParsedEmail) -> dict[str, Any]:
        latest, cleanup = clean_high_confidence_artifacts(
            parsed.segmentation.latest_message_candidate
        )
        candidates = extract_numeric_candidates(latest)
        latest, warnings = self._latest(latest)
        thread, thread_warnings = self._uncertain_thread_context(parsed)
        return self._finalize(
            {
                "mode": "new_request",
                "subject": parsed.subject,
                "latest_user_message": latest,
                "text_since_last_closed_request": latest,
                "numeric_candidates": [candidate.model_dump() for candidate in candidates],
                "closed_history_summary": None,
                "preprocessing_notes": cleanup,
                "segmentation_confidence": parsed.segmentation.segmentation_confidence,
                **thread,
            },
            [*warnings, *thread_warnings],
        )

    def clarification_reply(
        self,
        parsed: ParsedEmail,
        *,
        request_reference: str,
        previous_agent_question: str,
        operations: list[OperationSnapshot],
    ) -> dict[str, Any]:
        latest, cleanup = clean_high_confidence_artifacts(
            parsed.segmentation.latest_message_candidate
        )
        candidates = extract_numeric_candidates(latest)
        latest, warnings = self._latest(latest)
        thread, thread_warnings = self._uncertain_thread_context(parsed)
        return self._finalize(
            {
                "mode": "clarification_reply",
                "request_reference": request_reference,
                "latest_user_message": latest,
                "previous_agent_question": previous_agent_question,
                "target_operations": [
                    {
                        "operation_id": operation.operation_id,
                        "action": operation.action.value,
                        "known_fields": {
                            "pdv_code": operation.pdv_code,
                            "phone": operation.phone,
                            **operation.additional_payload,
                        },
                        "missing_fields": operation.missing_fields,
                    }
                    for operation in operations
                ],
                "numeric_candidates_from_latest_reply": [
                    candidate.model_dump() for candidate in candidates
                ],
                "preprocessing_notes": cleanup,
                **thread,
            },
            [*warnings, *thread_warnings],
        )

    def possible_follow_up(
        self,
        parsed: ParsedEmail,
        possible_open_requests: list[dict[str, Any]],
    ) -> dict[str, Any]:
        latest, cleanup = clean_high_confidence_artifacts(
            parsed.segmentation.latest_message_candidate
        )
        candidates = extract_numeric_candidates(latest)
        latest, warnings = self._latest(latest)
        thread, thread_warnings = self._uncertain_thread_context(parsed)
        return self._finalize(
            {
                "mode": "possible_follow_up",
                "latest_user_message": latest,
                "possible_open_requests": possible_open_requests,
                "correlation_strength": "weak",
                "automatic_execution_allowed": False,
                "numeric_candidates": [candidate.model_dump() for candidate in candidates],
                "preprocessing_notes": cleanup,
                **thread,
            },
            [*warnings, *thread_warnings],
        )

    def correlated_request_reply(
        self,
        parsed: ParsedEmail,
        *,
        request_reference: str,
        request_status: str,
        operations: list[OperationSnapshot],
    ) -> dict[str, Any]:
        """Strong header correlation outside a clarification, including late corrections."""

        latest, cleanup = clean_high_confidence_artifacts(
            parsed.segmentation.latest_message_candidate
        )
        candidates = extract_numeric_candidates(latest)
        latest, warnings = self._latest(latest)
        thread, thread_warnings = self._uncertain_thread_context(parsed)
        return self._finalize(
            {
                "mode": "correlated_request_reply",
                "subject": parsed.subject,
                "request_reference": request_reference,
                "request_status": request_status,
                "latest_user_message": latest,
                "closed_history_summary": None,
                "stored_operations": [
                    {
                        "operation_id": operation.operation_id,
                        "action": operation.action.value,
                        "known_fields": {
                            "pdv_code": operation.pdv_code,
                            "phone": operation.phone,
                            **operation.additional_payload,
                        },
                        "missing_fields": operation.missing_fields,
                    }
                    for operation in operations
                ],
                "numeric_candidates": [candidate.model_dump() for candidate in candidates],
                "preprocessing_notes": cleanup,
                **thread,
            },
            [*warnings, *thread_warnings],
        )

    @staticmethod
    def current_candidates(context: dict[str, Any]) -> list[NumericCandidate]:
        raw = context.get("numeric_candidates") or context.get(
            "numeric_candidates_from_latest_reply", []
        )
        return [NumericCandidate.model_validate(item) for item in raw]
