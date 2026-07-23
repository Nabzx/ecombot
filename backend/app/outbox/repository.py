"""Typed async repositories for the durable outbox and its attempt history (S6).

Claiming uses ``SELECT ... FOR UPDATE SKIP LOCKED`` so competing workers never contend
for the same job, ordered deterministically (priority, due time, age, then id).
Terminal jobs are never reclaimed, leases expire, and every method returns typed rows —
raw SQLAlchemy errors never leak to callers.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.outbox import OutboxJob
from app.models.outbox_attempt import OutboxAttempt
from app.outbox.enums import CLAIMABLE_STATUSES, UNCLAIMABLE_STATUSES, OutboxStatus


class OutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- creation / lookup ----------------------------------------------------------
    async def create(self, job: OutboxJob) -> OutboxJob:
        self._session.add(job)
        await self._session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> OutboxJob | None:
        return await self._session.get(OutboxJob, job_id)

    async def get_for_update(self, job_id: uuid.UUID) -> OutboxJob | None:
        stmt = select(OutboxJob).where(OutboxJob.id == job_id).with_for_update()
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_idempotency_key(self, key: str) -> OutboxJob | None:
        stmt = select(OutboxJob).where(OutboxJob.idempotency_key == key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_approval(self, approval_id: uuid.UUID) -> OutboxJob | None:
        stmt = select(OutboxJob).where(OutboxJob.approval_request_id == approval_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_jobs(
        self,
        *,
        status: OutboxStatus | None = None,
        workflow_run_id: uuid.UUID | None = None,
        action_type: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[OutboxJob]:
        stmt: Select[tuple[OutboxJob]] = select(OutboxJob)
        if status is not None:
            stmt = stmt.where(OutboxJob.status == status)
        if workflow_run_id is not None:
            stmt = stmt.where(OutboxJob.workflow_run_id == workflow_run_id)
        if action_type is not None:
            stmt = stmt.where(OutboxJob.action_type == action_type)
        # Deterministic pagination: newest first, id tie-break.
        stmt = (
            stmt.order_by(OutboxJob.created_at.desc(), OutboxJob.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars())

    # -- claiming -------------------------------------------------------------------
    async def claim_batch(
        self, *, worker_id: str, now: datetime, lease_seconds: int, batch_size: int
    ) -> list[OutboxJob]:
        """Claim up to ``batch_size`` due, unlocked jobs and lease them to a worker.

        A job is claimable when it is pending/retry_scheduled and due, or when a prior
        lease has expired (safe reclamation). Terminal jobs are never returned.
        """
        stmt = (
            select(OutboxJob)
            .where(
                OutboxJob.status.in_(tuple(CLAIMABLE_STATUSES)),
                OutboxJob.next_attempt_at <= now,
                (
                    OutboxJob.lease_expires_at.is_(None)
                    | (OutboxJob.lease_expires_at <= now)
                ),
            )
            .order_by(
                OutboxJob.priority.desc(),
                OutboxJob.next_attempt_at.asc(),
                OutboxJob.created_at.asc(),
                OutboxJob.id.asc(),
            )
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
        jobs = list((await self._session.execute(stmt)).scalars())
        lease_until = now + timedelta(seconds=lease_seconds)
        for job in jobs:
            job.status = OutboxStatus.CLAIMED
            job.claimed_at = now
            job.claimed_by = worker_id
            job.lease_expires_at = lease_until
        await self._session.flush()
        return jobs

    async def claim_next(
        self, *, worker_id: str, now: datetime, lease_seconds: int
    ) -> OutboxJob | None:
        jobs = await self.claim_batch(
            worker_id=worker_id, now=now, lease_seconds=lease_seconds, batch_size=1
        )
        return jobs[0] if jobs else None

    async def renew_lease(
        self, job: OutboxJob, *, now: datetime, lease_seconds: int
    ) -> None:
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        await self._session.flush()

    async def release_expired_leases(self, *, now: datetime, limit: int = 100) -> int:
        """Reset claimed/processing jobs whose lease has expired back to pending."""
        stmt = (
            select(OutboxJob)
            .where(
                OutboxJob.status.in_((OutboxStatus.CLAIMED, OutboxStatus.PROCESSING)),
                OutboxJob.lease_expires_at.is_not(None),
                OutboxJob.lease_expires_at <= now,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        jobs = list((await self._session.execute(stmt)).scalars())
        for job in jobs:
            job.status = OutboxStatus.PENDING
            job.claimed_at = None
            job.claimed_by = None
            job.lease_expires_at = None
        await self._session.flush()
        return len(jobs)

    # -- state transitions ----------------------------------------------------------
    async def mark_processing(self, job: OutboxJob) -> None:
        job.status = OutboxStatus.PROCESSING
        await self._session.flush()

    async def mark_succeeded(self, job: OutboxJob, *, now: datetime) -> None:
        job.status = OutboxStatus.SUCCEEDED
        job.completed_at = now
        job.lease_expires_at = None
        job.last_error_code = None
        job.last_error_message = None
        await self._session.flush()

    async def schedule_retry(
        self,
        job: OutboxJob,
        *,
        next_attempt_at: datetime,
        error_code: str,
        error_message: str,
    ) -> None:
        job.status = OutboxStatus.RETRY_SCHEDULED
        job.attempt_count += 1
        job.next_attempt_at = next_attempt_at
        job.claimed_at = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.last_error_code = error_code
        job.last_error_message = error_message
        await self._session.flush()

    async def mark_failed(
        self,
        job: OutboxJob,
        *,
        now: datetime,
        error_code: str,
        error_message: str,
    ) -> None:
        job.status = OutboxStatus.FAILED
        job.attempt_count += 1
        job.completed_at = now
        job.claimed_at = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.last_error_code = error_code
        job.last_error_message = error_message
        await self._session.flush()

    async def mark_dead_letter(
        self,
        job: OutboxJob,
        *,
        now: datetime,
        error_code: str,
        error_message: str,
    ) -> None:
        job.status = OutboxStatus.DEAD_LETTER
        job.attempt_count += 1
        job.dead_lettered_at = now
        job.claimed_at = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.last_error_code = error_code
        job.last_error_message = error_message
        await self._session.flush()

    async def cancel(self, job: OutboxJob, *, now: datetime, reason: str) -> None:
        job.status = OutboxStatus.CANCELLED
        job.completed_at = now
        job.claimed_at = None
        job.claimed_by = None
        job.lease_expires_at = None
        job.last_error_code = "cancelled"
        job.last_error_message = reason
        await self._session.flush()

    async def reset_for_retry(
        self, job: OutboxJob, *, now: datetime, maximum_attempts: int
    ) -> None:
        """Return a failed/dead-lettered job to pending for an authorised retry."""
        job.status = OutboxStatus.PENDING
        job.next_attempt_at = now
        job.maximum_attempts = job.attempt_count + maximum_attempts
        job.completed_at = None
        job.dead_lettered_at = None
        job.claimed_at = None
        job.claimed_by = None
        job.lease_expires_at = None
        await self._session.flush()

    # -- statistics -----------------------------------------------------------------
    async def counts_by_status(self) -> dict[str, int]:
        stmt = select(OutboxJob.status, func.count()).group_by(OutboxJob.status)
        rows = (await self._session.execute(stmt)).all()
        counts = {status.value: 0 for status in OutboxStatus}
        for status, count in rows:
            counts[status.value] = count
        return counts

    @staticmethod
    def is_terminal(status: OutboxStatus) -> bool:
        return status in UNCLAIMABLE_STATUSES


class OutboxAttemptRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def next_attempt_number(self, job_id: uuid.UUID) -> int:
        stmt = select(func.coalesce(func.max(OutboxAttempt.attempt_number), 0)).where(
            OutboxAttempt.outbox_job_id == job_id
        )
        current = (await self._session.execute(stmt)).scalar_one()
        return int(current) + 1

    async def start(
        self,
        *,
        job_id: uuid.UUID,
        attempt_number: int,
        worker_id: str,
        previous_status: str,
        lease_expires_at: datetime | None,
        now: datetime,
    ) -> OutboxAttempt:
        attempt = OutboxAttempt(
            outbox_job_id=job_id,
            attempt_number=attempt_number,
            worker_id=worker_id,
            previous_status=previous_status,
            lease_expires_at=lease_expires_at,
            started_at=now,
            created_at=now,
        )
        self._session.add(attempt)
        await self._session.flush()
        return attempt

    async def finish(
        self,
        attempt: OutboxAttempt,
        *,
        result_status: str,
        now: datetime,
        error_code: str | None = None,
        error_message: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        attempt.result_status = result_status
        attempt.error_code = error_code
        attempt.error_message = error_message
        attempt.retryable = retryable
        attempt.finished_at = now
        attempt.duration_ms = max(
            0, int((now - attempt.started_at).total_seconds() * 1000)
        )
        await self._session.flush()

    async def list_for_job(self, job_id: uuid.UUID) -> list[OutboxAttempt]:
        stmt = (
            select(OutboxAttempt)
            .where(OutboxAttempt.outbox_job_id == job_id)
            .order_by(OutboxAttempt.attempt_number.asc())
        )
        return list((await self._session.execute(stmt)).scalars())
