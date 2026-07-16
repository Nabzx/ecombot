"""Command-line entrypoint for seeding and verification.

Usage (inside the backend environment)::

    python -m app.seeds.cli seed
    python -m app.seeds.cli stats
    python -m app.seeds.cli verify
    python -m app.seeds.cli reset --yes
    python -m app.seeds.cli reseed --yes

``reset`` and ``reseed`` are destructive and development-only; they require ``--yes``.
``verify`` exits non-zero when integrity issues are found.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.db.session import dispose_engine, get_sessionmaker
from app.seeds.runner import SeedError, SeedStats, gather_stats, reseed, reset, seed
from app.seeds.verify import verify_data


def _print_stats(stats: SeedStats) -> None:
    print("Seed statistics:")
    for table, count in stats.counts.items():
        print(f"  {table:<16} {count}")
    print(f"  adversarial tickets: {stats.adversarial_tickets}")
    print(f"  demo fixtures:       {stats.demo_fixtures}")
    print("  orders by status:")
    for status, count in stats.orders_by_status.items():
        print(f"    {status:<20} {count}")
    print("  tickets by category:")
    for category, count in stats.tickets_by_category.items():
        print(f"    {category:<24} {count}")
    print("  policy versions by status:")
    for status, count in stats.policy_versions_by_status.items():
        print(f"    {status:<12} {count}")


async def _cmd_seed() -> int:
    async with get_sessionmaker()() as session:
        stats = await seed(session)
    _print_stats(stats)
    return 0


async def _cmd_stats() -> int:
    async with get_sessionmaker()() as session:
        stats = await gather_stats(session)
    _print_stats(stats)
    return 0


async def _cmd_reset() -> int:
    async with get_sessionmaker()() as session:
        await reset(session)
    print("Database reset: all domain data removed.")
    return 0


async def _cmd_reseed() -> int:
    async with get_sessionmaker()() as session:
        stats = await reseed(session)
    print("Database reset and reseeded.")
    _print_stats(stats)
    return 0


async def _cmd_verify() -> int:
    async with get_sessionmaker()() as session:
        issues = await verify_data(session)
    if issues:
        print(f"Data integrity check FAILED with {len(issues)} issue(s):")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    print("Data integrity check passed.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="seeds", description="AgentOps seed tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("seed", help="Seed an empty database")
    sub.add_parser("stats", help="Show seed statistics")
    sub.add_parser("verify", help="Verify data integrity (non-zero exit on failure)")
    reset_parser = sub.add_parser("reset", help="DEV ONLY: remove all domain data")
    reset_parser.add_argument("--yes", action="store_true", help="confirm destruction")
    reseed_parser = sub.add_parser("reseed", help="DEV ONLY: reset then seed")
    reseed_parser.add_argument("--yes", action="store_true", help="confirm destruction")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.command in {"reset", "reseed"} and not args.yes:
        print(
            f"'{args.command}' is destructive and development-only. "
            f"Re-run with --yes to confirm.",
            file=sys.stderr,
        )
        return 2

    handlers = {
        "seed": _cmd_seed,
        "stats": _cmd_stats,
        "verify": _cmd_verify,
        "reset": _cmd_reset,
        "reseed": _cmd_reseed,
    }

    async def _run() -> int:
        try:
            return await handlers[args.command]()
        except SeedError as exc:
            print(f"Seed error: {exc}", file=sys.stderr)
            return 1
        finally:
            await dispose_engine()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
