"""LangChain Runnable adapters that preserve the audited model services."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableLambda

from snoc_agent.ai.analyzer import EmailAnalyzer
from snoc_agent.ai.backend import StructuredGenerationResult
from snoc_agent.ai.schemas import ProposedOperation
from snoc_agent.ai.verifier import SemanticVerifier


class LangChainEmailAnalyzer:
    """Run the existing analyzer through the LangChain Runnable interface."""

    def __init__(self, analyzer: EmailAnalyzer) -> None:
        self._analyzer = analyzer
        self.backend = analyzer.backend
        self.config = analyzer.config
        self.prompt_version = analyzer.prompt_version
        self.runnable = RunnableLambda(analyzer.analyze).with_config(
            {"run_name": "snoc_email_analyzer"}
        )

    def analyze(self, context: dict[str, Any]) -> StructuredGenerationResult:
        return self.runnable.invoke(context)


class LangChainSemanticVerifier:
    """Run the existing semantic verifier through a typed Runnable boundary."""

    def __init__(self, verifier: SemanticVerifier) -> None:
        self._verifier = verifier
        self.backend = verifier.backend
        self.config = verifier.config
        self.prompt_version = verifier.prompt_version
        self.runnable = RunnableLambda(self._invoke).with_config(
            {"run_name": "snoc_semantic_verifier"}
        )

    def _invoke(self, value: dict[str, Any]) -> StructuredGenerationResult:
        return self._verifier.verify(
            context_mode=str(value["context_mode"]),
            latest_user_message=str(value["latest_user_message"]),
            stored_operation_state=dict(value["stored_operation_state"]),
            proposed_operation=value["proposed_operation"],
            candidate_evidence=list(value["candidate_evidence"]),
            correlation_strength=str(value["correlation_strength"]),
            subject=str(value.get("subject", "")),
        )

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
        return self.runnable.invoke(
            {
                "context_mode": context_mode,
                "latest_user_message": latest_user_message,
                "stored_operation_state": stored_operation_state,
                "proposed_operation": proposed_operation,
                "candidate_evidence": candidate_evidence,
                "correlation_strength": correlation_strength,
                "subject": subject,
            }
        )
