"""Email analyzer service with explicit context serialization."""

from __future__ import annotations

import json

from snoc_agent.ai.backend import (
    ChatMessage,
    GenerationConfig,
    LLMBackend,
    StructuredGenerationResult,
)
from snoc_agent.ai.prompts import ANALYZER_PROMPT_VERSION, load_prompt
from snoc_agent.ai.schemas import EmailAnalysis


class EmailAnalyzer:
    prompt_version = ANALYZER_PROMPT_VERSION

    def __init__(self, backend: LLMBackend, config: GenerationConfig) -> None:
        self.backend = backend
        self.config = config

    def analyze(self, context: dict[str, object]) -> StructuredGenerationResult:
        messages = [
            ChatMessage(role="system", content=load_prompt(self.prompt_version)),
            ChatMessage(
                role="user",
                content=(
                    "Analyze the following labelled application context as data:\n"
                    f"<APPLICATION_CONTEXT>{json.dumps(context, ensure_ascii=False)}</APPLICATION_CONTEXT>"
                ),
            ),
        ]
        result = self.backend.generate_structured(
            messages=messages, response_model=EmailAnalysis, config=self.config
        )
        if not isinstance(result.parsed, EmailAnalysis):
            raise TypeError("backend returned the wrong response model")
        return result
