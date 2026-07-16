"""CLI for inspecting the tool registry and running deterministic demos.

python -m app.tools.cli list-tools
python -m app.tools.cli schema <tool-name>
python -m app.tools.cli run-demo <fixture-id>        # e.g. DEMO-RETURN-DAY-30
python -m app.tools.cli inspect-ticket <reference>   # e.g. TKT-2026-000079
"""

from __future__ import annotations

import argparse
import asyncio
import json

from app.db.session import dispose_engine, get_sessionmaker
from app.repositories.ticket import TicketRepository
from app.rules.clock import seed_reference_clock
from app.rules.service import TicketInspection, inspect_ticket
from app.tools.registry import RESERVED_TOOL_NAMES, get_tool, list_tools


def _list_tools() -> int:
    header = (
        f"{'TOOL':<28}{'PERM':<18}{'RISK':<11}{'RO':<4}{'APPR':<6}{'MODEL':<7}VERSION"
    )
    print(header)
    for tool in list_tools():
        print(
            f"{tool.name:<28}{tool.permission.value:<18}{tool.risk_level.value:<11}"
            f"{'yes' if tool.read_only else 'no':<4}"
            f"{'yes' if tool.approval_required else 'no':<6}"
            f"{'yes' if tool.model_accessible else 'no':<7}{tool.version}"
        )
    print("\nReserved (no handler in S2): " + ", ".join(RESERVED_TOOL_NAMES))
    return 0


def _schema(name: str) -> int:
    tool = get_tool(name)
    if tool is None:
        print(f"Unknown tool: {name}")
        return 1
    print(json.dumps(tool.input_schema(), indent=2))
    return 0


def _print_inspection(result: TicketInspection) -> None:
    print(f"Fixture:            {result.seed_tag or '(none)'}")
    print(f"Ticket:             {result.ticket_reference}  [{result.category.value}]")
    print(f"Injection flag:     {result.injection_flag}")
    print(f"Resolved customer:  {result.resolved_customer_id}")
    print(f"Resolved order:     {result.resolved_order_number}")
    print(f"Order owned by cust:{result.order_belongs_to_customer}")
    print(
        f"Ownership:          {result.ownership.outcome.value} "
        f"{[c.value for c in result.ownership.reason_codes]}"
    )
    if result.category_result is not None:
        cr = result.category_result
        print(
            f"Category rule:      {cr.outcome.value} risk={cr.risk_level.value} "
            f"route={cr.route.value} approval={cr.approval_required}"
        )
        print(f"  reason codes:     {[c.value for c in cr.reason_codes]}")
        if cr.computed:
            print(f"  computed:         {cr.computed}")
    role = result.routing.required_role
    print(
        f"Routing:            {result.routing.outcome.value} "
        f"route={result.routing.route.value} "
        f"role={role.value if role else 'n/a'}"
    )
    print(f"  reason codes:     {[c.value for c in result.routing.reason_codes]}")
    if result.idempotency_key:
        print(f"Idempotency key:    {result.idempotency_key}")


async def _inspect(reference: str) -> int:
    async with get_sessionmaker()() as session:
        try:
            result = await inspect_ticket(session, reference, seed_reference_clock())
        except LookupError as exc:
            print(str(exc))
            return 1
    _print_inspection(result)
    return 0


async def _run_demo(fixture_id: str) -> int:
    async with get_sessionmaker()() as session:
        ticket = await TicketRepository(session).get_by_seed_tag(fixture_id)
        if ticket is None:
            print(f"No fixture with seed tag {fixture_id}")
            return 1
        result = await inspect_ticket(
            session, ticket.ticket_reference, seed_reference_clock()
        )
    _print_inspection(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tools", description="AgentOps tool tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list-tools", help="List registered tools")
    schema_parser = sub.add_parser("schema", help="Print a tool's input JSON schema")
    schema_parser.add_argument("tool_name")
    demo_parser = sub.add_parser(
        "run-demo", help="Run the deterministic layer for a fixture"
    )
    demo_parser.add_argument("fixture_id")
    inspect_parser = sub.add_parser(
        "inspect-ticket", help="Inspect a ticket by reference"
    )
    inspect_parser.add_argument("ticket_reference")
    args = parser.parse_args(argv)

    if args.command == "list-tools":
        return _list_tools()
    if args.command == "schema":
        return _schema(args.tool_name)

    async def _run() -> int:
        try:
            if args.command == "run-demo":
                return await _run_demo(args.fixture_id)
            return await _inspect(args.ticket_reference)
        finally:
            await dispose_engine()

    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
