"""PostgreSQL-backed outbox execution tests (S6).

Drives the real refund workflow to an approved outbox job, then processes it through the
exactly-once processor: success, idempotent duplicate processing, prior-refund balance,
tampering blocks, expiry blocks, retry/dead-letter and cancellation preconditions.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from app.actions.errors import ExecutionError, ExecutionErrorCode, technical
from app.actions.repository import ExecutedActionRepository, RefundLedgerRepository
from app.approvals.enums import ApprovalStatus
from app.approvals.repository import ApprovalRequestRepository
from app.approvals.service import ApprovalService, ApproveRequest, CreateApprovalRequest
from app.models.enums import OrderStatus, ShipmentStatus, UserRole
from app.models.ticket import Ticket
from app.models.user import User
from app.outbox.enums import OutboxStatus
from app.outbox.payload import OutboxJobData
from app.outbox.processor import OutboxProcessor, ProcessOutcome
from app.outbox.repository import OutboxAttemptRepository, OutboxRepository
from app.rules.clock import seed_reference_clock
from app.workflows.enums import ProposedActionStatus, WorkflowState
from app.workflows.repository import WorkflowRepository
from app.workflows.service import StartWorkflowRequest, SupportWorkflowService
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.conftest import TEST_DATABASE_URL
from tests.test_approval_service import _truncate_all

pytestmark = pytest.mark.usefixtures("_prepare_test_database")

REFUND_TICKET = "DEMO-REFUND-APPROVAL-001"


@pytest.fixture
async def maker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    from app.seeds.runner import seed

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_here = False
    async with factory() as session:
        await _truncate_execution(session)
        if (
            await session.scalar(select(Ticket).where(Ticket.seed_tag == REFUND_TICKET))
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
                await _truncate_all(session)
            else:
                await _truncate_execution(session)
            await session.commit()
        await engine.dispose()


async def _truncate_execution(session: AsyncSession) -> None:
    await session.execute(
        text(
            "TRUNCATE TABLE audit_events, refund_ledger_entries, executed_actions, "
            "outbox_attempts, outbox_jobs, approval_requests, workflow_runs "
            "RESTART IDENTITY CASCADE"
        )
    )


async def _second_supervisor(session: AsyncSession) -> object:
    from app.auth.models import AuthenticatedUser

    users = list(
        await session.scalars(
            select(User).where(User.role == UserRole.supervisor).order_by(User.email)
        )
    )
    user = users[1]
    return AuthenticatedUser.build(
        user_id=user.id, role=UserRole.supervisor, email=user.email, is_active=True
    )


async def _agent(session: AsyncSession) -> object:
    from app.auth.models import AuthenticatedUser

    user = await session.scalar(
        select(User).where(User.role == UserRole.support_agent).limit(1)
    )
    assert user is not None
    return AuthenticatedUser.build(
        user_id=user.id,
        role=UserRole.support_agent,
        email=user.email,
        is_active=True,
    )


async def _approved_job(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Return (job_id, approval_id, run_id) for a fresh approved refund job."""
    async with factory() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == REFUND_TICKET)
        )
        assert ticket is not None
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=factory).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    assert run.state == WorkflowState.AWAITING_APPROVAL
    async with factory() as session:
        agent = await _agent(session)
        proposal = await WorkflowRepository(session).get_current_proposal(run.run_id)
        assert proposal is not None
        svc = ApprovalService(session, clock=seed_reference_clock())
        created = await svc.create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        await session.commit()
        approval_id = created.approval_id
    async with factory() as session:
        supervisor = await _second_supervisor(session)
        result = await ApprovalService(session, clock=seed_reference_clock()).approve(
            approval_id, ApproveRequest(), supervisor
        )
        await session.commit()
        assert result.outbox_job_id is not None
        return result.outbox_job_id, approval_id, run.run_id


def _processor(
    factory: async_sessionmaker[AsyncSession],
    *,
    injector: Callable[[OutboxJobData, int], ExecutionError | None] | None = None,
) -> OutboxProcessor:
    return OutboxProcessor(
        factory, clock=seed_reference_clock(), failure_injector=injector
    )


