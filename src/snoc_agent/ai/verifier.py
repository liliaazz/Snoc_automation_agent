"""Independent per-operation semantic verification service."""

from __future__ import annotations

import json
from typing import Any

from snoc_agent.ai.backend import (
    ChatMessage,
    GenerationConfig,
    LLMBackend,
    StructuredGenerationResult,
)
from snoc_agent.ai.prompts import VERIFIER_PROMPT_VERSION, load_prompt
from snoc_agent.ai.schemas import ProposedOperation, SemanticVerification


class SemanticVerifier:
    prompt_version = VERIFIER_PROMPT_VERSION

    def __init__(self, backend: LLMBackend, config: GenerationConfig) -> None:
        self.backend = backend
        self.config = config

    def verify(
        self,
        *,
        context_mode: str,
        latest_user_message: str,
        stored_operation_state: dict[str, Any],
        proposed_operation: ProposedOperation,
        candidate_evidence: list[dict[str, Any]],
        correlation_strength: str,
        subject: str = "",
    ) -> StructuredGenerationResult:
        payload = self.input_payload(
            context_mode=context_mode,
            latest_user_message=latest_user_message,
            stored_operation_state=stored_operation_state,
            proposed_operation=proposed_operation,
            candidate_evidence=candidate_evidence,
            correlation_strength=correlation_strength,
            subject=subject,
        )
        messages = [
            ChatMessage(role="system", content=load_prompt(self.prompt_version)),
            ChatMessage(
                role="user",
                content=f"<VERIFICATION_CONTEXT>{json.dumps(payload, ensure_ascii=False)}</VERIFICATION_CONTEXT>",
            ),
        ]
        result = self.backend.generate_structured(
            messages=messages, response_model=SemanticVerification, config=self.config
        )
        if not isinstance(result.parsed, SemanticVerification):
            raise TypeError("backend returned the wrong response model")
        return result

    @staticmethod
    def input_payload(
        *,
        context_mode: str,
        latest_user_message: str,
        stored_operation_state: dict[str, Any],
        proposed_operation: ProposedOperation,
        candidate_evidence: list[dict[str, Any]],
        correlation_strength: str,
        subject: str = "",
    ) -> dict[str, Any]:
        return {
            "context_mode": context_mode,
            "subject": subject,
            "latest_user_message": latest_user_message,
            "stored_operation_state": stored_operation_state,
            "proposed_operation": proposed_operation.model_dump(),
            "candidate_evidence": candidate_evidence,
            "correlation_strength": correlation_strength,
        }
