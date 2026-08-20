"""Enhanced ingress graph agent with retry logic and error handling."""

from __future__ import annotations

import logging

from langgraph.runtime import Runtime

from snoc_agent.graph.agents.base import EnhancedBaseAgent
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.legacy_adapter import LegacyStageAdapter
from snoc_agent.graph.serialization import result_to_dict
from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)


class IngressAgent(EnhancedBaseAgent):
    """Enhanced ingress agent with retry logic and validation."""

    def __init__(self):
        super().__init__(
            name="ingress",
            max_retries=3,
            retry_delay=1.0,
            backoff_factor=2.0,
            timeout=30.0,
        )

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for ingress agent."""
        # Ingress doesn't require specific input state
        return True

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from ingress agent."""
        if "inbound_email_id" not in output:
            return False
        return "processing_status" in output

    def execute(
        self, state: WorkflowState, runtime: Runtime[GraphExecutionContext]
    ) -> WorkflowState:
        """Execute ingress agent with retry logic."""

        def _execute() -> WorkflowState:
            result = LegacyStageAdapter().ingress(runtime.context)
            completed = [*state.get("completed_agents", []), "ingress"]

            if result is not None:
                return {
                    "inbound_email_id": str(result.email_message_id),
                    "processing_status": result.status,
                    "result": result_to_dict(result),
                    "terminal": True,
                    "completed_agents": completed,
                }

            if runtime.context.email_id is None:
                raise RuntimeError("ingress completed without a persisted email ID")

            return {
                "inbound_email_id": str(runtime.context.email_id),
                "terminal": False,
                "completed_agents": completed,
            }

        try:
            output = self._execute_with_retry(_execute)
            self._log_execution(state, output)
            return output
        except Exception as exc:
            logger.exception("Ingress agent failed after retries")
            email_id = runtime.context.email_id

            # Do not fabricate an empty email UUID. If raw persistence failed
            # before an email row was created, preserve and re-raise the
            # original exception for the orchestrator.
            if email_id is None:
                raise

            failed = LegacyStageAdapter().mark_failed(
                runtime.context,
                email_id,
                exc,
            )
            return {
                "inbound_email_id": str(email_id),
                "processing_status": failed.status,
                "result": result_to_dict(failed),
                "terminal": True,
                "completed_agents": [*state.get("completed_agents", []), "ingress"],
            }


def ingress_agent(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> WorkflowState:
    """Enhanced ingress agent function."""
    agent = IngressAgent()
    return agent.execute(state, runtime)
