"""Offline span exporters (S7).

Spans are exported only within the process: to structured logs, an in-memory list (tests
and the evaluation), or a JSON file under the reports directory. There is no network
exporter — tracing never contacts an external collector.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.core.logging import get_logger
from app.tracing.spans import Span, SpanExporter

_logger = get_logger("agentops.tracing")


class LogSpanExporter:
    """Emit each finished span as a structured log record."""

    def export(self, span: Span) -> None:
        _logger.info("span", extra={"span": json.dumps(span.as_dict(), default=str)})


class CollectingExporter:
    """Collect spans in memory (tests / evaluation); trace-completeness is checkable."""

    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def orphan_spans(self) -> list[Span]:
        """Non-root spans whose parent id was never seen as a span in the trace."""
        known = {s.span_id for s in self.spans}
        return [
            s
            for s in self.spans
            if s.parent_id is not None and s.parent_id not in known
        ]

    def as_trace_tree(self) -> list[dict[str, object]]:
        return [s.as_dict() for s in self.spans]


class JsonFileExporter:
    """Append finished spans to a JSON-lines file under a reports directory."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, span: Span) -> None:
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(span.as_dict(), default=str) + "\n")


_default: SpanExporter = LogSpanExporter()


def default_exporter() -> SpanExporter:
    return _default
