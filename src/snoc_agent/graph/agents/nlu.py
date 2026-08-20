"""Enhanced NLU graph agent with retry logic and validation."""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from snoc_agent.graph.agents.base import EnhancedBaseAgent
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.serialization import result_to_dict
from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)


class NLUAgent(EnhancedBaseAgent):
    """Enhanced NLU agent with retry logic and validation."""

    def __init__(self):
        super().__init__(
            name="nlu",
            max_retries=3,
            retry_delay=2.0,
            backoff_factor=2.0,
            timeout=60.0,
        )

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for NLU agent."""
        return "inbound_email_id" in state

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from NLU agent."""
        required_fields = [
            "request_ids",
            "operation_ids",
            "analysis",
            "execute_operation_ids",
            "decisions",
        ]
        return all(field in output for field in required_fields)

    def execute(
        self, state: WorkflowState, runtime: Runtime[GraphExecutionContext]
    ) -> WorkflowState:
        """Execute NLU agent with retry logic."""

        def _execute() -> WorkflowState:
            adapter = LegacyStageAdapter()
            work, request_ids, early = adapter.analyze(runtime.context)
            completed = [*state.get("completed_agents", []), "nlu"]

            if early is not None:
                return {
                    "request_ids": [str(value) for value in request_ids],
                    "processing_status": early.status,
                    "result": result_to_dict(early),
                    "terminal": True,
                    "completed_agents": completed,
                }

            execute_ids, decisions = adapter.verify_and_decide(runtime.context)
            analysis = runtime.context.analysis

            return {
                "request_ids": [str(value) for value in request_ids],
                "operation_ids": [str(item.operation_id) for item in work],
                "analysis": analysis.model_dump(mode="json") if analysis else {},
                "execute_operation_ids": [str(value) for value in execute_ids],
                "decisions": decisions,
                "terminal": False,
                "completed_agents": completed,
            }

        try:
            output = self._execute_with_retry(_execute)
            self._log_execution(state, output)
            return output
        except Exception as e:
            logger.error(f"NLU agent failed after retries: {e}")
            email_id = runtime.context.email_id
            if email_id is not None:
                failed = LegacyStageAdapter().mark_failed(runtime.context, email_id, e)
                failed_result = result_to_dict(failed)
            else:
                failed_result = {
                    "email_message_id": state.get("inbound_email_id", ""),
                    "status": "failed",
                    "detail": str(e),
                }
            return {
                "processing_status": "failed",
                "result": failed_result,
                "terminal": True,
                "completed_agents": [*state.get("completed_agents", []), "nlu"],
            }


def nlu_agent(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> WorkflowState:
    """Enhanced NLU agent function."""
    agent = NLUAgent()
    return agent.execute(state, runtime)
