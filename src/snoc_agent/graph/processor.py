"""InboundProcessor-compatible adapter backed by the compiled graph."""

from __future__ import annotations

import logging
import uuid
from copy import copy

from snoc_agent.ai.langchain_adapter import (
    LangChainEmailAnalyzer,
    LangChainSemanticVerifier,
)
from snoc_agent.graph.build import build_workflow_graph
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.serialization import result_from_dict
from snoc_agent.graph.state import GRAPH_VERSION, WorkflowState
from snoc_agent.workflow.inbound_processor import (
    InboundIdentity,
    InboundProcessor,
    ProcessingResult,
)

LOGGER = logging.getLogger(__name__)


class LangGraphInboundProcessor:
    """Drop-in processor that preserves a separately callable legacy processor."""

    def __init__(self, legacy_processor: InboundProcessor) -> None:
        self.legacy_processor = legacy_processor
        self.graph_processor = copy(legacy_processor)
        self.graph_processor.analyzer = LangChainEmailAnalyzer(legacy_processor.analyzer)  # type: ignore[assignment]
        self.graph_processor.verifier = LangChainSemanticVerifier(legacy_processor.verifier)  # type: ignore[assignment]
        self.graph = build_workflow_graph()

    def process_raw(
        self,
        raw_message: bytes,
        *,
        identity: InboundIdentity | None = None,
        execute_operations: bool = True,
    ) -> ProcessingResult:
        invocation = GraphExecutionContext(
            processor=self.graph_processor,
            raw_message=raw_message,
            identity=identity or InboundIdentity(),
            execute_operations=execute_operations,
        )
        initial: WorkflowState = {
            "workflow_run_id": str(uuid.uuid4()),
            "graph_version": GRAPH_VERSION,
            "completed_agents": [],
            "terminal": False,
        }
        try:
            final = self.graph.invoke(initial, context=invocation)
            result = final.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("graph completed without a ProcessingResult")
            return result_from_dict(result)
        except Exception as exc:
            LOGGER.exception("LangGraph inbound processing failed")
            email_id = invocation.email_id
            if email_id is None:
                raise
            return LegacyStageAdapter().mark_failed(invocation, email_id, exc)

    def retry_stored(self, email_id: uuid.UUID) -> ProcessingResult:
        # Retried/quarantined rows retain the proven legacy path until resumable graph
        # reconstruction from persisted email state is implemented.
        return self.legacy_processor.retry_stored(email_id)
