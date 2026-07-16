"""PostgreSQL-backed tool tests via the executor."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from app.rules.clock import FixedClock
from app.tools.context import ToolContext
from app.tools.enums import READ_PERMISSIONS, ToolErrorCode
from app.tools.executor import execute_tool
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import build_sample

CLOCK = FixedClock(datetime(2026, 7, 16, 12, 0, tzinfo=UTC))


def _ctx(session: AsyncSession, **kw: object) -> ToolContext:
    return ToolContext(
        permissions=kw.get("permissions", READ_PERMISSIONS),  # type: ignore[arg-type]
        clock=CLOCK,
        session=session,
        customer_scope=kw.get("customer_scope"),  # type: ignore[arg-type]
    )


async def test_search_customer_masks_pii(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    result = await execute_tool(
        "search_customer", _ctx(db_session), {"name_query": "Jane"}
    )
    assert result.ok is True
    assert result.data is not None
    dumped = result.model_dump_json()
    assert "hashed_password" not in dumped
    assert "jane.doe@example.com" not in dumped  # full email never leaks
    assert "j***@example.com" in dumped


async def test_get_customer_not_found(db_session: AsyncSession) -> None:
    result = await execute_tool(
        "get_customer", _ctx(db_session), {"customer_id": str(uuid.uuid4())}
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ToolErrorCode.not_found


async def test_get_order_within_customer(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    result = await execute_tool(
        "get_order",
        _ctx(db_session),
        {"order_id": str(sample.order_a1.id), "customer_id": str(sample.customer_a.id)},
    )
    assert result.ok is True
    assert result.data is not None


async def test_get_order_cross_customer_blocked(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    result = await execute_tool(
        "get_order",
        _ctx(db_session),
        {"order_id": str(sample.order_a1.id), "customer_id": str(sample.customer_b.id)},
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ToolErrorCode.ownership_mismatch


async def test_permission_denied(db_session: AsyncSession) -> None:
    ctx = _ctx(db_session, permissions=frozenset())
    result = await execute_tool("search_customer", ctx, {"name_query": "Jane"})
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ToolErrorCode.forbidden


async def test_get_shipment_status(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    result = await execute_tool(
        "get_shipment_status",
        _ctx(db_session),
        {"order_id": str(sample.order_a1.id), "customer_id": str(sample.customer_a.id)},
    )
    assert result.ok is True
    assert result.data is not None


async def test_active_policy_retrieval(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    result = await execute_tool(
        "get_active_policy", _ctx(db_session), {"topic": "returns"}
    )
    assert result.ok is True
    assert result.data is not None


async def test_result_metadata_present(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    result = await execute_tool(
        "get_customer", _ctx(db_session), {"customer_id": str(sample.customer_a.id)}
    )
    assert result.metadata.duration_ms >= 0
    assert result.metadata.correlation_id
    assert result.metadata.tool_version == "get_customer-v1"


async def test_scope_enforced(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    # Context scoped to customer A cannot fetch under customer B.
    ctx = _ctx(db_session, customer_scope=sample.customer_a.id)
    result = await execute_tool(
        "get_order",
        ctx,
        {"order_id": str(sample.order_b1.id), "customer_id": str(sample.customer_b.id)},
    )
    assert result.ok is False
    assert result.error is not None
    assert result.error.code == ToolErrorCode.ownership_mismatch
