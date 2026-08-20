"""Persistent, bounded audit wrappers for LangGraph nodes."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from langgraph.runtime import Runtime
from sqlalchemy import func, select

from snoc_agent.db.base import utc_now
from snoc_agent.db.models import WorkflowEvent, WorkflowRun
from snoc_agent.db.session import session_scope
from snoc_agent.graph.context import GraphExecutionContext
from snoc_agent.graph.state import GRAPH_VERSION, WorkflowState

GraphNode = Callable[[WorkflowState, Runtime[GraphExecutionContext]], WorkflowState]


def _summary(state: WorkflowState) -> dict[str, Any]:
    return {
        "inbound_email_id": state.get("inbound_email_id"),
        "conversation_id": state.get("conversation_id"),
        "processing_status": state.get("processing_status"),
        "request_count": len(state.get("request_ids", [])),
        "operation_count": len(state.get("operation_ids", [])),
        "decision_count": len(state.get("decisions", [])),
        "execute_count": len(state.get("execute_operation_ids", [])),
        "terminal": bool(state.get("terminal", False)),
        "completed_agents": list(state.get("completed_agents", [])),
    }


def _ensure_run(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> uuid.UUID:
    run_id = uuid.UUID(state["workflow_run_id"])
    with session_scope(runtime.context.processor.session_factory) as session:
        if session.get(WorkflowRun, run_id) is None:
            session.add(
                WorkflowRun(
                    id=run_id,
                    graph_version=state.get("graph_version", GRAPH_VERSION),
                    engine="langgraph",
                    status="running",
                )
            )
    return run_id


def audited_node(agent: str, node: GraphNode) -> GraphNode:
    """Persist one independently committed event for a graph node invocation."""

    def invoke(state: WorkflowState, runtime: Runtime[GraphExecutionContext]) -> WorkflowState:
        run_id = _ensure_run(state, runtime)
        factory = runtime.context.processor.session_factory
        with session_scope(factory) as session:
            sequence = (
                int(
                    session.scalar(
                        select(func.coalesce(func.max(WorkflowEvent.sequence), 0)).where(
                            WorkflowEvent.workflow_run_id == run_id
                        )
                    )
                    or 0
                )
                + 1
            )
            event = WorkflowEvent(
                workflow_run_id=run_id,
                sequence=sequence,
                agent=agent,
                status="started",
                input_summary=_summary(state),
            )
            session.add(event)
            session.flush()
            event_id = event.id
            run = session.get_one(WorkflowRun, run_id)
            run.current_agent = agent
            run.updated_at = utc_now()
        try:
            update = node(state, runtime)
        except Exception as exc:
            with session_scope(factory) as session:
                event = session.get_one(WorkflowEvent, event_id)
                event.status = "failed"
                event.completed_at = utc_now()
                event.error_category = type(exc).__name__
                event.error_message = str(exc)[:4000]
                run = session.get_one(WorkflowRun, run_id)
                run.status = "failed"
                run.current_agent = agent
                run.completed_at = utc_now()
                run.updated_at = utc_now()
                run.error_category = type(exc).__name__
                run.error_message = str(exc)[:4000]
            raise
        merged: WorkflowState = {**state, **update}
        terminal = bool(merged.get("terminal", False))
        processing_status = merged.get("processing_status", "")
        is_failure = processing_status == "failed"
        with session_scope(factory) as session:
            event = session.get_one(WorkflowEvent, event_id)
            event.status = (
                "failed" if (terminal and is_failure) else ("terminal" if terminal else "succeeded")
            )
            event.output_summary = _summary(merged)
            event.completed_at = utc_now()
            run = session.get_one(WorkflowRun, run_id)
            email_id = merged.get("inbound_email_id")
            if email_id:
                run.inbound_email_id = uuid.UUID(email_id)
            run.current_agent = agent
            run.updated_at = utc_now()
            if terminal:
                run.status = "failed" if is_failure else "completed"
                run.completed_at = utc_now()
                if is_failure:
                    error_detail = merged.get("result", {})
                    run.error_category = "RuntimeError"
                    run.error_message = (
                        error_detail.get("detail", "") if isinstance(error_detail, dict) else ""
                    )
        return update

    return invoke
