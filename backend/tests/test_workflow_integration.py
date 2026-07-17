"""PostgreSQL-backed workflow integration, concurrency and recovery tests.

Uses a dedicated committing session factory on the test database (the workflow service
opens its own short transactions), with explicit cleanup of ``workflow_runs`` around
each test rather than the rolled-back ``db_session`` fixture.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.models.ticket import Ticket
from app.rules.clock import seed_reference_clock
from app.workflows.checkpointing import CheckpointError, verify_checkpoint
from app.workflows.enums import WorkflowState, WorkflowStatus
from app.workflows.repository import WorkflowRepository
from app.workflows.service import (
    CancelWorkflowRequest,
    ReplayWorkflowRequest,
    StartWorkflowRequest,
    SupportWorkflowService,
)
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.usefixtures("_prepare_test_database")

_EXPECTED = {
    "DEMO-TRACKING-001": WorkflowState.AWAITING_AGENT,
    "DEMO-REFUND-APPROVAL-001": WorkflowState.AWAITING_APPROVAL,
    "DEMO-PROMPT-INJECTION-001": WorkflowState.ESCALATED,
    "DEMO-CROSS-CUSTOMER-001": WorkflowState.BLOCKED,
    "DEMO-RETURN-DAY-30": WorkflowState.AWAITING_APPROVAL,
    "DEMO-RETURN-DAY-31": WorkflowState.AWAITING_AGENT,
}


@pytest.fixture
async def service() -> AsyncIterator[SupportWorkflowService]:
    from app.seeds.runner import seed

    engine = create_async_engine(TEST_DATABASE_URL)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        await session.execute(text("DELETE FROM workflow_runs"))
        # Seed the demo tickets on first use (the test schema starts empty).
        demo = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == "DEMO-TRACKING-001")
        )
        if demo is None:
            await seed(session)
        await session.commit()
    try:
        yield SupportWorkflowService(session_factory=maker)
    finally:
        async with maker() as session:
            await session.execute(text("DELETE FROM workflow_runs"))
            await session.commit()
        await engine.dispose()


async def _ref(service: SupportWorkflowService, seed_tag: str) -> str:
    async with service.session_factory() as session:
        ticket = await session.scalar(select(Ticket).where(Ticket.seed_tag == seed_tag))
        assert ticket is not None
        return ticket.ticket_reference


@pytest.mark.parametrize(("seed_tag", "expected"), sorted(_EXPECTED.items()))
async def test_demo_reaches_expected_state(
    service: SupportWorkflowService, seed_tag: str, expected: WorkflowState
) -> None:
    ref = await _ref(service, seed_tag)
    result = await service.start(StartWorkflowRequest(ticket_reference=ref))
    assert result.state == expected
    # No consequential action is ever executed.
    assert not (result.proposed_action or "").startswith("execute_")


async def test_cross_customer_never_reveals_or_proceeds(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-CROSS-CUSTOMER-001")
    result = await service.start(StartWorkflowRequest(ticket_reference=ref))
    assert result.state == WorkflowState.BLOCKED
    assert result.resolved_order_id is None


async def test_duplicate_trigger_returns_same_run(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-TRACKING-001")
    first = await service.start(
        StartWorkflowRequest(ticket_reference=ref, process_immediately=False)
    )
    second = await service.start(
        StartWorkflowRequest(ticket_reference=ref, process_immediately=False)
    )
    assert first.run_id == second.run_id


async def test_concurrent_processors_cannot_both_claim(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-TRACKING-001")
    run = await service.start(
        StartWorkflowRequest(ticket_reference=ref, process_immediately=False)
    )
    sm = service.session_factory
    now = seed_reference_clock().now()
    async with sm() as sa, sm() as sb:
        claim_a = await WorkflowRepository(sa).claim(
            run.run_id, worker_id="A", lease_seconds=60, now=now
        )
        claim_b = await WorkflowRepository(sb).claim(
            run.run_id, worker_id="B", lease_seconds=60, now=now
        )
        assert (claim_a is None) != (claim_b is None)
        await sa.rollback()
        await sb.rollback()


async def test_every_completed_step_has_a_checkpoint(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-TRACKING-001")
    result = await service.start(StartWorkflowRequest(ticket_reference=ref))
    async with service.session_factory() as session:
        repo = WorkflowRepository(session)
        steps = await repo.list_steps(result.run_id)
        checkpoints = await repo.list_checkpoints(result.run_id)
    completed = [s for s in steps if s.status.value == "completed"]
    # One checkpoint per completed step, plus the initial checkpoint at index 0.
    assert len(checkpoints) == len(completed) + 1


async def test_checkpoints_verify_and_reject_tampering(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-TRACKING-001")
    result = await service.start(StartWorkflowRequest(ticket_reference=ref))
    async with service.session_factory() as session:
        checkpoint = await WorkflowRepository(session).get_latest_checkpoint(
            result.run_id
        )
    assert checkpoint is not None
    verify_checkpoint(
        checkpoint.snapshot_json,
        checkpoint.snapshot_hash,
        checkpoint.state_schema_version,
    )
    tampered = dict(checkpoint.snapshot_json)
    tampered["approval_required"] = not tampered.get("approval_required", False)
    with pytest.raises(CheckpointError):
        verify_checkpoint(
            tampered, checkpoint.snapshot_hash, checkpoint.state_schema_version
        )


async def test_cancel_is_safe_and_terminal(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-REFUND-APPROVAL-001")
    run = await service.start(StartWorkflowRequest(ticket_reference=ref))
    cancelled = await service.cancel(
        CancelWorkflowRequest(run_id=run.run_id, reason="test cancel")
    )
    assert cancelled.state == WorkflowState.CANCELLED
    assert cancelled.status == WorkflowStatus.CANCELLED
    with pytest.raises(ValueError, match="terminal"):
        await service.cancel(CancelWorkflowRequest(run_id=run.run_id, reason="again"))


async def test_replay_links_source_and_leaves_it_unchanged(
    service: SupportWorkflowService,
) -> None:
    ref = await _ref(service, "DEMO-TRACKING-001")
    original = await service.start(StartWorkflowRequest(ticket_reference=ref))
    replay = await service.replay(ReplayWorkflowRequest(run_id=original.run_id))
    assert replay.replay.replay_source_run_id == original.run_id
    assert replay.replay.run_id != original.run_id
    assert replay.diff.identical
    source_after = await service.summary(original.run_id)
    assert source_after.state == original.state
    assert source_after.replay_source_run_id is None