# --- success ------------------------------------------------------------------------
async def test_refund_executes_exactly_once(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, approval_id, run_id = await _approved_job(maker)
    result = await _processor(maker).process_job(job_id)
    assert result.outcome == ProcessOutcome.SUCCEEDED
    assert result.business_effect_reference is not None
    assert result.business_effect_reference.startswith("SIM-REF-")

    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.SUCCEEDED
        approval = await ApprovalRequestRepository(session).get(approval_id)
        assert approval is not None and approval.status == ApprovalStatus.EXECUTED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None and run.current_state == WorkflowState.ACTION_SUCCEEDED
        proposal = await WorkflowRepository(session).get_current_proposal(run_id)
        assert proposal is not None
        assert proposal.status == ProposedActionStatus.COMPLETED
        # Exactly one executed action and one ledger entry.
        executed = await ExecutedActionRepository(session).get_by_outbox_job(job_id)
        assert executed is not None
        assert ExecutedActionRepository.verify_result_hash(executed)
        assert executed.order_id is not None
        ledger = await RefundLedgerRepository(session).list_for_order(executed.order_id)
        assert len(ledger) == 1
        assert ledger[0].amount_pence == executed.amount_pence


async def test_duplicate_processing_creates_no_second_effect(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, _, _ = await _approved_job(maker)
    first = await _processor(maker).process_job(job_id)
    assert first.outcome == ProcessOutcome.SUCCEEDED
    # Re-queue the same job and process again — no duplicate effect.
    async with maker() as session:
        await session.execute(
            text("UPDATE outbox_jobs SET status = 'pending' WHERE id = :j"),
            {"j": str(job_id)},
        )
        await session.commit()
    second = await _processor(maker).process_job(job_id)
    assert second.outcome == ProcessOutcome.DUPLICATE
    async with maker() as session:
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 1
        assert (
            await session.scalar(text("SELECT count(*) FROM refund_ledger_entries"))
            == 1
        )


async def test_prior_refund_reduces_remaining_balance(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, _, _ = await _approved_job(maker)
    await _processor(maker).process_job(job_id)
    async with maker() as session:
        executed = await ExecutedActionRepository(session).get_by_outbox_job(job_id)
        assert executed is not None
        prior = await RefundLedgerRepository(session).refunded_total_pence(
            executed.order_id
        )
        assert prior == executed.amount_pence  # the ledger now reflects the refund


# --- security blocks ----------------------------------------------------------------
async def test_tampered_payload_never_executes(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, _, _ = await _approved_job(maker)
    async with maker() as session:
        await session.execute(
            text(
                "UPDATE outbox_jobs SET payload_json = jsonb_set("
                "payload_json, '{approved_amount_pence}', '999999') WHERE id = :j"
            ),
            {"j": str(job_id)},
        )
        await session.commit()
    result = await _processor(maker).process_job(job_id)
    assert result.outcome == ProcessOutcome.FAILED
    assert result.error_code == ExecutionErrorCode.PAYLOAD_TAMPERED.value
    async with maker() as session:
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0


async def test_expired_approval_never_executes(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, approval_id, _ = await _approved_job(maker)
    async with maker() as session:
        # Backdate both timestamps so the approval is expired now while keeping the
        # expires_at > created_at constraint satisfied.
        await session.execute(
            text(
                "UPDATE approval_requests SET "
                "created_at = created_at - interval '2 days', "
                "expires_at = created_at - interval '1 day' WHERE id = :a"
            ),
            {"a": str(approval_id)},
        )
        await session.commit()
    result = await _processor(maker).process_job(job_id)
    assert result.outcome == ProcessOutcome.FAILED
    assert result.error_code == ExecutionErrorCode.APPROVAL_EXPIRED.value
    async with maker() as session:
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0


# --- reliability --------------------------------------------------------------------
async def test_retryable_failure_schedules_retry(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, _, _ = await _approved_job(maker)

    def injector(_payload: OutboxJobData, _attempt: int) -> ExecutionError | None:
        return technical(ExecutionErrorCode.INJECTED_FAILURE, "boom")

    result = await _processor(maker, injector=injector).process_job(job_id)
    assert result.outcome == ProcessOutcome.RETRY_SCHEDULED
    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None
        assert job.status == OutboxStatus.RETRY_SCHEDULED
        assert job.attempt_count == 1
        attempts = await OutboxAttemptRepository(session).list_for_job(job_id)
        assert len(attempts) == 1
        assert attempts[0].retryable is True


async def test_exhausted_retries_dead_letter(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, approval_id, run_id = await _approved_job(maker)

    def injector(_payload: OutboxJobData, _attempt: int) -> ExecutionError | None:
        return technical(ExecutionErrorCode.INJECTED_FAILURE, "boom")

    processor = _processor(maker, injector=injector)
    outcome = None
    # Drive attempts until the job dead-letters (max attempts = 5).
    for _ in range(6):
        async with maker() as session:
            job = await OutboxRepository(session).get(job_id)
            assert job is not None
            if job.status == OutboxStatus.DEAD_LETTER:
                break
            # Make a retry-scheduled job due again.
            await session.execute(
                text(
                    "UPDATE outbox_jobs SET status = 'pending', "
                    "next_attempt_at = now() "
                    "WHERE id = :j AND status = 'retry_scheduled'"
                ),
                {"j": str(job_id)},
            )
            await session.commit()
        outcome = await processor.process_job(job_id)

    async with maker() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None and job.status == OutboxStatus.DEAD_LETTER
        approval = await ApprovalRequestRepository(session).get(approval_id)
        assert approval is not None
        assert approval.status == ApprovalStatus.EXECUTION_FAILED
        run = await WorkflowRepository(session).get(run_id)
        assert run is not None and run.current_state == WorkflowState.ACTION_FAILED
        # Attempt history explains every failure; no effect was ever applied.
        attempts = await OutboxAttemptRepository(session).list_for_job(job_id)
        assert len(attempts) == 5
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0
    assert outcome is not None and outcome.outcome == ProcessOutcome.DEAD_LETTER


# --- cancellation -------------------------------------------------------------------
async def _approved_cancellation_job(
    factory: async_sessionmaker[AsyncSession],
    *,
    order_status: OrderStatus,
    shipment_status: ShipmentStatus | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """Approve a refund job, then repoint it at a cancellation of *its own* order.

    Reusing the approval's own order keeps ownership valid, so the test isolates the
    cancellation handler and its precondition checks. The payload is rewritten and
    re-hashed so it stays internally consistent (a genuine cancellation instruction).
    """
    job_id, _approval_id, _run_id = await _approved_job(factory)
    async with factory() as session:
        job = await OutboxRepository(session).get(job_id)
        assert job is not None
        payload = OutboxJobData.model_validate(job.payload_json)
        order_id = payload.order_id
        assert order_id is not None
        await session.execute(
            text("UPDATE orders SET status = :s WHERE id = :o"),
            {"s": order_status.value, "o": str(order_id)},
        )
        if shipment_status is not None:
            # A non-delivered shipment must have no delivered_at (DB check constraint).
            await session.execute(
                text(
                    "UPDATE shipments SET status = :s, delivered_at = NULL "
                    "WHERE order_id = :o"
                ),
                {"s": shipment_status.value, "o": str(order_id)},
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
        # Keep the approval's action type consistent so revalidation's action-mapping
        # check (cancellation → simulated_order_cancellation) passes.
        await session.execute(
            text(
                "UPDATE approval_requests SET "
                "action_type = 'request_supervisor_cancellation_approval' "
                "WHERE id = :a"
            ),
            {"a": str(job.approval_request_id)},
        )
        await session.commit()
    return job_id, order_id


async def test_cancellation_blocked_after_shipment_routes_to_manual(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, order_id = await _approved_cancellation_job(
        maker, order_status=OrderStatus.shipped
    )
    result = await _processor(maker).process_job(job_id)
    assert result.outcome == ProcessOutcome.MANUAL_ACTION_REQUIRED
    assert result.error_code == ExecutionErrorCode.ORDER_SHIPPED_BEFORE_EXECUTION.value
    async with maker() as session:
        order = await session.scalar(
            text("SELECT status FROM orders WHERE id = :o"), {"o": str(order_id)}
        )
        assert order == "shipped"  # never cancelled
        assert await session.scalar(text("SELECT count(*) FROM executed_actions")) == 0


async def test_cancellation_executes_before_shipment(
    maker: async_sessionmaker[AsyncSession],
) -> None:
    job_id, order_id = await _approved_cancellation_job(
        maker,
        order_status=OrderStatus.processing,
        shipment_status=ShipmentStatus.label_created,
    )
    result = await _processor(maker).process_job(job_id)
    assert result.outcome == ProcessOutcome.SUCCEEDED
    assert result.business_effect_reference is not None
    assert result.business_effect_reference.startswith("SIM-CAN-")
    async with maker() as session:
        order = await session.scalar(
            text("SELECT status FROM orders WHERE id = :o"), {"o": str(order_id)}
        )
        assert order == "cancelled"
        # No refund ledger entry for a cancellation.
        assert (
            await session.scalar(text("SELECT count(*) FROM refund_ledger_entries"))
            == 0
        )
