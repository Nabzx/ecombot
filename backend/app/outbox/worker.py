"""The durable outbox worker: ``python -m app.outbox.worker`` (S6).

A plain async loop — no HTTP server, no exposed port. Each tick claims a bounded batch
of due jobs with ``FOR UPDATE SKIP LOCKED`` (competing workers never collide), processes
each through the exactly-once :class:`OutboxProcessor`, and sleeps on an empty queue.
It holds no transaction while sleeping, recovers safely after termination (leases
expire and jobs are reclaimed), and never approves anything. Logs carry job / worker
ids and never PII or secrets.
"""

from __future__ import annotations

import asyncio
import signal
import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import get_sessionmaker
from app.observability.metrics import M_EXECUTIONS, registry
from app.outbox.processor import FailureInjector, OutboxProcessor, ProcessResult
from app.outbox.repository import OutboxRepository
from app.rules.clock import Clock, SystemClock

logger = get_logger("agentops.outbox.worker")


@dataclass
class WorkerStats:
    ticks: int = 0
    claimed: int = 0
    processed: int = 0
    by_outcome: dict[str, int] = field(default_factory=dict)

    def record(self, result: ProcessResult) -> None:
        self.processed += 1
        key = result.outcome.value
        self.by_outcome[key] = self.by_outcome.get(key, 0) + 1


class OutboxWorker:
    """Claims and processes durable outbox jobs until asked to stop."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
        failure_injector: FailureInjector | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings or get_settings()
        self._clock = clock or SystemClock()
        self._processor = OutboxProcessor(
            session_factory,
            settings=self._settings,
            clock=self._clock,
            failure_injector=failure_injector,
        )
        self._stop = asyncio.Event()
        self.stats = WorkerStats()

    def request_stop(self) -> None:
        self._stop.set()

    async def _claim_batch(self) -> list[uuid.UUID]:
        """Claim a bounded batch in a short committed transaction."""
        async with self._factory() as session:
            repo = OutboxRepository(session)
            jobs = await repo.claim_batch(
                worker_id=self._settings.worker_id,
                now=self._clock.now(),
                lease_seconds=self._settings.outbox_lease_seconds,
                batch_size=self._settings.worker_batch_size,
            )
            job_ids = [job.id for job in jobs]
            await session.commit()
        return job_ids

    async def run_once(self) -> int:
        """One tick: claim a batch and process each job. Returns jobs processed."""
        self.stats.ticks += 1
        job_ids = await self._claim_batch()
        self.stats.claimed += len(job_ids)
        for job_id in job_ids:
            result = await self._processor.process_job(job_id)
            self.stats.record(result)
            registry().inc(M_EXECUTIONS, outcome=result.outcome.value)
            logger.info(
                "outbox_job_processed",
                extra={
                    "job_id": str(job_id),
                    "worker_id": self._settings.worker_id,
                    "outcome": result.outcome.value,
                    "error_code": result.error_code,
                },
            )
        return len(job_ids)

    async def run_forever(self) -> None:
        logger.info(
            "outbox_worker_started",
            extra={"worker_id": self._settings.worker_id},
        )
        while not self._stop.is_set():
            try:
                processed = await self.run_once()
            except Exception:  # pragma: no cover - defensive; keep the loop alive
                logger.exception("outbox_worker_tick_failed")
                processed = 0
            if processed == 0:
                # No transaction is held across the sleep.
                try:
                    await asyncio.wait_for(
                        self._stop.wait(),
                        timeout=self._settings.worker_poll_interval_seconds,
                    )
                except TimeoutError:
                    pass
        logger.info(
            "outbox_worker_stopped",
            extra={"worker_id": self._settings.worker_id, **self.stats.by_outcome},
        )


def _build_injector(settings: Settings) -> FailureInjector | None:
    """Optional deterministic failure injection from settings (tests/eval only)."""
    mode = settings.outbox_failure_injection.strip()
    if not mode:
        return None
    from app.actions.errors import ExecutionErrorCode, technical

    def injector(_payload: object, attempt: int) -> object:
        # "technical:N" fails the first N attempts; "technical" fails every attempt.
        parts = mode.split(":")
        if parts[0] != "technical":
            return None
        if len(parts) == 2 and attempt > int(parts[1]):
            return None
        return technical(ExecutionErrorCode.INJECTED_FAILURE, "injected failure")

    return injector  # type: ignore[return-value]


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)
    if not settings.worker_enabled:
        logger.info("outbox_worker_disabled")
        return
    worker = OutboxWorker(
        get_sessionmaker(),
        settings=settings,
        failure_injector=_build_injector(settings),
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with_suppress = getattr(loop, "add_signal_handler", None)
        if with_suppress is not None:
            try:
                loop.add_signal_handler(sig, worker.request_stop)
            except NotImplementedError:  # pragma: no cover - platform dependent
                pass
    await worker.run_forever()


if __name__ == "__main__":  # pragma: no cover - process entry point
    asyncio.run(_main())
