"""Outbox developer CLI: ``python -m app.outbox.cli <command>``.

Inspect the durable queue, its attempt history and worker stats, and process a single
job through the exact worker path (``process-one``). PII-safe: identifiers, statuses
and sanitised errors only.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.db.session import get_sessionmaker
from app.outbox.enums import OutboxStatus
from app.outbox.repository import OutboxAttemptRepository, OutboxRepository
from app.outbox.worker import OutboxWorker
from app.rules.clock import seed_reference_clock


def _run(coro: object) -> int:
    asyncio.run(coro)  # type: ignore[arg-type]
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            rows = await OutboxRepository(session).list_jobs(
                status=OutboxStatus(args.status) if args.status else None,
                limit=args.limit,
            )
            if not rows:
                print("no outbox jobs match")
                return
            for job in rows:
                print(
                    f"- {job.id} {job.status.value:14} {job.action_type:28} "
                    f"attempts={job.attempt_count}/{job.maximum_attempts} "
                    f"prio={job.priority} next={job.next_attempt_at.isoformat()}"
                )

    return _run(_go())


def cmd_inspect(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            job = await OutboxRepository(session).get(uuid.UUID(args.job))
            if job is None:
                raise SystemExit("job not found")
            print(f"job            {job.id}")
            print(f"status         {job.status.value}")
            print(f"action_type    {job.action_type}")
            print(f"approval       {job.approval_request_id}")
            print(f"workflow_run   {job.workflow_run_id}")
            print(f"idempotency    {job.idempotency_key}")
            print(f"payload_hash   {job.payload_hash}")
            print(f"attempts       {job.attempt_count}/{job.maximum_attempts}")
            print(f"last_error     {job.last_error_code or '-'}")

    return _run(_go())


def cmd_attempts(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            rows = await OutboxAttemptRepository(session).list_for_job(
                uuid.UUID(args.job)
            )
            for a in rows:
                print(
                    f"- #{a.attempt_number} {a.result_status or 'started':16} "
                    f"worker={a.worker_id} {a.previous_status}->{a.result_status} "
                    f"error={a.error_code or '-'} retryable={a.retryable}"
                )

    return _run(_go())


def cmd_process_one(_: argparse.Namespace) -> int:
    async def _go() -> None:
        worker = OutboxWorker(get_sessionmaker(), clock=seed_reference_clock())
        processed = await worker.run_once()
        if processed == 0:
            print("no due jobs to process")
        else:
            for outcome, count in worker.stats.by_outcome.items():
                print(f"{outcome}: {count}")

    return _run(_go())


def cmd_stats(_: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            counts = await OutboxRepository(session).counts_by_status()
            for status, count in counts.items():
                print(f"{status:16} {count}")

    return _run(_go())


def cmd_release_expired(_: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            released = await OutboxRepository(session).release_expired_leases(
                now=seed_reference_clock().now()
            )
            await session.commit()
            print(f"released {released} expired lease(s)")

    return _run(_go())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.outbox.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show the outbox queue")
    listing.add_argument("--status")
    listing.add_argument("--limit", type=int, default=25)
    listing.set_defaults(func=cmd_list)

    for name, handler in (("inspect", cmd_inspect), ("attempts", cmd_attempts)):
        node = sub.add_parser(name)
        node.add_argument("job")
        node.set_defaults(func=handler)

    sub.add_parser("process-one", help="process one due job").set_defaults(
        func=cmd_process_one
    )
    sub.add_parser("stats", help="counts by status").set_defaults(func=cmd_stats)
    sub.add_parser(
        "release-expired-leases", help="reclaim jobs with expired leases"
    ).set_defaults(func=cmd_release_expired)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
