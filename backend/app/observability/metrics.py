"""In-process metrics with Prometheus text rendering (S7).

A dependency-free registry of counters, gauges and histograms. Metrics live in-process
and are scraped at ``GET /metrics``; nothing is pushed to a gateway. Labels carry
only safe, low-cardinality values (states, outcomes) — never PII, ids or secrets.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

# Default histogram buckets (seconds) for request/processing latency.
DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_LabelKey = tuple[tuple[str, str], ...]


def _labels_key(labels: dict[str, str]) -> _LabelKey:
    return tuple(sorted(labels.items()))


def _render_labels(key: _LabelKey) -> str:
    if not key:
        return ""
    inner = ",".join(f'{name}="{_escape(value)}"' for name, value in key)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


@dataclass
class _Counter:
    help: str
    values: dict[_LabelKey, float] = field(default_factory=dict)

    def inc(self, amount: float, labels: dict[str, str]) -> None:
        key = _labels_key(labels)
        self.values[key] = self.values.get(key, 0.0) + amount


@dataclass
class _Gauge:
    help: str
    values: dict[_LabelKey, float] = field(default_factory=dict)

    def set(self, value: float, labels: dict[str, str]) -> None:
        self.values[_labels_key(labels)] = value


@dataclass
class _Histogram:
    help: str
    buckets: tuple[float, ...]
    counts: dict[_LabelKey, list[int]] = field(default_factory=dict)
    sums: dict[_LabelKey, float] = field(default_factory=dict)
    totals: dict[_LabelKey, int] = field(default_factory=dict)

    def observe(self, value: float, labels: dict[str, str]) -> None:
        key = _labels_key(labels)
        counts = self.counts.setdefault(key, [0] * len(self.buckets))
        for i, bound in enumerate(self.buckets):
            if value <= bound:
                counts[i] += 1
        self.sums[key] = self.sums.get(key, 0.0) + value
        self.totals[key] = self.totals.get(key, 0) + 1


class MetricsRegistry:
    """A small, thread-safe metrics registry rendered in Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, _Counter] = {}
        self._gauges: dict[str, _Gauge] = {}
        self._histograms: dict[str, _Histogram] = {}

    def counter(self, name: str, help_text: str = "") -> None:
        self._counters.setdefault(name, _Counter(help_text))

    def gauge(self, name: str, help_text: str = "") -> None:
        self._gauges.setdefault(name, _Gauge(help_text))

    def histogram(
        self,
        name: str,
        help_text: str = "",
        buckets: tuple[float, ...] = DEFAULT_BUCKETS,
    ) -> None:
        self._histograms.setdefault(name, _Histogram(help_text, buckets))

    def inc(self, name: str, amount: float = 1.0, **labels: str) -> None:
        with self._lock:
            self.counter(name)
            self._counters[name].inc(amount, labels)

    def set_gauge(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self.gauge(name)
            self._gauges[name].set(value, labels)

    def observe(self, name: str, value: float, **labels: str) -> None:
        with self._lock:
            self.histogram(name)
            self._histograms[name].observe(value, labels)

    def get_counter(self, name: str, **labels: str) -> float:
        counter = self._counters.get(name)
        if counter is None:
            return 0.0
        return counter.values.get(_labels_key(labels), 0.0)

    def render(self) -> str:
        lines: list[str] = []
        with self._lock:
            for name, counter in sorted(self._counters.items()):
                lines.append(f"# HELP {name} {counter.help}")
                lines.append(f"# TYPE {name} counter")
                for key, value in sorted(counter.values.items()):
                    lines.append(f"{name}{_render_labels(key)} {value}")
            for name, gauge in sorted(self._gauges.items()):
                lines.append(f"# HELP {name} {gauge.help}")
                lines.append(f"# TYPE {name} gauge")
                for key, value in sorted(gauge.values.items()):
                    lines.append(f"{name}{_render_labels(key)} {value}")
            for name, hist in sorted(self._histograms.items()):
                lines.append(f"# HELP {name} {hist.help}")
                lines.append(f"# TYPE {name} histogram")
                for key, counts in sorted(hist.counts.items()):
                    cumulative = 0
                    for bound, count in zip(hist.buckets, counts, strict=True):
                        cumulative += count
                        label = dict(key) | {"le": _fmt(bound)}
                        lines.append(
                            f"{name}_bucket{_render_labels(_labels_key(label))} "
                            f"{cumulative}"
                        )
                    total = hist.totals.get(key, 0)
                    inf_label = dict(key) | {"le": "+Inf"}
                    lines.append(
                        f"{name}_bucket{_render_labels(_labels_key(inf_label))} {total}"
                    )
                    lines.append(
                        f"{name}_sum{_render_labels(key)} {hist.sums.get(key, 0.0)}"
                    )
                    lines.append(f"{name}_count{_render_labels(key)} {total}")
        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


def _fmt(value: float) -> str:
    return repr(value)


# Metric names (safety counters mirror the hard gates and must stay 0).
M_HTTP_REQUESTS = "agentops_http_requests_total"
M_HTTP_LATENCY = "agentops_http_request_seconds"
M_APPROVAL_DECISIONS = "agentops_approval_decisions_total"
M_EXECUTIONS = "agentops_action_executions_total"
M_OUTBOX_JOBS = "agentops_outbox_jobs_total"
M_UNSAFE = "agentops_unsafe_outcomes_total"
M_BREAKER_STATE = "agentops_circuit_breaker_state"

_registry = MetricsRegistry()


def registry() -> MetricsRegistry:
    return _registry
