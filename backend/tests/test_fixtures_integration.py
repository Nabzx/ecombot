"""Integration: run the deterministic layer over the named demo fixtures (seeded DB)."""

from __future__ import annotations

from app.repositories.ticket import TicketRepository
from app.rules.clock import seed_reference_clock
from app.rules.enums import DecisionOutcome, ReasonCode
from app.rules.service import inspect_ticket
from app.seeds.runner import seed
from sqlalchemy.ext.asyncio import AsyncSession


async def _inspect(session: AsyncSession, seed_tag: str):  # type: ignore[no-untyped-def]
    ticket = await TicketRepository(session).get_by_seed_tag(seed_tag)
    assert ticket is not None, f"missing fixture {seed_tag}"
    return await inspect_ticket(
        session, ticket.ticket_reference, seed_reference_clock()
    )


async def test_demo_fixtures_produce_expected_results(db_session: AsyncSession) -> None:
    await seed(db_session)

    day30 = await _inspect(db_session, "DEMO-RETURN-DAY-30")
    assert day30.category_result is not None
    assert day30.category_result.outcome == DecisionOutcome.eligible
    assert day30.category_result.has(ReasonCode.RETURN_WITHIN_WINDOW)

    day31 = await _inspect(db_session, "DEMO-RETURN-DAY-31")
    assert day31.category_result is not None
    assert day31.category_result.has(ReasonCode.RETURN_WINDOW_EXPIRED)

    refund = await _inspect(db_session, "DEMO-REFUND-APPROVAL-001")
    assert refund.category_result is not None
    assert refund.category_result.approval_required is True
    assert refund.category_result.has(ReasonCode.REFUND_SUPERVISOR_APPROVAL_REQUIRED)

    injection = await _inspect(db_session, "DEMO-PROMPT-INJECTION-001")
    assert injection.routing.outcome == DecisionOutcome.escalate
    assert injection.routing.has(ReasonCode.INJECTION_FORCED_ESCALATION)

    cross = await _inspect(db_session, "DEMO-CROSS-CUSTOMER-001")
    assert cross.ownership.outcome == DecisionOutcome.blocked
    assert cross.ownership.has(ReasonCode.CROSS_CUSTOMER_ACCESS_BLOCKED)

    tracking = await _inspect(db_session, "DEMO-TRACKING-001")
    assert tracking.ownership.has(ReasonCode.ORDER_OWNERSHIP_CONFIRMED)


async def test_duplicate_refund_key_is_stable(db_session: AsyncSession) -> None:
    await seed(db_session)
    first = await _inspect(db_session, "DEMO-DUPLICATE-REFUND-001")
    second = await _inspect(db_session, "DEMO-DUPLICATE-REFUND-001")
    assert first.idempotency_key is not None
    assert first.idempotency_key == second.idempotency_key


async def test_policy_conflict_fixture_detected(db_session: AsyncSession) -> None:
    await seed(db_session)
    from app.tools.context import ToolContext
    from app.tools.enums import READ_PERMISSIONS
    from app.tools.executor import execute_tool

    ctx = ToolContext(
        permissions=READ_PERMISSIONS,
        clock=seed_reference_clock(),
        session=db_session,
    )
    result = await execute_tool(
        "validate_policy_versions", ctx, {"topic": "fixture_conflicting_returns"}
    )
    assert result.ok is True
    from app.rules.models import RuleResult

    assert isinstance(result.data, RuleResult)
    assert ReasonCode.POLICY_CONFLICT in result.data.reason_codes
