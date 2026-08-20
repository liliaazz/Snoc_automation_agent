"""Enhanced base agent class with retry logic and error handling."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from snoc_agent.ai.errors import InferenceError

if TYPE_CHECKING:
    from snoc_agent.graph.state import WorkflowState

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class AgentMetrics:
    """Metrics for agent execution."""

    execution_time: float = 0.0
    retry_count: int = 0
    success: bool = True
    error_message: str = ""
    input_tokens: int = 0
    output_tokens: int = 0


class EnhancedBaseAgent:
    """Base class for enhanced agents with retry logic and error handling."""

    def __init__(
        self,
        name: str,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        backoff_factor: float = 2.0,
        timeout: float = 30.0,
    ):
        self.name = name
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.backoff_factor = backoff_factor
        self.timeout = timeout
        self.metrics = AgentMetrics()

    def _execute_with_retry(
        self,
        func: Callable[..., T],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute a function with retry logic."""
        last_exception: Exception | None = None
        current_delay = self.retry_delay

        for attempt in range(self.max_retries + 1):
            try:
                start_time = time.time()
                result = func(*args, **kwargs)
                self.metrics.execution_time = time.time() - start_time
                self.metrics.retry_count = attempt
                self.metrics.success = True
                return result
            except Exception as e:
                last_exception = e
                self.metrics.error_message = str(e)
                logger.warning(f"Agent {self.name} attempt {attempt + 1} failed: {e}")

                retryable = not isinstance(e, InferenceError) or e.retryable
                if retryable and attempt < self.max_retries:
                    time.sleep(current_delay)
                    current_delay *= self.backoff_factor
                    continue
                break

        self.metrics.success = False

        if last_exception is None:
            raise RuntimeError(f"Agent {self.name} failed without an exception")

        raise last_exception

    def _validate_input(self, state: WorkflowState) -> bool:
        """Validate input state for the agent."""
        # Override in subclasses for specific validation
        return True

    def _validate_output(self, output: WorkflowState) -> bool:
        """Validate output from the agent."""
        # Override in subclasses for specific validation
        return True

    def _log_execution(
        self,
        state: WorkflowState,
        output: WorkflowState,
    ) -> None:
        """Log execution details."""
        logger.info(
            f"Agent {self.name} executed",
            extra={
                "agent": self.name,
                "execution_time": self.metrics.execution_time,
                "retry_count": self.metrics.retry_count,
                "success": self.metrics.success,
                "input_state_keys": list(state.keys()),
                "output_keys": list(output.keys()),
            },
        )
