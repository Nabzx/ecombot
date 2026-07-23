"""Synthetic business-effect references for simulated actions (S6).

References like ``SIM-REF-2026-000001`` are **demonstration** identifiers. They make it
obvious in every log, API response and summary that no external payment processor or
store was ever contacted. They are not external transaction ids.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.enums import ExecutionActionType
from app.models.execution import ExecutedAction

_PREFIX = {
    ExecutionActionType.SIMULATED_REFUND: "SIM-REF",
    ExecutionActionType.SIMULATED_ORDER_CANCELLATION: "SIM-CAN",
}


async def next_reference(
    session: AsyncSession, *, action_type: ExecutionActionType, year: int
) -> str:
    """A sequential-looking, clearly-simulated reference for a new effect."""
    stmt = select(func.count()).where(ExecutedAction.action_type == action_type.value)
    existing = (await session.execute(stmt)).scalar_one()
    prefix = _PREFIX[action_type]
    return f"{prefix}-{year}-{existing + 1:06d}"
