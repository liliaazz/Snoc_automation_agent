"""Enhanced deterministic fail-closed policy routing agent with validation."""

from __future__ import annotations

import logging
import uuid

from langgraph.runtime import Runtime

from snoc_agent.graph.agents.base import EnhancedBaseAgent
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)


class PolicyAgent(EnhancedBaseAgent):
    """Enhanced policy agent with validation and error handling."""

    def __init__(self):
        super().__init__(
            name="policy",
            max_retries=1,
            retry_delay=0.5,
            backoff_factor=1.0,
            timeout=10.0,
        )

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for policy agent."""
        return "operation_ids" in state and "execute_operation_ids" in state

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from policy agent."""
        return "completed_agents" in output

    def execute(
        self, state: WorkflowState, runtime: Runtime[GraphExecutionContext]
    ) -> WorkflowState:
        """Execute policy agent with validation."""

        def _execute() -> WorkflowState:
            operation_ids = [uuid.UUID(value) for value in state.get("operation_ids", [])]
            execute_ids = [uuid.UUID(value) for value in state.get("execute_operation_ids", [])]

            # Validate policy outputs
            LegacyStageAdapter().assert_policy_outputs(runtime.context, operation_ids, execute_ids)

            return {
                "completed_agents": [*state.get("completed_agents", []), "policy"],
                "terminal": False,
            }

        try:
            output = self._execute_with_retry(_execute)
            self._log_execution(state, output)
            return output
        except Exception as e:
            logger.error(f"Policy agent failed: {e}")
            # Policy failures are critical - escalate immediately
            return {
                "processing_status": "failed",
                "result": {
                    "email_message_id": state.get("inbound_email_id", ""),
                    "status": "failed",
                    "detail": f"Policy validation failed: {e!s}",
                },
                "terminal": True,
                "completed_agents": [*state.get("completed_agents", []), "policy"],
            }


def policy_agent(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> WorkflowState:
    """Enhanced policy agent function."""
    agent = PolicyAgent()
    return agent.execute(state, runtime)
