from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from snoc_agent.ai.langchain_adapter import (
    LangChainEmailAnalyzer,
    LangChainSemanticVerifier,
)
from snoc_agent.cli.runtime import build_runtime
from snoc_agent.config import Settings
from snoc_agent.db.models import (
    BusinessRequest,
    EmailMessage,
    Escalation,
    Execution,
    Operation,
    ValidationDecision,
    WorkflowEvent,
    WorkflowRun,
)
from snoc_agent.db.session import session_scope
from snoc_agent.graph.processor import LangGraphInboundProcessor

FIXTURE = Path("tests/fixtures/emails/scenario_a_complete_unblock/01_complete_unblock.eml")


def _settings(database_path: Path, *, engine: str) -> Settings:
    return Settings(
        database_url=f"sqlite:///{database_path}",
        workflow_engine=engine,
        authorized_senders="animateur.alpha@example.invalid",
        store_raw_eml=False,
        dry_run=True,
        execution_correction_grace_seconds=0,
    )


def _snapshot(runtime) -> dict[str, object]:
    with session_scope(runtime.session_factory) as session:
        operation = session.scalar(select(Operation))
        decision = session.scalar(select(ValidationDecision))
        execution = session.scalar(select(Execution))
        return {
            "emails": session.scalar(select(func.count()).select_from(EmailMessage)),
            "requests": session.scalar(select(func.count()).select_from(BusinessRequest)),
            "operations": session.scalar(select(func.count()).select_from(Operation)),
            "action": operation.action if operation else None,
            "operation_status": operation.status if operation else None,
            "decision": decision.decision if decision else None,
            "execution_status": execution.status if execution else None,
            "dry_run": execution.dry_run if execution else None,
        }


def test_runtime_defaults_to_separately_preserved_legacy_processor(tmp_path: Path) -> None:
    runtime = build_runtime(
        _settings(tmp_path / "legacy.db", engine="legacy"), initialize_schema=True
    )

    assert runtime.processor is runtime.legacy_processor


def test_langgraph_runtime_keeps_legacy_processor_available(tmp_path: Path) -> None:
    runtime = build_runtime(
        _settings(tmp_path / "graph.db", engine="langgraph"), initialize_schema=True
    )

    assert isinstance(runtime.processor, LangGraphInboundProcessor)
    assert runtime.processor.legacy_processor is runtime.legacy_processor
    assert isinstance(runtime.processor.graph_processor.analyzer, LangChainEmailAnalyzer)
    assert isinstance(runtime.processor.graph_processor.verifier, LangChainSemanticVerifier)
    assert runtime.legacy_processor.analyzer is not runtime.processor.graph_processor.analyzer
    assert runtime.legacy_processor.verifier is not runtime.processor.graph_processor.verifier


def test_compiled_graph_exposes_exactly_five_agent_nodes(tmp_path: Path) -> None:
    runtime = build_runtime(
        _settings(tmp_path / "topology.db", engine="langgraph"), initialize_schema=True
    )
    graph_nodes = set(runtime.processor.graph.get_graph().nodes)

    assert graph_nodes - {"__start__", "__end__"} == {
        "ingress",
        "security",
        "nlu",
        "policy",
        "fulfilment",
    }


def test_five_agent_graph_matches_legacy_business_outcome(tmp_path: Path) -> None:
    raw = FIXTURE.read_bytes()
    legacy = build_runtime(
        _settings(tmp_path / "legacy.db", engine="legacy"),
        replay_authorized_senders={"animateur.alpha@example.invalid"},
        initialize_schema=True,
    )
    graph = build_runtime(
        _settings(tmp_path / "graph.db", engine="langgraph"),
        replay_authorized_senders={"animateur.alpha@example.invalid"},
        initialize_schema=True,
    )

    legacy_result = legacy.processor.process_raw(raw)
    graph_result = graph.processor.process_raw(raw)

    assert graph_result.status == legacy_result.status
    assert graph_result.decisions == legacy_result.decisions
    assert graph_result.detail == legacy_result.detail
    assert _snapshot(graph) == _snapshot(legacy)
    with session_scope(graph.session_factory) as session:
        run = session.scalars(select(WorkflowRun)).one()
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run.id)
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert run.status == "completed"
        assert run.inbound_email_id == graph_result.email_message_id
        assert [event.agent for event in events] == [
            "ingress",
            "security",
            "nlu",
            "policy",
            "fulfilment",
        ]
        assert [event.status for event in events] == [
            "succeeded",
            "succeeded",
            "succeeded",
            "succeeded",
            "terminal",
        ]


