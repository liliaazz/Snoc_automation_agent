"""Tests for enhanced agent framework, risk scorer, conversation history, and metrics."""

from __future__ import annotations

import time
from typing import Any

import pytest

from snoc_agent.ai.errors import InferenceError, InferenceErrorCategory
from snoc_agent.ai.risk_scorer import RiskScorer, RiskSignals
from snoc_agent.graph.agents.base import AgentMetrics, EnhancedBaseAgent
from snoc_agent.metrics import MetricPoint, MetricsCollector

# ── EnhancedBaseAgent tests ──────────────────────────────────────────────


class _ConcreteAgent(EnhancedBaseAgent):
    """Minimal concrete agent for testing base class logic."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(name="test_agent", **kwargs)

    def execute(self, state: dict[str, Any], runtime: Any) -> dict[str, Any]:
        return self._execute_with_retry(lambda: {"processed": True})


class _FailingAgent(EnhancedBaseAgent):
    """Agent that always fails for retry testing."""

    def __init__(self, fail_count: int = 1, **kwargs: Any) -> None:
        super().__init__(name="failing_agent", **kwargs)
        self._attempts = 0
        self._fail_count = fail_count

    def execute(self, state: dict[str, Any], runtime: Any) -> dict[str, Any]:
        def _work() -> dict[str, Any]:
            self._attempts += 1
            if self._attempts <= self._fail_count:
                raise RuntimeError(f"attempt {self._attempts} failed")
            return {"processed": True, "attempts": self._attempts}

        return self._execute_with_retry(_work)


def test_enhanced_base_agent_execute_succeeds() -> None:
    agent = _ConcreteAgent()
    result = agent.execute({}, None)
    assert result == {"processed": True}
    assert agent.metrics.success is True
    assert agent.metrics.execution_time >= 0
    assert agent.metrics.retry_count == 0


def test_enhanced_base_agent_retry_on_failure() -> None:
    agent = _FailingAgent(fail_count=2, max_retries=3, retry_delay=0.01)
    result = agent.execute({}, None)
    assert result == {"processed": True, "attempts": 3}
    assert agent.metrics.success is True
    assert agent.metrics.retry_count == 2


def test_enhanced_base_agent_exhausts_retries() -> None:
    agent = _FailingAgent(fail_count=5, max_retries=2, retry_delay=0.01)
    with pytest.raises(RuntimeError, match="attempt 3 failed"):
        agent.execute({}, None)
    assert agent.metrics.success is False
    assert agent._attempts == 3  # max_retries + 1 attempts made


def test_enhanced_base_agent_does_not_retry_non_retryable_inference_error() -> None:
    attempts = 0
    agent = _ConcreteAgent(max_retries=3, retry_delay=0.01)

    def fail_once() -> dict[str, Any]:
        nonlocal attempts
        attempts += 1
        raise InferenceError(InferenceErrorCategory.INVALID_REQUEST, "invalid context")

    with pytest.raises(InferenceError):
        agent._execute_with_retry(fail_once)

    assert attempts == 1
    assert agent.metrics.success is False


def test_enhanced_base_agent_validation_defaults() -> None:
    agent = _ConcreteAgent()
    assert agent._validate_input({}) is True
    assert agent._validate_output({}) is True


def test_agent_metrics_defaults() -> None:
    m = AgentMetrics()
    assert m.execution_time == 0.0
    assert m.retry_count == 0
    assert m.success is True
    assert m.error_message == ""


# ── RiskScorer tests ─────────────────────────────────────────────────────


class TestRiskScorer:
    """Tests for the ML-based risk scoring module."""

    @pytest.fixture
    def scorer(self) -> RiskScorer:
        return RiskScorer()

    def test_low_risk_all_good_signals(self, scorer: RiskScorer) -> None:
        signals = RiskSignals(
            svm_confidence=0.95,
            llm_confidence=0.90,
            verifier_confidence=0.95,
            correlation_strength="strong",
            sender_authorized=True,
            has_missing_fields=False,
            model_agreement=True,
            contradiction_detected=False,
        )
        score = scorer.score(signals)
        assert score.risk_level == "low"
        assert score.overall <= scorer.LOW_RISK_THRESHOLD
        assert score.recommendation == "auto_execute"

    def test_high_risk_poor_confidence(self, scorer: RiskScorer) -> None:
        signals = RiskSignals(
            svm_confidence=0.1,
            llm_confidence=0.05,
            verifier_confidence=0.0,
            correlation_strength="none",
            sender_authorized=False,
            has_missing_fields=True,
            missing_field_count=3,
            model_agreement=False,
            contradiction_detected=True,
        )
        score = scorer.score(signals)
        assert score.risk_level in ("high", "critical")
        assert score.overall > scorer.MEDIUM_RISK_THRESHOLD
        assert score.recommendation == "escalate"

    def test_medium_risk_mixed_signals(self, scorer: RiskScorer) -> None:
        signals = RiskSignals(
            svm_confidence=0.5,
            llm_confidence=0.4,
            verifier_confidence=0.3,
            correlation_strength="weak",
            sender_authorized=True,
            has_missing_fields=True,
            missing_field_count=1,
            model_agreement=False,
            contradiction_detected=False,
        )
        score = scorer.score(signals)
        assert score.risk_level == "medium"
        assert scorer.LOW_RISK_THRESHOLD < score.overall <= scorer.MEDIUM_RISK_THRESHOLD

    def test_medium_risk_with_contradiction_escalates(self, scorer: RiskScorer) -> None:
        signals = RiskSignals(
            svm_confidence=0.7,
            llm_confidence=0.6,
            verifier_confidence=0.5,
            correlation_strength="weak",
            sender_authorized=True,
            has_missing_fields=True,
            missing_field_count=1,
            model_agreement=False,
            contradiction_detected=True,
        )
        score = scorer.score(signals)
        assert score.risk_level == "medium"
        assert score.recommendation == "escalate"

    def test_critical_risk_all_worst(self, scorer: RiskScorer) -> None:
        signals = RiskSignals(
            svm_confidence=0.0,
            llm_confidence=0.0,
            verifier_confidence=0.0,
            correlation_strength="none",
            sender_authorized=False,
            has_missing_fields=True,
            missing_field_count=5,
            model_agreement=False,
            contradiction_detected=True,
        )
        score = scorer.score(signals)
        assert score.risk_level == "critical"
        assert score.overall > scorer.HIGH_RISK_THRESHOLD
        assert score.recommendation == "escalate"

    def test_score_from_operation_data(self, scorer: RiskScorer) -> None:
        score = scorer.score_from_operation_data(
            analyzer_confidence={"raw_svm_confidence": 0.85, "raw_llm_confidence": 0.80},
            verifier_confidence={"raw_confidence": 0.90},
            correlation={"strength": "strong"},
            sender_authorized=True,
            missing_fields=[],
            model_agreement=True,
            contradiction_data=None,
            action="unblock",
        )
        assert score.risk_level == "low"
        assert score.recommendation == "auto_execute"

    def test_factor_weights_sum_to_one(self, scorer: RiskScorer) -> None:
        total = sum(scorer.WEIGHTS.values())
        assert abs(total - 1.0) < 0.01, f"Weights sum to {total}, expected 1.0"


# ── ConversationHistoryService tests ─────────────────────────────────────


@pytest.mark.skip(reason="requires database setup; tested via integration tests")
class TestConversationHistoryService:
    """Placeholder for conversation history service tests.

    These require a database fixture. The integration test suite covers
    the full conversation history retrieval path.
    """

    def test_placeholder(self) -> None:
        pass


# ── MetricsCollector tests ───────────────────────────────────────────────


class TestMetricsCollector:
    """Tests for the Prometheus-compatible metrics collector."""

    @pytest.fixture
    def collector(self) -> MetricsCollector:
        return MetricsCollector()

    def test_counter_defaults_to_zero(self, collector: MetricsCollector) -> None:
        summary = collector.get_summary()
        assert summary["counters"] == {}

    def test_increment_counter(self, collector: MetricsCollector) -> None:
        collector.increment_counter("test_count")
        summary = collector.get_summary()
        assert summary["counters"]["test_count"] == 1.0

    def test_increment_counter_with_value(self, collector: MetricsCollector) -> None:
        collector.increment_counter("test_count", 5.0)
        summary = collector.get_summary()
        assert summary["counters"]["test_count"] == 5.0

    def test_increment_counter_with_labels(self, collector: MetricsCollector) -> None:
        collector.increment_counter("test_count", 1.0, {"agent": "nlu", "status": "success"})
        summary = collector.get_summary()
        assert 'test_count{agent="nlu",status="success"}' in summary["counters"]

    def test_counter_accumulates(self, collector: MetricsCollector) -> None:
        collector.increment_counter("ops_total", 1.0)
        collector.increment_counter("ops_total", 2.0)
        collector.increment_counter("ops_total", 3.0)
        summary = collector.get_summary()
        assert summary["counters"]["ops_total"] == 6.0

    def test_set_gauge(self, collector: MetricsCollector) -> None:
        collector.set_gauge("active_requests", 42.0)
        summary = collector.get_summary()
        assert summary["gauges"]["active_requests"] == 42.0

    def test_gauge_overwrites(self, collector: MetricsCollector) -> None:
        collector.set_gauge("queue_depth", 10.0)
        collector.set_gauge("queue_depth", 20.0)
        summary = collector.get_summary()
        assert summary["gauges"]["queue_depth"] == 20.0

    def test_observe_histogram(self, collector: MetricsCollector) -> None:
        collector.observe_histogram("latency_ms", 100.0)
        collector.observe_histogram("latency_ms", 200.0)
        collector.observe_histogram("latency_ms", 300.0)
        summary = collector.get_summary()
        hist = summary["histograms"]["latency_ms"]
        assert hist["count"] == 3
        assert hist["sum"] == 600.0
        assert hist["avg"] == 200.0
        assert hist["min"] == 100.0
        assert hist["max"] == 300.0

    def test_prometheus_format_counters(self, collector: MetricsCollector) -> None:
        collector.increment_counter("requests_total", 10.0, {"status": "ok"})
        output = collector.format_prometheus()
        assert "# TYPE requests_total counter" in output
        assert 'requests_total{status="ok"} 10.0' in output

    def test_prometheus_format_gauges(self, collector: MetricsCollector) -> None:
        collector.set_gauge("memory_bytes", 1048576.0)
        output = collector.format_prometheus()
        assert "# TYPE memory_bytes gauge" in output
        assert "memory_bytes 1048576.0" in output

    def test_prometheus_format_histogram(self, collector: MetricsCollector) -> None:
        collector.observe_histogram("request_duration", 0.5)
        collector.observe_histogram("request_duration", 1.5)
        output = collector.format_prometheus()
        assert "# TYPE request_duration histogram" in output
        assert "request_duration_sum" in output
        assert "request_duration_count" in output
        assert 'request_duration{quantile="0.5"}' in output

    def test_empty_collector_format(self, collector: MetricsCollector) -> None:
        output = collector.format_prometheus()
        assert output == "\n"

    def test_thread_safety(self, collector: MetricsCollector) -> None:
        import concurrent.futures

        def _worker(idx: int) -> None:
            collector.increment_counter("concurrent_count")
            collector.set_gauge("concurrent_gauge", float(idx))
            collector.observe_histogram("concurrent_hist", float(idx))

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_worker, range(100)))

        summary = collector.get_summary()
        assert summary["counters"]["concurrent_count"] == 100.0
        assert summary["histograms"]["concurrent_hist"]["count"] == 100


# ── MetricPoint tests ────────────────────────────────────────────────────


class TestMetricPoint:
    def test_default_timestamp_is_current(self) -> None:
        before = time.time()
        point = MetricPoint(name="test", value=1.0)
        after = time.time()
        assert before <= point.timestamp <= after

    def test_default_labels_empty(self) -> None:
        point = MetricPoint(name="test", value=1.0)
        assert point.labels == {}

    def test_custom_labels(self) -> None:
        point = MetricPoint(name="test", value=1.0, labels={"env": "prod"})
        assert point.labels == {"env": "prod"}
