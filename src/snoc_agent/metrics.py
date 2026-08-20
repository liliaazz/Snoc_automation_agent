"""Prometheus-compatible metrics collection and exposure for the SNOC API."""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class MetricPoint:
    """A single metric data point."""

    name: str
    value: float
    labels: dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class MetricsCollector:
    """Thread-safe in-memory metrics collector that exposes Prometheus-compatible text format."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._counters: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = defaultdict(float)
        self._histograms: dict[str, list[float]] = defaultdict(list)
        self._labels: dict[str, dict[str, str]] = {}

    def increment_counter(
        self, name: str, value: float = 1.0, labels: dict[str, str] | None = None
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._counters[key] += value
            if labels:
                self._labels[key] = labels

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._gauges[key] = value
            if labels:
                self._labels[key] = labels

    def observe_histogram(
        self, name: str, value: float, labels: dict[str, str] | None = None
    ) -> None:
        key = self._key(name, labels)
        with self._lock:
            self._histograms[key].append(value)
            if labels:
                self._labels[key] = labels

    def _key(self, name: str, labels: dict[str, str] | None) -> str:
        if not labels:
            return name
        label_str = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
        return f"{name}{{{label_str}}}"

    def format_prometheus(self) -> str:
        """Format all metrics in Prometheus exposition format."""
        lines: list[str] = []
        with self._lock:
            for key, value in sorted(self._counters.items()):
                base_name = key.split("{")[0] if "{" in key else key
                label_str = self._format_embedded_labels(key)
                lines.append(f"# TYPE {base_name} counter")
                lines.append(f"{base_name}{label_str} {value}")

            for key, value in sorted(self._gauges.items()):
                base_name = key.split("{")[0] if "{" in key else key
                label_str = self._format_embedded_labels(key)
                lines.append(f"# TYPE {base_name} gauge")
                lines.append(f"{base_name}{label_str} {value}")

            for key, values in sorted(self._histograms.items()):
                base_name = key.split("{")[0] if "{" in key else key
                label_str = self._format_embedded_labels(key)
                lines.append(f"# TYPE {base_name} histogram")
                if values:
                    lines.append(f"{base_name}_sum{label_str} {sum(values)}")
                    lines.append(f"{base_name}_count{label_str} {len(values)}")
                    sorted_vals = sorted(values)
                    p50_idx = len(sorted_vals) // 2
                    p95_idx = int(len(sorted_vals) * 0.95)
                    p99_idx = int(len(sorted_vals) * 0.99)
                    lines.append(
                        f'{base_name}{{quantile="0.5"{label_str[1:] if label_str else ""}}} {sorted_vals[p50_idx]}'
                    )
                    lines.append(
                        f'{base_name}{{quantile="0.95"{label_str[1:] if label_str else ""}}} {sorted_vals[min(p95_idx, len(sorted_vals) - 1)]}'
                    )
                    lines.append(
                        f'{base_name}{{quantile="0.99"{label_str[1:] if label_str else ""}}} {sorted_vals[min(p99_idx, len(sorted_vals) - 1)]}'
                    )

        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_embedded_labels(key: str) -> str:
        """Extract label string from an internal key that may already contain labels."""
        if "{" in key:
            return "{" + key.split("{", 1)[1]
        return ""

    def _format_labels(self, labels: dict[str, str]) -> str:
        if not labels:
            return ""
        parts = [f'{k}="{v}"' for k, v in sorted(labels.items())]
        return "{" + ",".join(parts) + "}"

    def get_summary(self) -> dict[str, Any]:
        """Return a JSON-friendly summary of all metrics."""
        with self._lock:
            return {
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": {
                    k: {
                        "count": len(v),
                        "sum": sum(v),
                        "avg": sum(v) / len(v) if v else 0,
                        "min": min(v) if v else 0,
                        "max": max(v) if v else 0,
                    }
                    for k, v in self._histograms.items()
                },
            }


# Global singleton
collector = MetricsCollector()
