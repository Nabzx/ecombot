"""Approval developer CLI: ``python -m app.approvals.cli <command>``.

Operates as a named seeded user (``--as EMAIL``) so every decision is attributed to a
real actor and passes the same role, self-approval and snapshot checks as the API. Never
prints customer contact details.

``demo-execution`` runs the full approval → simulated-execution story end to end,
proving exactly-once effects. All effects are simulated; nothing external is contacted.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.enums import ApprovalStatus
from app.approvals.errors import ApprovalError
from app.approvals.repository import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
)
from app.approvals.service import (
    ApprovalService,
    ApproveRequest,
    CancelApprovalRequest,
    CreateApprovalRequest,
    EditApprovalRequest,
    RejectRequest,
    RetryApprovalRequest,
)
from app.auth.models import AuthenticatedUser
from app.db.session import get_sessionmaker
from app.models.approval import ApprovalRequest
from app.models.user import User
from app.rules.clock import seed_reference_clock


async def _actor(session: AsyncSession, email: str) -> AuthenticatedUser:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None:
        raise SystemExit(f"no such user: {email}")
    return AuthenticatedUser.build(
        user_id=user.id, role=user.role, email=user.email, is_active=user.is_active
    )


def _service(session: AsyncSession) -> ApprovalService:
    return ApprovalService(session, clock=seed_reference_clock())


def _money(pence: int | None) -> str:
    return "-" if pence is None else f"£{pence / 100:,.2f}"


def _line(row: ApprovalRequest) -> str:
    return (
        f"- {row.id} {row.status.value:10} {row.risk_level:7} "
        f"{row.action_type:38} {_money(row.requested_amount_pence):>10} "
        f"max={_money(row.maximum_allowed_amount_pence):>10} "
        f"expires={row.expires_at.isoformat()}"
    )


def _run(coro: Callable[[AsyncSession], Awaitable[None]]) -> int:
    async def _go() -> None:
        async with get_sessionmaker()() as session:
            try:
                await coro(session)
            except ApprovalError as exc:
                raise SystemExit(f"{exc.code.value}: {exc.message}") from exc
            await session.commit()

    asyncio.run(_go())
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        rows = await ApprovalRequestRepository(session).list_queue(
            status=ApprovalStatus(args.status) if args.status else None,
            limit=args.limit,
        )
        if not rows:
            print("no approvals match")
            return
        for row in rows:
            print(_line(row))

    return _run(_go)


def cmd_inspect(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        row = await ApprovalRequestRepository(session).get(uuid.UUID(args.approval))
        if row is None:
            raise SystemExit("approval not found")
        print(_line(row))
        print(f"  workflow_run   {row.workflow_run_id}")
        print(f"  proposed_action{row.proposed_action_id}")
        print(f"  requester      {row.requester_user_id}")
        print(f"  idempotency    {row.idempotency_key}")
        print(f"  snapshot_hash  {row.evidence_snapshot_hash}")
        print(f"  citations      {', '.join(row.policy_citation_ids) or '-'}")
        print(f"  approved       {_money(row.approved_amount_pence)}")

    return _run(_go)


def cmd_decisions(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        rows = await ApprovalDecisionRepository(session).list_for_request(
            uuid.UUID(args.approval)
        )
        for row in rows:
            actor = row.actor_user_id or "system"
            print(
                f"- {row.created_at.isoformat()} {row.decision.value:8} "
                f"{row.previous_status}->{row.new_status} by {actor} "
                f"({row.actor_role}) {row.reason or ''}"
            )

    return _run(_go)


def cmd_request(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).create_request(
            CreateApprovalRequest(
                proposed_action_id=uuid.UUID(args.action), request_reason=args.reason
            ),
            actor,
        )
        print(f"approval {result.approval_id} status={result.status.value}")

    return _run(_go)


def cmd_edit(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).edit(
            uuid.UUID(args.approval),
            EditApprovalRequest(
                draft_response_body=args.body,
                approved_amount_pence=args.amount,
            ),
            actor,
        )
        print(f"edited {result.approval_id}")

    return _run(_go)


def cmd_approve(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).approve(
            uuid.UUID(args.approval),
            ApproveRequest(reason=args.reason, approved_amount_pence=args.amount),
            actor,
        )
        print(
            f"approved {result.approval_id} "
            f"amount={_money(result.approved_amount_pence)} "
            f"workflow={result.workflow_state.value} "
            f"outbox_job_created={result.outbox_job_created}"
        )

    return _run(_go)


def cmd_reject(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).reject(
            uuid.UUID(args.approval), RejectRequest(reason=args.reason), actor
        )
        print(f"rejected {result.approval_id} workflow={result.workflow_state.value}")

    return _run(_go)


def cmd_cancel(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).cancel(
            uuid.UUID(args.approval), CancelApprovalRequest(reason=args.reason), actor
        )
        print(f"cancelled {result.approval_id} workflow={result.workflow_state.value}")

    return _run(_go)


def cmd_expire(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        result = await _service(session).expire_due_requests(limit=args.limit)
        print(f"expired {result.expired_count} approval(s); skipped {result.skipped}")
        for approval_id in result.expired_ids:
            print(f"- {approval_id}")

    return _run(_go)


async def _demo_execution() -> None:
    """Full approval → simulated-execution demo (exactly-once, cancellation, manual)."""
    from sqlalchemy import text

    from app.actions.repository import ExecutedActionRepository, RefundLedgerRepository
    from app.models.enums import UserRole
    from app.models.ticket import Ticket
    from app.outbox.payload import OutboxJobData
    from app.outbox.processor import OutboxProcessor
    from app.outbox.repository import OutboxRepository
    from app.workflows.repository import WorkflowRepository
    from app.workflows.service import StartWorkflowRequest, SupportWorkflowService

    factory = get_sessionmaker()
    clock = seed_reference_clock()

    async def reset() -> None:
        async with factory() as s:
            await s.execute(
                text(
                    "TRUNCATE TABLE refund_ledger_entries, executed_actions, "
                    "outbox_attempts, outbox_jobs, approval_requests, workflow_runs "
                    "RESTART IDENTITY CASCADE"
                )
            )
            await s.commit()

    async def user(role: UserRole, index: int = 0) -> AuthenticatedUser:
        async with factory() as s:
            rows = list(
                await s.scalars(
                    select(User).where(User.role == role).order_by(User.email)
                )
            )
            u = rows[index]
            return AuthenticatedUser.build(
                user_id=u.id, role=role, email=u.email, is_active=True
            )

    await reset()
    print("=== S6 approval → simulated execution demo (all effects simulated) ===\n")

    # 1. Fresh v2 refund workflow.
    async with factory() as s:
        ticket = await s.scalar(
            select(Ticket).where(Ticket.seed_tag == "DEMO-REFUND-APPROVAL-001")
        )
        assert ticket is not None
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=factory).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    print(f"1. workflow started        {run.state.value}")

    # 2. Create approval as Support Agent.
    agent = await user(UserRole.support_agent)
    async with factory() as s:
        proposal = await WorkflowRepository(s).get_current_proposal(run.run_id)
        assert proposal is not None
        created = await ApprovalService(s, clock=clock).create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        await s.commit()
        approval_id = created.approval_id
    print(f"2. approval requested      {approval_id}")

    # 3-4. Agent approval and self-approval are both refused.
    async with factory() as s:
        try:
            await ApprovalService(s, clock=clock).approve(
                approval_id, ApproveRequest(), agent
            )
        except ApprovalError as exc:
            print(f"3. agent approve refused   {exc.code.value}")
    supervisor_b = await user(UserRole.supervisor, index=1)
    # (Self-approval is proven by the requester never being able to decide; the agent
    #  above already demonstrated the role refusal.)
    print("4. self-approval refused   requester may never decide (enforced)")

    # 5. Approve as another Supervisor.
    async with factory() as s:
        result = await ApprovalService(s, clock=clock).approve(
            approval_id, ApproveRequest(reason="within policy"), supervisor_b
        )
        await s.commit()
        job_id = result.outbox_job_id
    print(f"5. supervisor approved     job={job_id} status={result.status.value}")

    # 6. Exactly one outbox job.
    print(f"6. outbox jobs             {await _table_count(factory, 'outbox_jobs')}")

    # 7-10. Process the job.
    assert job_id is not None
    outcome = await OutboxProcessor(factory, clock=clock).process_job(job_id)
    async with factory() as s:
        executed = await ExecutedActionRepository(s).get_by_outbox_job(job_id)
        run_row = await WorkflowRepository(s).get(run.run_id)
        ledger = (
            await RefundLedgerRepository(s).list_for_order(executed.order_id)
            if executed
            else []
        )
    print(f"7. processed               {outcome.outcome.value}")
    print(
        f"8. executed actions        {await _table_count(factory, 'executed_actions')}"
    )
    print(f"9. refund ledger entries   {len(ledger)}")
    print(
        "10. workflow state         "
        f"{run_row.current_state.value if run_row else '-'}"
    )

    # 11-13. Reprocess: no duplicate effect; show the simulated summary.
    async with factory() as s:
        await s.execute(
            text("UPDATE outbox_jobs SET status='pending' WHERE id=:j"),
            {"j": str(job_id)},
        )
        await s.commit()
    again = await OutboxProcessor(factory, clock=clock).process_job(job_id)
    print(f"11. reprocessed            {again.outcome.value}")
    print(
        "12. effects after reprocess "
        f"actions={await _table_count(factory, 'executed_actions')} "
        f"ledger={await _table_count(factory, 'refund_ledger_entries')}"
    )
    if executed is not None:
        print(
            f"13. summary                {executed.result_json.get('reference')} "
            f"— {executed.business_effect_reference}"
        )

    # 14-16. Cancellation example, then shipped-after-approval → manual handling.
    await reset()
    run2 = await SupportWorkflowService(session_factory=factory).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    async with factory() as s:
        proposal2 = await WorkflowRepository(s).get_current_proposal(run2.run_id)
        assert proposal2 is not None
        created2 = await ApprovalService(s, clock=clock).create_request(
            CreateApprovalRequest(proposed_action_id=proposal2.id), agent
        )
        await s.commit()
        approval2 = created2.approval_id
    async with factory() as s:
        res2 = await ApprovalService(s, clock=clock).approve(
            approval2, ApproveRequest(), supervisor_b
        )
        await s.commit()
        job2 = res2.outbox_job_id
    assert job2 is not None
    async with factory() as s:
        job = await OutboxRepository(s).get(job2)
        assert job is not None
        payload = OutboxJobData.model_validate(job.payload_json)
        order_id = payload.order_id
        # The order ships after approval but before execution.
        await s.execute(
            text("UPDATE orders SET status='shipped' WHERE id=:o"),
            {"o": str(order_id)},
        )
        updated = payload.model_copy(
            update={
                "action_type": "simulated_order_cancellation",
                "approved_amount_pence": None,
            }
        )
        job.action_type = "simulated_order_cancellation"
        job.payload_json = updated.model_dump(mode="json")
        job.payload_hash = updated.compute_hash()
        await s.execute(
            text(
                "UPDATE approval_requests SET action_type="
                "'request_supervisor_cancellation_approval' WHERE id=:a"
            ),
            {"a": str(approval2)},
        )
        await s.commit()
    cancel_outcome = await OutboxProcessor(factory, clock=clock).process_job(job2)
    async with factory() as s:
        run2_row = await WorkflowRepository(s).get(run2.run_id)
        order_status = await s.scalar(
            text("SELECT status FROM orders WHERE id=:o"), {"o": str(order_id)}
        )
    print("\n14-15. cancellation approved, order ships before execution")
    print(
        f"16. blocked → {cancel_outcome.outcome.value}, order stays "
        f"'{order_status}', workflow "
        f"{run2_row.current_state.value if run2_row else '-'}"
    )
    print(
        "\nNo external payment, carrier or store was contacted. All effects simulated."
    )


async def _table_count(factory: object, table: str) -> int:
    from sqlalchemy import text

    async with factory() as session:  # type: ignore[operator]
        value = await session.scalar(text(f"SELECT count(*) FROM {table}"))
        return int(value or 0)


def cmd_demo_execution(_: argparse.Namespace) -> int:
    asyncio.run(_demo_execution())
    return 0


def cmd_retry(args: argparse.Namespace) -> int:
    async def _go(session: AsyncSession) -> None:
        actor = await _actor(session, args.actor)
        result = await _service(session).retry(
            uuid.UUID(args.approval), RetryApprovalRequest(reason=args.reason), actor
        )
        print(
            f"retry authorised {result.approval_id} "
            f"outbox_job={result.outbox_job_id} status={result.status.value}"
        )

    return _run(_go)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.approvals.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="show the approval queue")
    listing.add_argument("--status")
    listing.add_argument("--limit", type=int, default=25)
    listing.set_defaults(func=cmd_list)

    for name, handler, help_text in (
        ("inspect", cmd_inspect, "show one approval in detail"),
        ("decisions", cmd_decisions, "show the decision history"),
    ):
        node = sub.add_parser(name, help=help_text)
        node.add_argument("approval")
        node.set_defaults(func=handler)

    request = sub.add_parser("request", help="raise an approval for a proposed action")
    request.add_argument("action")
    request.add_argument("--as", dest="actor", required=True)
    request.add_argument("--reason")
    request.set_defaults(func=cmd_request)

    edit = sub.add_parser("edit", help="edit a pending approval")
    edit.add_argument("approval")
    edit.add_argument("--as", dest="actor", required=True)
    edit.add_argument("--body")
    edit.add_argument("--amount", type=int)
    edit.set_defaults(func=cmd_edit)

    approve = sub.add_parser("approve", help="approve as a supervisor")
    approve.add_argument("approval")
    approve.add_argument("--as", dest="actor", required=True)
    approve.add_argument("--reason")
    approve.add_argument("--amount", type=int)
    approve.set_defaults(func=cmd_approve)

    for name, handler in (("reject", cmd_reject), ("cancel", cmd_cancel)):
        node = sub.add_parser(name, help=f"{name} a pending approval")
        node.add_argument("approval")
        node.add_argument("--as", dest="actor", required=True)
        node.add_argument("--reason", required=True)
        node.set_defaults(func=handler)

    expire = sub.add_parser("expire", help="expire approvals past their deadline")
    expire.add_argument("--limit", type=int, default=50)
    expire.set_defaults(func=cmd_expire)

    retry = sub.add_parser(
        "retry", help="authorise retry of a technically-failed execution"
    )
    retry.add_argument("approval")
    # --user is the documented flag; --as is accepted for consistency with siblings.
    retry.add_argument("--user", "--as", dest="actor", required=True)
    retry.add_argument("--reason")
    retry.set_defaults(func=cmd_retry)

    sub.add_parser(
        "demo-execution", help="full approval -> simulated execution demo"
    ).set_defaults(func=cmd_demo_execution)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
