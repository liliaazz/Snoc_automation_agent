"""Enhanced fulfilment graph agent with retry logic, validation, and error handling."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from langgraph.runtime import Runtime

from snoc_agent.graph.agents.base import AgentMetrics, EnhancedBaseAgent
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.serialization import result_to_dict
from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)


@dataclass
class FulfilmentMetrics(AgentMetrics):
    """Extended metrics for fulfilment execution."""

    operations_total: int = 0
    operations_executed: int = 0
    operations_escalated: int = 0


class FulfilmentAgent(EnhancedBaseAgent):
    """Enhanced fulfilment agent with retry logic, validation, and error handling."""

    def __init__(self):
        super().__init__(
            name="fulfilment",
            max_retries=2,
            retry_delay=1.0,
            backoff_factor=2.0,
            timeout=60.0,
        )
        self.fulfilment_metrics = FulfilmentMetrics()

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for fulfilment agent."""
        if "inbound_email_id" not in state:
            return False
        return "execute_operation_ids" in state

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from fulfilment agent."""
        required_fields = ["processing_status", "result", "terminal", "completed_agents"]
        return all(field in output for field in required_fields)

    def execute(
        self,
        state: WorkflowState,
        runtime: Runtime[GraphExecutionContext],
    ) -> WorkflowState:
        """Execute fulfilment agent with retry logic."""

        def _execute() -> WorkflowState:
            email_id = uuid.UUID(state["inbound_email_id"])
            execute_ids = [uuid.UUID(value) for value in state.get("execute_operation_ids", [])]
            request_ids = [uuid.UUID(value) for value in state.get("request_ids", [])]
            decisions = list(state.get("decisions", []))

            self.fulfilment_metrics.operations_total = len(execute_ids)

            result = LegacyStageAdapter().fulfil(
                runtime.context,
                email_id=email_id,
                request_ids=request_ids,
                execute_ids=execute_ids,
                decisions=decisions,
            )

            updated_decisions = result.decisions
            original_decision_count = len(decisions)
            self.fulfilment_metrics.operations_escalated = (
                len(updated_decisions) - original_decision_count
            )
            self.fulfilment_metrics.operations_executed = (
                self.fulfilment_metrics.operations_total
                - self.fulfilment_metrics.operations_escalated
            )

            result_dict = result_to_dict(result)
            completed = [*state.get("completed_agents", []), "fulfilment"]

            return {
                "processing_status": result.status,
                "result": result_dict,
                "terminal": True,
                "completed_agents": completed,
            }

        try:
            output = self._execute_with_retry(_execute)
            if self._validate_output(output):
                self._log_execution(state, output)
            else:
                logger.warning(
                    "Fulfilment agent output failed validation: %s",
                    {k: type(v).__name__ for k, v in output.items()},
                )
            return output
        except Exception as e:
            logger.error("Fulfilment agent failed after retries: %s", e)
            return {
                "processing_status": "failed",
                "result": {
                    "email_message_id": state.get("inbound_email_id", ""),
                    "status": "failed",
                    "detail": str(e),
                },
                "terminal": True,
                "completed_agents": [*state.get("completed_agents", []), "fulfilment"],
            }


def fulfilment_agent(
    state: WorkflowState, runtime: Runtime[GraphExecutionContext]
) -> WorkflowState:
    """Enhanced fulfilment agent function."""
    agent = FulfilmentAgent()
    return agent.execute(state, runtime)
