"""Executed-action developer CLI: ``python -m app.actions.cli <command>``.

Inspect immutable executed actions and the simulated refund ledger. Each reference is a
clearly-synthetic ``SIM-…`` demonstration id; no external processor was ever contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.actions.repository import ExecutedActionRepository, RefundLedgerRepository
from app.db.session import get_sessionmaker
from app.models.order import Order


def _run(coro: object) -> int:
    asyncio.run(coro)  # type: ignore[arg-type]
    return 0


def _money(pence: int | None) -> str:
    return "-" if pence is None else f"£{pence / 100:,.2f}"


def cmd_list(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            rows = await ExecutedActionRepository(session).list_actions(
                limit=args.limit
            )
            if not rows:
                print("no executed actions")
                return
            for a in rows:
                print(
                    f"- {a.id} {a.action_type:28} {a.status.value:10} "
                    f"{_money(a.amount_pence):>10} ref={a.business_effect_reference}"
                )

    return _run(_go())


def cmd_inspect(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            action = await ExecutedActionRepository(session).get(uuid.UUID(args.action))
            if action is None:
                raise SystemExit("executed action not found")
            print(f"action         {action.id}")
            print(f"type / status  {action.action_type} / {action.status.value}")
            print(f"reference      {action.business_effect_reference}")
            print(f"amount         {_money(action.amount_pence)} {action.currency}")
            print(f"approval       {action.approval_request_id}")
            print(f"outbox_job     {action.outbox_job_id}")
            print(f"order          {action.order_id}")
            print(f"result_hash    {action.result_hash}")
            ok = ExecutedActionRepository.verify_result_hash(action)
            print(f"result_valid   {ok}")

    return _run(_go())


def cmd_refund_ledger(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            order = await session.scalar(
                select(Order).where(Order.order_number == args.order)
            )
            if order is None:
                raise SystemExit(f"no order {args.order!r}")
            ledger = RefundLedgerRepository(session)
            entries = await ledger.list_for_order(order.id)
            total = await ledger.refunded_total_pence(order.id)
            print(f"order          {order.order_number} ({order.id})")
            print(f"total paid     {_money(order.total_paid_pence)}")
            print(f"refunded total {_money(total)}")
            for e in entries:
                print(
                    f"- {e.created_at.isoformat()} {_money(e.amount_pence):>10} "
                    f"{e.entry_type.value} ref={e.reference}"
                )

    return _run(_go())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.actions.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="list executed actions")
    listing.add_argument("--limit", type=int, default=25)
    listing.set_defaults(func=cmd_list)

    inspect = sub.add_parser("inspect", help="inspect one executed action")
    inspect.add_argument("action")
    inspect.set_defaults(func=cmd_inspect)

    ledger = sub.add_parser("refund-ledger", help="show an order's refund ledger")
    ledger.add_argument("order")
    ledger.set_defaults(func=cmd_refund_ledger)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
