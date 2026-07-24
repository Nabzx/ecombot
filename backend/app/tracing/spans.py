"""Deterministic, offline tracing (S7).

A minimal span model and tracer that produce a trace per workflow run / outbox job with
child spans for each phase. Spans **never** leave the process to a remote collector: they
export to structured logs, an in-memory collector (tests/eval) or a JSON file. Under the
deterministic seed clock, span timings are reproducible (durations may be zero).

Parent/child is derived from the observability context, so a non-root span always has a
parent — there are no orphan spans by construction.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from app.core.context import current, reset, update
from app.rules.clock import Clock, SystemClock


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start: datetime
    end: datetime | None = None
    attributes: dict[str, object] = field(default_factory=dict)
    status: str = "ok"

    @property
    def duration_ms(self) -> int:
        if self.end is None:
            return 0
        return max(0, int((self.end - self.start).total_seconds() * 1000))

    def as_dict(self) -> dict[str, object]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "attributes": self.attributes,
        }


class SpanExporter(Protocol):
    def export(self, span: Span) -> None: ...


class Tracer:
    """Creates spans under the current observability context and exports them."""

    def __init__(self, exporter: SpanExporter, *, clock: Clock | None = None) -> None:
        self._exporter = exporter
        self._clock = clock or SystemClock()

    def _now(self) -> datetime:
        return self._clock.now()

    @contextmanager
    def trace(
        self, name: str, *, trace_id: str | None = None, **attributes: object
    ) -> Iterator[Span]:
        """Start a root span for a whole trace (parent is None)."""
        tid = trace_id or uuid.uuid4().hex[:16]
        sid = uuid.uuid4().hex[:16]
        token = update(trace_id=tid, span_id=sid)
        span = Span(tid, sid, None, name, self._now(), attributes=dict(attributes))
        try:
            yield span
        except Exception:
            span.status = "error"
            raise
        finally:
            span.end = self._now()
            self._exporter.export(span)
            reset(token)

    @contextmanager
    def span(self, name: str, **attributes: object) -> Iterator[Span]:
        """Start a child span under the current span (never an orphan)."""
        ctx = current()
        tid = ctx.trace_id or uuid.uuid4().hex[:16]
        parent = ctx.span_id
        sid = uuid.uuid4().hex[:16]
        token = update(trace_id=tid, span_id=sid)
        span = Span(tid, sid, parent, name, self._now(), attributes=dict(attributes))
        try:
            yield span
        except Exception:
            span.status = "error"
            raise
        finally:
            span.end = self._now()
            self._exporter.export(span)
            reset(token)
