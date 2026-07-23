"""Authenticated read-only execution APIs (S6).

Exposes executed actions, the outbox queue, attempt history and worker statistics — all
PII-safe. There is **no** production endpoint that executes an action: execution happens
only in the worker. A single environment-gated dev endpoint can process one job through
the exact worker path for demos, and it is disabled outside development/test.

RBAC: action summaries need ``action_status_read`` (agents + supervisors); the outbox
queue, attempts and stats are diagnostics and need ``outbox_inspect`` (supervisors).
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.repository import ExecutedActionRepository
from app.auth.dependencies import CurrentUser
from app.auth.enums import Permission
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.models.execution import ExecutedAction
from app.models.outbox import OutboxJob
from app.models.outbox_attempt import OutboxAttempt
from app.outbox.enums import OutboxStatus
from app.outbox.repository import OutboxAttemptRepository, OutboxRepository

router = APIRouter(prefix="/api", tags=["execution"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


# --- schemas ------------------------------------------------------------------------
class ActionSummary(BaseModel):
    """A PII-safe view of one executed (simulated) action."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    action_type: str
    status: str
    business_effect_reference: str
    amount_pence: int | None
    currency: str
    approval_request_id: uuid.UUID
    workflow_run_id: uuid.UUID
    order_id: uuid.UUID
    result_hash: str
    completed_at: str

    @classmethod
    def of(cls, row: ExecutedAction) -> ActionSummary:
        return cls(
            id=row.id,
            action_type=row.action_type,
            status=row.status.value,
            business_effect_reference=row.business_effect_reference,
            amount_pence=row.amount_pence,
            currency=row.currency,
            approval_request_id=row.approval_request_id,
            workflow_run_id=row.workflow_run_id,
            order_id=row.order_id,
            result_hash=row.result_hash,
            completed_at=row.completed_at.isoformat(),
        )


class OutboxJobSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: str
    action_type: str
    approval_request_id: uuid.UUID
    workflow_run_id: uuid.UUID
    idempotency_key: str
    payload_hash: str
    priority: int
    attempt_count: int
    maximum_attempts: int
    next_attempt_at: str
    last_error_code: str | None

    @classmethod
    def of(cls, row: OutboxJob) -> OutboxJobSummary:
        return cls(
            id=row.id,
            status=row.status.value,
            action_type=row.action_type,
            approval_request_id=row.approval_request_id,
            workflow_run_id=row.workflow_run_id,
            idempotency_key=row.idempotency_key,
            payload_hash=row.payload_hash,
            priority=row.priority,
            attempt_count=row.attempt_count,
            maximum_attempts=row.maximum_attempts,
            next_attempt_at=row.next_attempt_at.isoformat(),
            last_error_code=row.last_error_code,
        )


class AttemptSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attempt_number: int
    worker_id: str
    previous_status: str
    result_status: str | None
    error_code: str | None
    retryable: bool | None
    duration_ms: int | None
    started_at: str

    @classmethod
    def of(cls, row: OutboxAttempt) -> AttemptSummary:
        return cls(
            attempt_number=row.attempt_number,
            worker_id=row.worker_id,
            previous_status=row.previous_status,
            result_status=row.result_status,
            error_code=row.error_code,
            retryable=row.retryable,
            duration_ms=row.duration_ms,
            started_at=row.started_at.isoformat(),
        )


# --- actions (agents + supervisors) -------------------------------------------------
def _require(user: CurrentUser, permission: Permission) -> None:
    if not user.has(permission):
        raise HTTPException(status_code=403, detail="missing permission")


@router.get("/actions")
async def list_actions(
    session: SessionDep,
    user: CurrentUser,
    workflow_run_id: uuid.UUID | None = None,
    order_id: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ActionSummary]:
    _require(user, Permission.ACTION_STATUS_READ)
    rows = await ExecutedActionRepository(session).list_actions(
        workflow_run_id=workflow_run_id, order_id=order_id, limit=limit, offset=offset
    )
    return [ActionSummary.of(row) for row in rows]


@router.get("/actions/{action_id}")
async def get_action(
    action_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> ActionSummary:
    _require(user, Permission.ACTION_STATUS_READ)
    row = await ExecutedActionRepository(session).get(action_id)
    if row is None:
        raise HTTPException(status_code=404, detail="action not found")
    return ActionSummary.of(row)


# --- outbox (supervisors only) ------------------------------------------------------
@router.get("/outbox")
async def list_outbox(
    session: SessionDep,
    user: CurrentUser,
    status: OutboxStatus | None = None,
    workflow_run_id: uuid.UUID | None = None,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[OutboxJobSummary]:
    _require(user, Permission.OUTBOX_INSPECT)
    rows = await OutboxRepository(session).list_jobs(
        status=status, workflow_run_id=workflow_run_id, limit=limit, offset=offset
    )
    return [OutboxJobSummary.of(row) for row in rows]


@router.get("/outbox/stats")
async def outbox_stats(session: SessionDep, user: CurrentUser) -> dict[str, int]:
    _require(user, Permission.OUTBOX_INSPECT)
    return await OutboxRepository(session).counts_by_status()


@router.get("/outbox/{job_id}")
async def get_outbox_job(
    job_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> OutboxJobSummary:
    _require(user, Permission.OUTBOX_INSPECT)
    row = await OutboxRepository(session).get(job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return OutboxJobSummary.of(row)


@router.get("/outbox/{job_id}/attempts")
async def get_outbox_attempts(
    job_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[AttemptSummary]:
    _require(user, Permission.OUTBOX_INSPECT)
    rows = await OutboxAttemptRepository(session).list_for_job(job_id)
    return [AttemptSummary.of(row) for row in rows]
