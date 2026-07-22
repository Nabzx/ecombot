"""Typed async repositories for approval requests and immutable decisions (S6).

Decisions are append-only. Approval-request rows are locked with ``FOR UPDATE`` during a
decision so two Supervisors cannot decide the same request simultaneously. No actor
identity is ever taken from request data; callers pass the authenticated actor.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Select, case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approvals.enums import ApprovalDecisionType, ApprovalStatus
from app.models.approval import ApprovalDecision, ApprovalRequest

# Risk ordering for the queue (higher risk surfaces first).
_RISK_RANK = case(
    (ApprovalRequest.risk_level == "high", 0),
    (ApprovalRequest.risk_level == "medium", 1),
    else_=2,
)


class ApprovalRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, request: ApprovalRequest) -> ApprovalRequest:
        self._session.add(request)
        await self._session.flush()
        return request

    async def get(self, approval_id: uuid.UUID) -> ApprovalRequest | None:
        return await self._session.get(ApprovalRequest, approval_id)

    async def get_for_update(self, approval_id: uuid.UUID) -> ApprovalRequest | None:
        """Lock the approval row for an exclusive decision transaction."""
        stmt = (
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id)
            .with_for_update()
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_open_for_action(
        self, proposed_action_id: uuid.UUID
    ) -> ApprovalRequest | None:
        stmt = select(ApprovalRequest).where(
            ApprovalRequest.proposed_action_id == proposed_action_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_queue(
        self,
        *,
        status: ApprovalStatus | None = None,
        requester_user_id: uuid.UUID | None = None,
        workflow_run_id: uuid.UUID | None = None,
        risk_level: str | None = None,
        action_type: str | None = None,
        limit: int = 25,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        stmt: Select[tuple[ApprovalRequest]] = select(ApprovalRequest)
        if status is not None:
            stmt = stmt.where(ApprovalRequest.status == status)
        if requester_user_id is not None:
            stmt = stmt.where(ApprovalRequest.requester_user_id == requester_user_id)
        if workflow_run_id is not None:
            stmt = stmt.where(ApprovalRequest.workflow_run_id == workflow_run_id)
        if risk_level is not None:
            stmt = stmt.where(ApprovalRequest.risk_level == risk_level)
        if action_type is not None:
            stmt = stmt.where(ApprovalRequest.action_type == action_type)
        # Deterministic: expiring soon, then higher risk, then oldest created.
        stmt = (
            stmt.order_by(
                ApprovalRequest.expires_at.asc(),
                _RISK_RANK.asc(),
                ApprovalRequest.created_at.asc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list((await self._session.execute(stmt)).scalars())

    async def list_due_for_expiry(
        self, *, now: datetime, limit: int
    ) -> list[ApprovalRequest]:
        stmt = (
            select(ApprovalRequest)
            .where(
                ApprovalRequest.status == ApprovalStatus.PENDING,
                ApprovalRequest.expires_at <= now,
            )
            .order_by(ApprovalRequest.expires_at.asc())
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return list((await self._session.execute(stmt)).scalars())


class ApprovalDecisionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def append(
        self,
        *,
        approval_request_id: uuid.UUID,
        decision: ApprovalDecisionType,
        actor_user_id: uuid.UUID | None,
        actor_role: str,
        previous_status: str,
        new_status: str,
        now: datetime,
        reason: str | None = None,
        requested_amount_pence: int | None = None,
        decided_amount_pence: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> ApprovalDecision:
        row = ApprovalDecision(
            approval_request_id=approval_request_id,
            decision=decision,
            actor_user_id=actor_user_id,
            actor_role=actor_role,
            reason=reason,
            previous_status=previous_status,
            new_status=new_status,
            requested_amount_pence=requested_amount_pence,
            decided_amount_pence=decided_amount_pence,
            decision_metadata_json=metadata or {},
            created_at=now,
        )
        self._session.add(row)
        await self._session.flush()
        return row

    async def list_for_request(
        self, approval_request_id: uuid.UUID
    ) -> list[ApprovalDecision]:
        stmt = (
            select(ApprovalDecision)
            .where(ApprovalDecision.approval_request_id == approval_request_id)
            .order_by(ApprovalDecision.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars())

    async def latest(self, approval_request_id: uuid.UUID) -> ApprovalDecision | None:
        stmt = (
            select(ApprovalDecision)
            .where(ApprovalDecision.approval_request_id == approval_request_id)
            .order_by(ApprovalDecision.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()
