"""Non-checkpointed invocation dependencies for graph nodes."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from snoc_agent.ai.schemas import EmailAnalysis
from snoc_agent.mail.parser import ParsedEmail
from snoc_agent.workflow.inbound_processor import (
    InboundIdentity,
    InboundProcessor,
    _OperationWork,
    _Prepared,
)


@dataclass(slots=True)
class GraphExecutionContext:
    processor: InboundProcessor
    raw_message: bytes
    identity: InboundIdentity
    execute_operations: bool
    email_id: uuid.UUID | None = None
    parsed: ParsedEmail | None = None
    prepared: _Prepared | None = None
    analysis: EmailAnalysis | None = None
    work: list[_OperationWork] | None = None
