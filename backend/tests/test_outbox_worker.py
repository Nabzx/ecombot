"""Worker-loop, concurrency and retry-authorisation tests (S6).

Exercises the real ``OutboxWorker`` claim/process loop, competing workers (no duplicate
effect), lease reclamation, and the Supervisor retry-authorisation path back to success.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.actions.errors import ExecutionError, ExecutionErrorCode, technical
from app.approvals.enums import ApprovalDecisionType, ApprovalStatus
from app.approvals.repository import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
)
from app.approvals.service import ApprovalService, RetryApprovalRequest
from app.outbox.enums import OutboxStatus
from app.outbox.payload import OutboxJobData
from app.outbox.repository import OutboxRepository
from app.outbox.worker import OutboxWorker
from app.rules.clock import seed_reference_clock
from app.workflows.enums import WorkflowState
from app.workflows.repository import WorkflowRepository
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_outbox_execution import (  # reuse the execution harness
    _approved_job,
    _second_supervisor,
    maker,  # noqa: F401  (pytest fixture)
)

# The deterministic seed reference time the worker's clock reports. Test SQL must use
# timestamps relative to this, not the DB wall clock, so "due"/"expired" line up.
SEED_NOW = seed_reference_clock().now()
BEFORE_SEED = SEED_NOW - timedelta(days=1)


def _worker(
    factory: async_sessionmaker[AsyncSession],
    *,
    worker_id: str = "worker-a",
    injector: object | None = None,
) -> OutboxWorker:
    from app.core.config import Settings

    settings = Settings(worker_id=worker_id, database_url="postgresql://x/y")
    return OutboxWorker(
        factory,
        settings=settings,
        clock=seed_reference_clock(),
        failure_injector=injector,  # type: ignore[arg-type]
    )


async def test_worker_empty_queue_processes_nothing(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    processed = await _worker(maker).run_once()
    assert processed == 0


async def test_worker_claims_and_processes(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    job_id, _, run_id = await _approved_job(maker)
    processed = await _worker(maker).run_once()
    assert processed == 1
    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.SUCCEEDED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None and run.current_state == WorkflowState.ACTION_SUCCEEDED


async def test_competing_workers_do_not_duplicate_effect(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    job_id, _, _ = await _approved_job(maker)
    a = _worker(maker, worker_id="worker-a")
    b = _worker(maker, worker_id="worker-b")
    # Two workers tick concurrently; exactly one applies the effect.
    results = await asyncio.gather(a.run_once(), b.run_once())
    assert sorted(results) == [0, 1]  # only one claimed the single job
    async with maker() as session:
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 1
        assert (
            await session.scalar(text("SELECT count(*) FROM refund_ledger_entries"))
            == 1
        )


async def test_expired_lease_is_reclaimable(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    job_id, _, _ = await _approved_job(maker)
    # Simulate a crashed worker: the job is claimed with an already-expired lease.
    async with maker() as session:
        await session.execute(
            text(
                "UPDATE outbox_jobs SET status = 'claimed', claimed_by = 'dead', "
                "lease_expires_at = :past WHERE id = :j"
            ),
            {"j": str(job_id), "past": BEFORE_SEED},
        )
        await session.commit()
    # A fresh worker reclaims and completes it.
    processed = await _worker(maker).run_once()
    assert processed == 1
    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.SUCCEEDED


# --- retry authorisation ------------------------------------------------------------
def _tech_injector() -> object:
    def injector(_payload: OutboxJobData, _attempt: int) -> ExecutionError | None:
        return technical(ExecutionErrorCode.INJECTED_FAILURE, "boom")

    return injector


async def _drive_to_dead_letter(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[object, object]:
    job_id, approval_id, run_id = await _approved_job(factory)
    worker = _worker(factory, injector=_tech_injector())
    for _ in range(6):
        async with factory() as session:
            job = await OutboxRepository(session).get(job_id)
            assert job is not None
            if job.status == OutboxStatus.DEAD_LETTER:
                break
            await session.execute(
                text(
                    "UPDATE outbox_jobs SET status = 'pending', "
                    "next_attempt_at = :seed_now "
                    "WHERE id = :j AND status = 'retry_scheduled'"
                ),
                {"j": str(job_id), "seed_now": SEED_NOW},
            )
            await session.commit()
        await worker.run_once()
    return job_id, (approval_id, run_id)


async def test_supervisor_retry_authorisation_recovers_dead_letter(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    job_id, (approval_id, run_id) = await _drive_to_dead_letter(maker)
    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.DEAD_LETTER
        approval = await ApprovalRequestRepository(session).get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.EXECUTION_FAILED

        supervisor = await _second_supervisor(session)
        result = await ApprovalService(session, clock=seed_reference_clock()).retry(
            approval_id,
            RetryApprovalRequest(reason="transient blip, retrying"),
            supervisor,  # type: ignore[arg-type]
        )
        await session.commit()
        assert result.status == ApprovalStatus.EXECUTION_PENDING
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.PENDING
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.current_state == WorkflowState.APPROVED_PENDING_EXECUTION
        decisions = await ApprovalDecisionRepository(session).list_for_request(
            approval_id
        )
        assert decisions[-1].decision == ApprovalDecisionType.RETRY_AUTHORISED

    # A clean worker (no injector) now completes the retried job successfully.
    processed = await _worker(maker).run_once()
    assert processed == 1
    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.SUCCEEDED
        approval = await ApprovalRequestRepository(session).get(approval_id)
        assert approval is not None and approval.status == ApprovalStatus.EXECUTED
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 1
