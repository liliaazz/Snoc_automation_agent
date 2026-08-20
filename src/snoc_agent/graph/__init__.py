"""Feature-flagged LangGraph orchestration for the SNOC workflow."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snoc_agent.graph.processor import LangGraphInboundProcessor


def get_langgraph_processor() -> type[LangGraphInboundProcessor]:
    """Lazy import to avoid pulling in langchain at package load time."""
    from snoc_agent.graph.processor import LangGraphInboundProcessor

    return LangGraphInboundProcessor


__all__ = ["get_langgraph_processor"]
