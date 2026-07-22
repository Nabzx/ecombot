"""PostgreSQL-backed approval-service tests (S6).

Drives the real ``DEMO-REFUND-APPROVAL-001`` workflow to ``awaiting_approval``, then
exercises creation, editing, the four decision paths and every safety guard. Execution
is out of scope for this increment: a successful approval must stop at
``approved_pending_execution`` with no outbox job.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from app.approvals.enums import ApprovalDecisionType, ApprovalStatus
from app.approvals.errors import ApprovalError, ApprovalErrorCode
from app.approvals.repository import ApprovalDecisionRepository
from app.approvals.service import (
    ApprovalService,
    ApproveRequest,
    CancelApprovalRequest,
    CreateApprovalRequest,
    EditApprovalRequest,
    RejectRequest,
)
from app.approvals.snapshot import compute_snapshot_hash, verify_snapshot
from app.auth.models import AuthenticatedUser
from app.models.approval import ApprovalRequest
from app.models.enums import UserRole
from app.models.ticket import Ticket
from app.models.user import User
from app.rules.clock import seed_reference_clock
from app.workflows.enums import ProposedActionStatus, WorkflowState
from app.workflows.registry import WORKFLOW_V2_VERSION
from app.workflows.repository import WorkflowRepository
from app.workflows.service import StartWorkflowRequest, SupportWorkflowService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("_prepare_test_database")

APPROVAL_TICKET = "DEMO-REFUND-APPROVAL-001"


class _LaterClock:
    """The seed clock advanced past the approval expiry window."""

    def __init__(self, hours: int = 48) -> None:
        self._at = seed_reference_clock().now() + timedelta(hours=hours)

    def now(self) -> datetime:
        return self._at


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from app.seeds.runner import seed

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_here = False
    async with factory() as session:
        await session.execute(text("DELETE FROM approval_requests"))
        await session.execute(text("DELETE FROM workflow_runs"))
        if (
            await session.scalar(
                select(Ticket).where(Ticket.seed_tag == APPROVAL_TICKET)
            )
            is None
        ):
            await seed(session)
            seeded_here = True
        await session.commit()
    try:
        yield factory
    finally:
        async with factory() as session:
            if seeded_here:
                # Leave the schema as empty as we found it: later test modules seed
                # themselves and assume they start from a clean database.
                await _truncate_all(session)
            else:
                await session.execute(text("DELETE FROM approval_requests"))
                await session.execute(text("DELETE FROM workflow_runs"))
            await session.commit()
        await engine.dispose()


async def _truncate_all(session: AsyncSession) -> None:
    names = list(
        await session.scalars(
            text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                "AND tablename <> 'alembic_version'"
            )
        )
    )
    if names:
        joined = ", ".join(f'"{name}"' for name in names)
        await session.execute(text(f"TRUNCATE TABLE {joined} RESTART IDENTITY CASCADE"))


async def _actor(session: AsyncSession, role: UserRole) -> AuthenticatedUser:
    user = await session.scalar(select(User).where(User.role == role).limit(1))
    assert user is not None, f"no seeded {role.value}"
    return AuthenticatedUser.build(
        user_id=user.id, role=role, email=user.email, is_active=True
    )


async def _second_supervisor(session: AsyncSession) -> AuthenticatedUser:
    users = list(
        await session.scalars(
            select(User).where(User.role == UserRole.supervisor).order_by(User.email)
        )
    )
    assert len(users) >= 2, "need two supervisors to test self-approval"
    user = users[1]
    return AuthenticatedUser.build(
        user_id=user.id, role=UserRole.supervisor, email=user.email, is_active=True
    )


async def _run_to_awaiting_approval(
    factory: async_sessionmaker[AsyncSession],
) -> uuid.UUID:
    """Drive the demo refund ticket to awaiting_approval on workflow v2."""
    async with factory() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == APPROVAL_TICKET)
        )
        assert ticket is not None, f"demo ticket {APPROVAL_TICKET} not seeded"
        ticket_id = ticket.id
    service = SupportWorkflowService(session_factory=factory)
    result = await service.start(StartWorkflowRequest(ticket_id=ticket_id))
    assert result.state == WorkflowState.AWAITING_APPROVAL, result.state
    return result.run_id


async def _pending_approval(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, AuthenticatedUser]:
    """Return (approval_id, run_id, requesting agent) for a fresh pending request."""
    run_id = await _run_to_awaiting_approval(factory)
    async with factory() as session:
        agent = await _actor(session, UserRole.support_agent)
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        assert proposal is not None
        svc = ApprovalService(session, clock=seed_reference_clock())
        created = await svc.create_request(
            CreateApprovalRequest(
                proposed_action_id=proposal.id,
                request_reason="customer requests refund",
            ),
            agent,
        )
        await session.commit()
    return created.approval_id, run_id, agent


# --- creation -----------------------------------------------------------------------
async def test_create_request_snapshots_the_action(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    run_id = await _run_to_awaiting_approval(maker)
    async with maker() as session:
        agent = await _actor(session, UserRole.support_agent)
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        assert proposal is not None
        svc = ApprovalService(session, clock=seed_reference_clock())
        result = await svc.create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        await session.commit()

        assert result.status == ApprovalStatus.PENDING
        assert result.created is True
        assert result.outbox_job_created is False
        approval = await session.get(ApprovalRequest, result.approval_id)
        assert approval is not None
        # The stored snapshot verifies against its hash and binds the exact action.
        snapshot = verify_snapshot(
            approval.evidence_snapshot_json, approval.evidence_snapshot_hash
        )
        assert snapshot.proposed_action_id == proposal.id
        assert snapshot.workflow_version == WORKFLOW_V2_VERSION
        assert snapshot.requester_user_id == agent.user_id
        assert approval.idempotency_key.startswith("act-")


async def test_create_request_is_idempotent(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, run_id, agent = await _pending_approval(maker)
    async with maker() as session:
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        assert proposal is not None
        svc = ApprovalService(session, clock=seed_reference_clock())
        again = await svc.create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        assert again.approval_id == approval_id
        assert again.created is False


async def test_snapshot_tampering_is_detected(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        tampered = dict(approval.evidence_snapshot_json)
        tampered["requested_amount_pence"] = 999_999
        approval.evidence_snapshot_json = tampered
        await session.commit()

        supervisor = await _actor(session, UserRole.supervisor)
        svc = ApprovalService(session, clock=seed_reference_clock())
        with pytest.raises(ApprovalError) as exc:
            await svc.approve(approval_id, ApproveRequest(), supervisor)
        assert exc.value.code == ApprovalErrorCode.APPROVAL_SNAPSHOT_TAMPERED


# --- approval -----------------------------------------------------------------------
async def test_approve_moves_workflow_and_creates_no_outbox_job(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, run_id, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        result = await svc.approve(
            approval_id, ApproveRequest(reason="within policy"), supervisor
        )
        await session.commit()

        assert result.status == ApprovalStatus.APPROVED
        assert result.outbox_job_created is False
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.current_state == WorkflowState.APPROVED_PENDING_EXECUTION
        # Nothing was executed or queued in this increment.
        assert await session.scalar(text("SELECT count(*) FROM outbox_jobs")) == 0
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0

        decisions = await ApprovalDecisionRepository(session).list_for_request(
            approval_id
        )
        assert [d.decision for d in decisions] == [ApprovalDecisionType.APPROVE]
        assert decisions[0].actor_user_id == supervisor.user_id
        assert decisions[0].previous_status == ApprovalStatus.PENDING.value
        assert decisions[0].new_status == ApprovalStatus.APPROVED.value


async def test_approve_writes_step_and_checkpoint(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, run_id, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        await ApprovalService(session, clock=seed_reference_clock()).approve(
            approval_id, ApproveRequest(), supervisor
        )
        await session.commit()

        repo = WorkflowRepository(session)
        steps = await repo.list_steps(run_id)
        assert steps[-1].step_name == "approval_granted"
        assert steps[-1].destination_state == WorkflowState.APPROVED_PENDING_EXECUTION
        checkpoint = await repo.get_latest_checkpoint(run_id)
        assert checkpoint is not None
        assert checkpoint.state == WorkflowState.APPROVED_PENDING_EXECUTION
        metadata = checkpoint.snapshot_json["approval_metadata"]
        assert isinstance(metadata, dict)
        assert metadata["approval_request_id"] == str(approval_id)
        assert metadata["actor_user_id"] == str(supervisor.user_id)


async def test_self_approval_is_forbidden(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """A supervisor who raised the request may never approve it themselves."""
    run_id = await _run_to_awaiting_approval(maker)
    async with maker() as session:
        supervisor = await _actor(session, UserRole.supervisor)
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        assert proposal is not None
        svc = ApprovalService(session, clock=seed_reference_clock())
        created = await svc.create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), supervisor
        )
        await session.commit()

        with pytest.raises(ApprovalError) as exc:
            await svc.approve(created.approval_id, ApproveRequest(), supervisor)
        assert exc.value.code == ApprovalErrorCode.APPROVAL_SELF_DECISION_FORBIDDEN


async def test_agent_cannot_approve(maker: async_sessionmaker[AsyncSession]) -> None:
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        other_agent = AuthenticatedUser.build(
            user_id=uuid.uuid4(),
            role=UserRole.support_agent,
            email="other.agent@meridian.example",
            is_active=True,
        )
        svc = ApprovalService(session, clock=seed_reference_clock())
        with pytest.raises(ApprovalError) as exc:
            await svc.approve(approval_id, ApproveRequest(), other_agent)
        assert exc.value.code == ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN


async def test_second_decision_is_rejected(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """Exactly one decision wins; the loser sees a stable conflict error."""
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        await svc.approve(approval_id, ApproveRequest(), supervisor)
        await session.commit()

        with pytest.raises(ApprovalError) as exc:
            await svc.reject(approval_id, RejectRequest(reason="too late"), supervisor)
        assert exc.value.code == ApprovalErrorCode.APPROVAL_NOT_PENDING


async def test_approved_amount_cannot_exceed_maximum(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        approval = await session.get(ApprovalRequest, approval_id)
        assert approval is not None
        if approval.maximum_allowed_amount_pence is None:
            pytest.skip("demo action carries no monetary limit")
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        with pytest.raises(ApprovalError) as exc:
            await svc.approve(
                approval_id,
                ApproveRequest(
                    approved_amount_pence=approval.maximum_allowed_amount_pence + 1
                ),
                supervisor,
            )
        assert exc.value.code in {
            ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_MAXIMUM,
            ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_REQUESTED,
        }


# --- rejection, cancellation, expiry -------------------------------------------------
async def test_reject_ends_the_run(maker: async_sessionmaker[AsyncSession]) -> None:
    approval_id, run_id, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        result = await svc.reject(
            approval_id, RejectRequest(reason="outside policy window"), supervisor
        )
        await session.commit()

        assert result.status == ApprovalStatus.REJECTED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.current_state == WorkflowState.APPROVAL_REJECTED
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        if proposal is not None:
            assert proposal.status == ProposedActionStatus.SUPERSEDED


async def test_reject_requires_a_reason(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        with pytest.raises(ApprovalError):
            await svc.reject(approval_id, RejectRequest(reason="  "), supervisor)


async def test_requesting_agent_can_cancel(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, run_id, agent = await _pending_approval(maker)
    async with maker() as session:
        svc = ApprovalService(session, clock=seed_reference_clock())
        result = await svc.cancel(
            approval_id, CancelApprovalRequest(reason="customer withdrew"), agent
        )
        await session.commit()

        assert result.status == ApprovalStatus.CANCELLED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.current_state == WorkflowState.AWAITING_AGENT


async def test_expiry_sweep_pauses_the_run(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, run_id, _ = await _pending_approval(maker)
    async with maker() as session:
        # Advance the clock past the expiry window rather than rewriting history.
        clock = _LaterClock()
        svc = ApprovalService(session, clock=clock)
        result = await svc.expire_due_requests()
        await session.commit()

        assert approval_id in result.expired_ids
        refreshed = await session.get(ApprovalRequest, approval_id)
        assert refreshed is not None
        assert refreshed.status == ApprovalStatus.EXPIRED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None
        assert run.current_state == WorkflowState.APPROVAL_EXPIRED
        decisions = await ApprovalDecisionRepository(session).list_for_request(
            approval_id
        )
        # A system expiry has no human actor.
        assert decisions[-1].decision == ApprovalDecisionType.EXPIRE
        assert decisions[-1].actor_user_id is None


async def test_expired_approval_cannot_be_approved(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, _ = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        with pytest.raises(ApprovalError) as exc:
            await ApprovalService(session, clock=_LaterClock()).approve(
                approval_id, ApproveRequest(), supervisor
            )
        assert exc.value.code == ApprovalErrorCode.APPROVAL_EXPIRED


# --- editing ------------------------------------------------------------------------
async def test_text_edit_keeps_the_idempotency_key(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    """A wording change must not create a new business action."""
    approval_id, _, agent = await _pending_approval(maker)
    async with maker() as session:
        before = await session.get(ApprovalRequest, approval_id)
        assert before is not None
        original_key = before.idempotency_key

        svc = ApprovalService(session, clock=seed_reference_clock())
        await svc.edit(
            approval_id,
            EditApprovalRequest(draft_response_body="Reworded, same action."),
            agent,
        )
        await session.commit()

        after = await session.get(ApprovalRequest, approval_id)
        assert after is not None
        assert after.idempotency_key == original_key
        # The snapshot is re-hashed and still verifies.
        snapshot = verify_snapshot(
            after.evidence_snapshot_json, after.evidence_snapshot_hash
        )
        assert compute_snapshot_hash(snapshot) == after.evidence_snapshot_hash


async def test_agent_cannot_change_the_amount(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, agent = await _pending_approval(maker)
    async with maker() as session:
        svc = ApprovalService(session, clock=seed_reference_clock())
        with pytest.raises(ApprovalError) as exc:
            await svc.edit(
                approval_id, EditApprovalRequest(approved_amount_pence=100), agent
            )
        assert exc.value.code == ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN


async def test_decided_approval_cannot_be_edited(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    approval_id, _, agent = await _pending_approval(maker)
    async with maker() as session:
        supervisor = await _second_supervisor(session)
        svc = ApprovalService(session, clock=seed_reference_clock())
        await svc.approve(approval_id, ApproveRequest(), supervisor)
        await session.commit()

        with pytest.raises(ApprovalError) as exc:
            await svc.edit(
                approval_id, EditApprovalRequest(draft_response_body="too late"), agent
            )
        assert exc.value.code == ApprovalErrorCode.EDIT_NOT_ALLOWED
