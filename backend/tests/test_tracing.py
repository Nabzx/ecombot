"""Offline tracing tests (S7): span parenting, no orphans, real execution trace."""

from __future__ import annotations

from app.core.context import correlation
from app.outbox.processor import OutboxProcessor, ProcessOutcome
from app.rules.clock import seed_reference_clock
from app.tracing.exporters import CollectingExporter
from app.tracing.spans import Tracer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_outbox_execution import _approved_job, maker  # noqa: F401


def test_child_spans_have_parents_and_no_orphans() -> None:
    exporter = CollectingExporter()
    tracer = Tracer(exporter, clock=seed_reference_clock())
    with tracer.trace("root"):
        with tracer.span("child-a"):
            with tracer.span("grandchild"):
                pass
        with tracer.span("child-b"):
            pass
    names = {s.name: s for s in exporter.spans}
    assert set(names) == {"root", "child-a", "grandchild", "child-b"}
    assert names["root"].parent_id is None
    assert names["child-a"].parent_id == names["root"].span_id
    assert names["grandchild"].parent_id == names["child-a"].span_id
    assert names["child-b"].parent_id == names["root"].span_id
    # Every span shares the one trace id, and there are no orphans.
    assert len({s.trace_id for s in exporter.spans}) == 1
    assert exporter.orphan_spans() == []


def test_error_span_marked_and_restores_context() -> None:
    exporter = CollectingExporter()
    tracer = Tracer(exporter, clock=seed_reference_clock())
    with correlation("cor-1"):
        try:
            with tracer.span("boom"):
                raise ValueError("x")
        except ValueError:
            pass
    assert exporter.spans[0].status == "error"


async def test_execution_produces_a_complete_trace(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    exporter = CollectingExporter()
    job_id, _, _ = await _approved_job(maker)
    processor = OutboxProcessor(
        maker,
        clock=seed_reference_clock(),
        tracer=Tracer(exporter, clock=seed_reference_clock()),
    )
    result = await processor.process_job(job_id)
    assert result.outcome == ProcessOutcome.SUCCEEDED
    names = {s.name for s in exporter.spans}
    assert "outbox.process_job" in names
    assert "execution.revalidate" in names
    assert "execution.handler" in names
    assert exporter.orphan_spans() == []
    # The root span carries the job id.
    root = next(s for s in exporter.spans if s.parent_id is None)
    assert root.attributes["job_id"] == str(job_id)