def test_langgraph_replay_preserves_execution_idempotency(tmp_path: Path) -> None:
    raw = FIXTURE.read_bytes()
    runtime = build_runtime(
        _settings(tmp_path / "replay.db", engine="langgraph"),
        replay_authorized_senders={"animateur.alpha@example.invalid"},
        initialize_schema=True,
    )

    first = runtime.processor.process_raw(raw)
    replay = runtime.processor.process_raw(raw)

    assert first.status == "processed"
    assert replay.status == "duplicate"
    with session_scope(runtime.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 1
        assert session.scalar(select(func.count()).select_from(Execution)) == 1
        assert session.scalar(select(func.count()).select_from(WorkflowRun)) == 2


def test_langgraph_unauthorized_sender_escalates_before_nlu(
    tmp_path: Path,
) -> None:
    raw = FIXTURE.read_bytes()
    runtime = build_runtime(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'unauthorized.db'}",
            workflow_engine="langgraph",
            authorized_senders="other@example.com",
            store_raw_eml=False,
            dry_run=True,
        ),
        initialize_schema=True,
    )

    result = runtime.processor.process_raw(raw)

    # The merged production behavior preserves the unauthorized email,
    # creates a human-review escalation, and terminates before NLU.
    assert result.status == "processed"
    assert result.decisions == ["ESCALATE"]
    assert result.detail == "sender_not_whitelisted_escalated"

    with session_scope(runtime.session_factory) as session:
        assert session.scalar(select(func.count()).select_from(Operation)) == 0
        assert session.scalar(select(func.count()).select_from(ValidationDecision)) == 0
        assert session.scalar(select(func.count()).select_from(Execution)) == 0

        email = session.scalars(select(EmailMessage)).one()
        assert email.authorization_allowed is False
        assert email.authorization_reason == "sender_not_whitelisted"
        assert email.processing_status == "processed"

        escalation = session.scalars(select(Escalation)).one()
        assert escalation.email_message_id == email.id
        assert escalation.reason_code == "sender_not_whitelisted"
        assert escalation.request_id is None

        run = session.scalars(select(WorkflowRun)).one()
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run.id)
            .order_by(WorkflowEvent.sequence)
        ).all()

        # No NLU, policy, verifier, or fulfilment agent is reached.
        assert [event.agent for event in events] == ["ingress", "security"]
        assert [event.status for event in events] == ["succeeded", "terminal"]


def test_langgraph_node_failure_is_durably_audited(tmp_path: Path, monkeypatch) -> None:
    raw = FIXTURE.read_bytes()
    runtime = build_runtime(
        _settings(tmp_path / "failure.db", engine="langgraph"),
        replay_authorized_senders={"animateur.alpha@example.invalid"},
        initialize_schema=True,
    )

    def fail_analysis(_context):
        raise RuntimeError("forced analyzer failure")

    monkeypatch.setattr(runtime.processor.graph_processor.analyzer, "analyze", fail_analysis)
    result = runtime.processor.process_raw(raw)

    assert result.status == "failed"
    with session_scope(runtime.session_factory) as session:
        run = session.scalars(select(WorkflowRun)).one()
        events = session.scalars(
            select(WorkflowEvent)
            .where(WorkflowEvent.workflow_run_id == run.id)
            .order_by(WorkflowEvent.sequence)
        ).all()
        assert run.status == "failed"
        assert run.error_category == "RuntimeError"
        assert [event.agent for event in events] == ["ingress", "security", "nlu"]
        assert events[-1].status == "failed"
