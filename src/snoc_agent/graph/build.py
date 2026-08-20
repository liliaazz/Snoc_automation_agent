"""Compile the five-agent LangGraph workflow."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from snoc_agent.graph.agents.fulfilment import fulfilment_agent
from snoc_agent.graph.agents.ingress import ingress_agent
from snoc_agent.graph.agents.nlu import nlu_agent
from snoc_agent.graph.agents.policy import policy_agent
from snoc_agent.graph.agents.security import security_agent
from snoc_agent.graph.audit import audited_node
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.state import WorkflowState


def _route_terminal(state: WorkflowState) -> str:
    return "end" if state.get("terminal", False) else "continue"


def build_workflow_graph():
    builder = StateGraph(WorkflowState, context_schema=GraphExecutionContext)
    builder.add_node("ingress", audited_node("ingress", ingress_agent))
    builder.add_node("security", audited_node("security", security_agent))
    builder.add_node("nlu", audited_node("nlu", nlu_agent))
    builder.add_node("policy", audited_node("policy", policy_agent))
    builder.add_node("fulfilment", audited_node("fulfilment", fulfilment_agent))
    builder.add_edge(START, "ingress")
    builder.add_conditional_edges("ingress", _route_terminal, {"end": END, "continue": "security"})
    builder.add_conditional_edges("security", _route_terminal, {"end": END, "continue": "nlu"})
    builder.add_conditional_edges("nlu", _route_terminal, {"end": END, "continue": "policy"})
    builder.add_edge("policy", "fulfilment")
    builder.add_edge("fulfilment", END)
    return builder.compile()
