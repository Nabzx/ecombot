"""Audit-log hash-chain and event-coverage tests (S7).

Verifies the chain verifies and detects tampering, that every consequential decision and
execution writes an audit record transactionally, and that a rollback drops both.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.approvals.service import ApprovalService, CreateApprovalRequest
from app.audit.enums import AuditEventType
from app.audit.repository import AuditRepository
from app.audit.service import AuditService
from app.outbox.processor import OutboxProcessor, ProcessOutcome
from app.rules.clock import seed_reference_clock
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.test_outbox_execution import (  # reuse the execution harness
    _agent,
    _approved_job,
    maker,  # noqa: F401
)


def _event_types(rows: list) -> set[str]:
    return {r.event_type for r in rows}


async def test_chain_verifies_and_detects_tampering(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
    async with maker() as session:
        audit = AuditService(session)
        for i in range(3):
            await audit.record(
                AuditEventType.AUTH_LOGIN_SUCCEEDED,
                occurred_at=now,
                subject_type="auth",
                actor_role="user",
                summary=f"login {i}",
            )
        await session.commit()
        result = await AuditRepository(session).verify_chain()
        assert result.ok and result.checked == 3

    # Tamper with a middle row's summary; the chain must now fail.
    async with maker() as session:
        await session.execute(
            text("UPDATE audit_events SET summary = 'tampered' WHERE sequence = 2")
        )
        await session.commit()
        broken = await AuditRepository(session).verify_chain()
        assert not broken.ok
        assert broken.broken_sequence == 2


async def test_approval_and_execution_are_audited(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    job_id, approval_id, _ = await _approved_job(maker)
    # After approval: requested + approved + outbox-job-created events exist.
    async with maker() as session:
        rows = await AuditRepository(session).list_events(limit=100)
        types = _event_types(rows)
        assert AuditEventType.APPROVAL_REQUESTED.value in types
        assert AuditEventType.APPROVAL_APPROVED.value in types
        assert AuditEventType.OUTBOX_JOB_CREATED.value in types

    result = await OutboxProcessor(maker, clock=seed_reference_clock()).process_job(
        job_id
    )
    assert result.outcome == ProcessOutcome.SUCCEEDED
    async with maker() as session:
        rows = await AuditRepository(session).list_events(limit=100)
        assert AuditEventType.ACTION_EXECUTED.value in _event_types(rows)
        # The whole chain still verifies after real writes across transactions.
        assert (await AuditRepository(session).verify_chain()).ok
        # Approval and execution share one correlation id (the business action key).
        async with maker() as s2:
            job = await s2.scalar(
                text("SELECT idempotency_key FROM outbox_jobs WHERE id = :j"),
                {"j": str(job_id)},
            )
        chain = await AuditRepository(session).list_for_correlation(str(job))
        chain_types = _event_types(chain)
        assert AuditEventType.APPROVAL_APPROVED.value in chain_types
        assert AuditEventType.ACTION_EXECUTED.value in chain_types


async def test_audit_rolls_back_with_its_event(
    maker: async_sessionmaker[AsyncSession],  # noqa: F811
) -> None:
    from app.models.ticket import Ticket
    from app.workflows.repository import WorkflowRepository
    from app.workflows.service import StartWorkflowRequest, SupportWorkflowService
    from sqlalchemy import select

    async with maker() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == "DEMO-REFUND-APPROVAL-001")
        )
        assert ticket is not None
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=maker).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    async with maker() as session:
        agent = await _agent(session)
        proposal = await WorkflowRepository(session).get_current_proposal(run.run_id)
        assert proposal is not None
        await ApprovalService(session, clock=seed_reference_clock()).create_request(
            CreateApprovalRequest(proposed_action_id=proposal.id), agent
        )
        before = await AuditRepository(session).count()
        # Roll back instead of committing: neither the approval nor its audit persist.
        await session.rollback()
    async with maker() as session:
        assert await AuditRepository(session).count() == 0
    assert before >= 1  # the audit row existed inside the rolled-back transaction
