"""Workflow developer CLI: ``python -m app.workflows.cli <command>``.

Start, run, inspect, resume, cancel and replay support-ticket workflows against the
seeded data with the deterministic mock provider. Output is redacted — no unredacted
PII, secrets or hidden reasoning.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import func, select

from app.db.session import get_sessionmaker
from app.models.ticket import Ticket
from app.models.workflow import WorkflowRun
from app.workflows.definition import (
    STATE_HANDLERS,
    TRANSITIONS,
    WORKFLOW_NAME,
    WORKFLOW_VERSION,
)
from app.workflows.repository import WorkflowRepository
from app.workflows.results import WorkflowRunResult
from app.workflows.service import (
    CancelWorkflowRequest,
    ReplayWorkflowRequest,
    ResumeWorkflowRequest,
    StartWorkflowRequest,
    SupportWorkflowService,
)

DEMO_FIXTURES = (
    "DEMO-TRACKING-001",
    "DEMO-REFUND-APPROVAL-001",
    "DEMO-PROMPT-INJECTION-001",
    "DEMO-CROSS-CUSTOMER-001",
    "DEMO-RETURN-DAY-30",
    "DEMO-RETURN-DAY-31",
)


def _service() -> SupportWorkflowService:
    return SupportWorkflowService(session_factory=get_sessionmaker())


def _print_result(result: WorkflowRunResult) -> None:
    print(f"run_id            {result.run_id}")
    print(f"ticket            {result.ticket_reference} ({result.ticket_id})")
    print(f"workflow          {result.workflow_name}@{result.workflow_version}")
    print(f"state / status    {result.state.value} / {result.status.value}")
    print(f"steps / cps       {result.step_count} / {result.checkpoint_count}")
    print(f"classification    {result.classification}")
    print(f"risk / route      {result.risk_level} / {result.recommended_route}")
    print(f"proposed_action   {result.proposed_action}")
    print(f"approval_required {result.approval_required} role={result.required_role}")
    print(f"citations         {result.citation_ids}")
    if result.warnings:
        print(f"warnings          {result.warnings}")
    if result.missing_information:
        print(f"missing_info      {result.missing_information}")
    if result.failure_code:
        print(f"failure           {result.failure_code}: {result.failure_message}")
    print(f"retry / resume    {result.retry_count} / {result.resume_count}")
    if result.replay_source_run_id:
        print(f"replay_source     {result.replay_source_run_id}")


async def _ticket_ref_for_fixture(fixture: str) -> str:
    async with get_sessionmaker()() as session:
        ticket = await session.scalar(select(Ticket).where(Ticket.seed_tag == fixture))
        if ticket is None:
            raise SystemExit(f"no seeded ticket for fixture {fixture!r}")
        return ticket.ticket_reference


# --- commands ----------------------------------------------------------------------
def cmd_list_definitions(_: argparse.Namespace) -> int:
    print(f"workflow          {WORKFLOW_NAME}@{WORKFLOW_VERSION}")
    print(f"active steps      {len(STATE_HANDLERS)}")
    for state, spec in TRANSITIONS.items():
        dests = ", ".join(sorted(d.value for d in spec.destinations))
        print(f"  {state.value:24} [{spec.handler}] -> {dests}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    result = asyncio.run(
        _service().start(StartWorkflowRequest(ticket_reference=args.ticket))
    )
    _print_result(result)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    result = asyncio.run(_service().run(uuid.UUID(args.run_id)))
    _print_result(result)
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    result = asyncio.run(_service().summary(uuid.UUID(args.run_id)))
    _print_result(result)
    return 0


def cmd_steps(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            steps = await WorkflowRepository(session).list_steps(uuid.UUID(args.run_id))
            for step in steps:
                dest = step.destination_state.value if step.destination_state else "-"
                print(
                    f"  {step.step_index:>2} {step.step_name:22} "
                    f"{step.source_state.value:20} -> {dest:20} "
                    f"[{step.status.value}] attempt={step.attempt} "
                    f"models={len(step.model_call_ids)} tools={len(step.tool_call_ids)}"
                )

    asyncio.run(_go())
    return 0


def cmd_checkpoints(args: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            cps = await WorkflowRepository(session).list_checkpoints(
                uuid.UUID(args.run_id)
            )
            for cp in cps:
                print(
                    f"  {cp.step_index:>2} {cp.state.value:22} "
                    f"hash={cp.snapshot_hash[:12]} schema={cp.state_schema_version}"
                )

    asyncio.run(_go())
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    result = asyncio.run(
        _service().resume(
            ResumeWorkflowRequest(run_id=uuid.UUID(args.run_id), reason=args.reason)
        )
    )
    _print_result(result)
    return 0


def cmd_cancel(args: argparse.Namespace) -> int:
    result = asyncio.run(
        _service().cancel(
            CancelWorkflowRequest(run_id=uuid.UUID(args.run_id), reason=args.reason)
        )
    )
    _print_result(result)
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    from app.workflows.enums import ReplayMode

    result = asyncio.run(
        _service().replay(
            ReplayWorkflowRequest(
                run_id=uuid.UUID(args.run_id), mode=ReplayMode(args.mode)
            )
        )
    )
    print("=== replay ===")
    _print_result(result.replay)
    print("\n=== diff (source vs replay) ===")
    for name, entry in result.diff.fields.items():
        marker = "=" if entry["source"] == entry["replay"] else "≠"
        print(f"  {marker} {name:24} {entry['source']} -> {entry['replay']}")
    print(f"identical: {result.diff.identical}")
    return 0


def cmd_run_demo(args: argparse.Namespace) -> int:
    ref = asyncio.run(_ticket_ref_for_fixture(args.fixture))
    print(f"=== workflow demo {args.fixture} (ticket {ref}) ===")
    result = asyncio.run(_service().start(StartWorkflowRequest(ticket_reference=ref)))
    _print_result(result)
    return 0


def cmd_stats(_: argparse.Namespace) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            total = await session.scalar(select(func.count()).select_from(WorkflowRun))
            print(f"workflow_runs total  {total}")
            rows = await session.execute(
                select(WorkflowRun.current_state, func.count())
                .group_by(WorkflowRun.current_state)
                .order_by(WorkflowRun.current_state)
            )
            for state, count in rows:
                print(f"  {state.value:24} {count}")

    asyncio.run(_go())
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.workflows.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-definitions").set_defaults(func=cmd_list_definitions)

    p = sub.add_parser("start")
    p.add_argument("ticket")
    p.set_defaults(func=cmd_start)

    for name, handler in (
        ("run", cmd_run),
        ("inspect", cmd_inspect),
        ("steps", cmd_steps),
        ("checkpoints", cmd_checkpoints),
    ):
        q = sub.add_parser(name)
        q.add_argument("run_id")
        q.set_defaults(func=handler)

    r = sub.add_parser("resume")
    r.add_argument("run_id")
    r.add_argument("--reason", default="manual resume")
    r.set_defaults(func=cmd_resume)

    c = sub.add_parser("cancel")
    c.add_argument("run_id")
    c.add_argument("--reason", required=True)
    c.set_defaults(func=cmd_cancel)

    rp = sub.add_parser("replay")
    rp.add_argument("run_id")
    rp.add_argument(
        "--mode",
        default="deterministic_mock",
        choices=["recorded_outputs", "deterministic_mock", "current_configuration"],
    )
    rp.set_defaults(func=cmd_replay)

    demo = sub.add_parser("run-demo")
    demo.add_argument("fixture", choices=DEMO_FIXTURES)
    demo.set_defaults(func=cmd_run_demo)

    sub.add_parser("stats").set_defaults(func=cmd_stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
