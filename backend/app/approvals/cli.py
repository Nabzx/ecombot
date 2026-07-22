"""Approval developer CLI: ``python -m app.approvals.cli <command>``.

Operates as a named seeded user (``--as EMAIL``) so every decision is attributed to a
real actor and passes the same role, self-approval and snapshot checks as the API. Never
prints customer contact details.

Execution is out of scope for this increment: ``approve`` stops the workflow at
``approved_pending_execution`` and queues nothing.
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
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    exit_code: int = args.func(args)
    return exit_code


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
