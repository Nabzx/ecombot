"""Property-style invariants for S6 execution safety.

Pure (Hypothesis) properties on the payload/backoff, plus DB-backed invariants:
completed and dead-letter jobs are never claimed, and a default replay of an executed
v2 run creates no second business effect.
"""

from __future__ import annotations

import uuid

from app.outbox.enums import OutboxStatus
from app.outbox.repository import OutboxRepository
from app.outbox.retry import compute_backoff_seconds
from app.rules.clock import seed_reference_clock
from app.workflows.enums import ReplayMode
from app.workflows.service import ReplayWorkflowRequest, SupportWorkflowService
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from tests.test_outbox_execution import _approved_job, maker  # noqa: F401
from tests.test_outbox_worker import _worker


# --- pure properties ----------------------------------------------------------------
@given(
    attempt=st.integers(min_value=1, max_value=20),
    base=st.floats(min_value=0.5, max_value=10.0),
    cap=st.floats(min_value=10.0, max_value=600.0),
    jitter=st.floats(min_value=0.0, max_value=0.5),
)
def test_backoff_never_exceeds_cap_plus_jitter(
    attempt: int, base: float, cap: float, jitter: float
) -> None:
    delay = compute_backoff_seconds(
        attempt=attempt,
        base_seconds=base,
        max_seconds=cap,
        jitter_ratio=jitter,
        job_id=uuid.uuid4(),
    )
    assert 0.0 <= delay <= cap * (1.0 + jitter) + 1e-9


# --- DB-backed invariants -----------------------------------------------------------
async def test_completed_job_is_never_claimed(
    maker: async_sessionmaker,  # noqa: F811
) -> None:
    job_id, _, _ = await _approved_job(maker)
    await _worker(maker).run_once()  # succeeds → job terminal
    async with maker() as session:
        claimed = await OutboxRepository(session).claim_batch(
            worker_id="w",
            now=seed_reference_clock().now(),
            lease_seconds=60,
            batch_size=10,
        )
        await session.commit()
        assert all(j.id != job_id for j in claimed)
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.SUCCEEDED


async def test_dead_letter_job_is_never_auto_claimed(
    maker: async_sessionmaker,  # noqa: F811
) -> None:
    job_id, _, _ = await _approved_job(maker)
    async with maker() as session:
        await session.execute(
            text("UPDATE outbox_jobs SET status = 'dead_letter' WHERE id = :j"),
            {"j": str(job_id)},
        )
        await session.commit()
    processed = await _worker(maker).run_once()
    assert processed == 0
    async with maker() as session:
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0


async def test_default_replay_creates_no_business_effect(
    maker: async_sessionmaker,  # noqa: F811
) -> None:
    # Execute a refund, then replay the run: replay must not touch money or the queue.
    job_id, _, run_id = await _approved_job(maker)
    await _worker(maker).run_once()
    async with maker() as session:
        before_actions = await session.scalar(
            text("SELECT count(*) FROM executed_actions")
        )
        before_ledger = await session.scalar(
            text("SELECT count(*) FROM refund_ledger_entries")
        )
        before_jobs = await session.scalar(text("SELECT count(*) FROM outbox_jobs"))

    service = SupportWorkflowService(session_factory=maker)
    await service.replay(
        ReplayWorkflowRequest(run_id=run_id, mode=ReplayMode.DETERMINISTIC_MOCK)
    )

    async with maker() as session:
        assert (
            await session.scalar(text("SELECT count(*) FROM executed_actions"))
            == before_actions
        )
        assert (
            await session.scalar(text("SELECT count(*) FROM refund_ledger_entries"))
            == before_ledger
        )
        # A replay never enqueues an outbox job (no approval decisions on replays).
        assert await session.scalar(text("SELECT count(*) FROM outbox_jobs")) == (
            before_jobs
        )
