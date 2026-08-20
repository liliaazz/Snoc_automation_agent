"""Enhanced security and correlation graph agent with retry logic."""

from __future__ import annotations

import logging
import uuid

from langgraph.runtime import Runtime

from snoc_agent.graph.agents.base import EnhancedBaseAgent
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.serialization import result_to_dict
from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)


class SecurityAgent(EnhancedBaseAgent):
    """Enhanced security agent with retry logic and validation."""

    def __init__(self):
        super().__init__(
            name="security",
            max_retries=3,
            retry_delay=1.0,
            backoff_factor=2.0,
            timeout=30.0,
        )

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for security agent."""
        return "inbound_email_id" in state

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from security agent."""
        required_fields = ["conversation_id", "authorization", "correlation"]
        return all(field in output for field in required_fields)

    def execute(
        self, state: WorkflowState, runtime: Runtime[GraphExecutionContext]
    ) -> WorkflowState:
        """Execute security agent with retry logic."""

        def _execute() -> WorkflowState:
            email_id = uuid.UUID(state["inbound_email_id"])
            result = LegacyStageAdapter().security(runtime.context, email_id)
            completed = [*state.get("completed_agents", []), "security"]

            if result is not None:
                return {
                    "processing_status": result.status,
                    "conversation_id": (
                        str(result.conversation_id) if result.conversation_id else None
                    ),
                    "result": result_to_dict(result),
                    "terminal": True,
                    "completed_agents": completed,
                }

            prepared = runtime.context.prepared
            if prepared is None:
                raise RuntimeError("Security agent: prepared context is None")

            return {
                "conversation_id": str(prepared.conversation_id),
                "authorization": {"allowed": True},
                "correlation": {
                    "strength": prepared.correlation.strength.value,
                    "matched_by": prepared.correlation.matched_by,
                    "request_id": prepared.correlation.request_id,
                    "clarification_id": prepared.correlation.clarification_id,
                    "conflicts": list(prepared.correlation.conflicts),
                },
                "terminal": False,
                "completed_agents": completed,
            }

        try:
            output = self._execute_with_retry(_execute)
            self._log_execution(state, output)
            return output
        except Exception as e:
            logger.error(f"Security agent failed after retries: {e}")
            return {
                "processing_status": "failed",
                "result": {
                    "email_message_id": state.get("inbound_email_id", ""),
                    "status": "failed",
                    "detail": str(e),
                },
                "terminal": True,
                "completed_agents": [*state.get("completed_agents", []), "security"],
            }


def security_agent(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> WorkflowState:
    """Enhanced security agent function."""
    agent = SecurityAgent()
    return agent.execute(state, runtime)
