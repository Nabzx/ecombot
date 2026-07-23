"""Typed async repositories for executed actions and the refund ledger (S6).

Successful effects are immutable: a unique idempotency key and a unique outbox-job
reference guarantee at most one executed-action row per business action, and the refund
ledger has its own unique idempotency key. Result hashes let a reader verify a stored
result was not altered.
"""

from __future__ import annotations

import hashlib
import json
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.enums import ExecutedActionStatus, RefundEntryType
from app.models.execution import ExecutedAction, RefundLedgerEntry


def result_hash(result_json: dict[str, object]) -> str:
    canonical = json.dumps(result_json, sort_keys=True, ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ExecutedActionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, action_id: uuid.UUID) -> ExecutedAction | None:
        return await self._session.get(ExecutedAction, action_id)

    async def get_by_idempotency_key(self, key: str) -> ExecutedAction | None:
        stmt = select(ExecutedAction).where(ExecutedAction.idempotency_key == key)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_by_outbox_job(self, job_id: uuid.UUID) -> ExecutedAction | None:
        stmt = select(ExecutedAction).where(ExecutedAction.outbox_job_id == job_id)
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def create_succeeded(self, action: ExecutedAction) -> ExecutedAction:
        action.status = ExecutedActionStatus.SUCCEEDED
        self._session.add(action)
        await self._session.flush()
        return action

    async def list_actions(
        self,
        *,
        workflow_run_id: uuid.UUID | None = None,
        order_id: uuid.UUID | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ExecutedAction]:
        stmt = select(ExecutedAction)
        if workflow_run_id is not None:
            stmt = stmt.where(ExecutedAction.workflow_run_id == workflow_run_id)
        if order_id is not None:
            stmt = stmt.where(ExecutedAction.order_id == order_id)
        stmt = (
            stmt.order_by(ExecutedAction.created_at.desc(), ExecutedAction.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars())

    @staticmethod
    def verify_result_hash(action: ExecutedAction) -> bool:
        return result_hash(action.result_json) == action.result_hash


class RefundLedgerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_entry(self, entry: RefundLedgerEntry) -> RefundLedgerEntry:
        self._session.add(entry)
        await self._session.flush()
        return entry

    async def refunded_total_pence(self, order_id: uuid.UUID) -> int:
        """Sum of successful refund entries for an order (integer pence, 0 if none)."""
        stmt = select(func.coalesce(func.sum(RefundLedgerEntry.amount_pence), 0)).where(
            RefundLedgerEntry.order_id == order_id,
            RefundLedgerEntry.entry_type == RefundEntryType.REFUND,
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_for_order(self, order_id: uuid.UUID) -> list[RefundLedgerEntry]:
        stmt = (
            select(RefundLedgerEntry)
            .where(RefundLedgerEntry.order_id == order_id)
            .order_by(RefundLedgerEntry.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def get_by_idempotency_key(self, key: str) -> RefundLedgerEntry | None:
        stmt = select(RefundLedgerEntry).where(RefundLedgerEntry.idempotency_key == key)
        return (await self._session.execute(stmt)).scalar_one_or_none()


class LedgerRefundHistory:
    """Production ``RefundHistoryPort`` adapter backed by the refund ledger.

    Replaces the S2 ``NoRefundHistory`` stub on execution paths so prior refunds
    reduce the remaining refundable balance.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._ledger = RefundLedgerRepository(session)

    async def refunded_total_pence(self, order_id: uuid.UUID) -> int:
        return await self._ledger.refunded_total_pence(order_id)
