"""Application-specific error types."""


class SnocAgentError(Exception):
    """Base error."""


class InvalidStateTransition(SnocAgentError):
    """Raised when code attempts a forbidden state transition."""


class CorrelationConflict(SnocAgentError):
    """Raised when independent correlation signals disagree."""


class StructuredOutputError(SnocAgentError):
    """Raised after bounded structured-output parsing attempts fail."""


class UnsafeExecutionError(SnocAgentError):
    """Raised when a hard invariant blocks an operation."""
