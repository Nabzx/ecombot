"""Audit developer CLI: ``python -m app.audit.cli <command>``.

Inspect the immutable audit log, verify its hash-chain and follow a correlation id
through the approval → execution journey. PII-safe: identifiers and hashes only.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.audit.repository import AuditRepository
from app.db.session import get_sessionmaker


def _run(coro: object) -> int:
    asyncio.run(coro)  # type: ignore[arg-type]
    return 0


def _line(row: object) -> str:
    r = row
    actor = getattr(r, "actor_user_id", None) or "system"
    return (
        f"#{r.sequence:<5} {r.occurred_at.isoformat()} {r.event_type:26} "  # type: ignore[attr-defined]
        f"by {actor} ({r.actor_role}) [{r.correlation_id}] {r.summary}"  # type: ignore[attr-defined]
    )


def cmd_list(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            rows = await AuditRepository(session).list_events(
                event_type=args.type, limit=args.limit
            )
            if not rows:
                print("no audit events")
                return
            for row in rows:
                print(_line(row))

    return _run(_go())


def cmd_inspect(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            row = await AuditRepository(session).get(uuid.UUID(args.event))
            if row is None:
                raise SystemExit("audit event not found")
            print(_line(row))
            print(f"  subject      {row.subject_type} {row.subject_id}")
            print(f"  previous_hash {row.previous_hash}")
            print(f"  entry_hash    {row.entry_hash}")
            print(f"  metadata      {row.metadata_json}")

    return _run(_go())


def cmd_verify_chain(_: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            result = await AuditRepository(session).verify_chain()
            if result.ok:
                print(f"chain OK: {result.checked} event(s) verified")
            else:
                print(
                    f"chain BROKEN at sequence {result.broken_sequence}: "
                    f"{result.reason}"
                )
                raise SystemExit(1)

    return _run(_go())


def cmd_trace(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            rows = await AuditRepository(session).list_for_correlation(args.correlation)
            if not rows:
                print("no events for that correlation id")
                return
            for row in rows:
                print(_line(row))

    return _run(_go())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.audit.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list recent audit events")
    listing.add_argument("--type")
    listing.add_argument("--limit", type=int, default=50)
    listing.set_defaults(func=cmd_list)

    inspect = sub.add_parser("inspect", help="inspect one audit event")
    inspect.add_argument("event")
    inspect.set_defaults(func=cmd_inspect)

    sub.add_parser("verify-chain", help="verify the audit hash-chain").set_defaults(
        func=cmd_verify_chain
    )

    trace = sub.add_parser("trace", help="events for one correlation id")
    trace.add_argument("correlation")
    trace.set_defaults(func=cmd_trace)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
