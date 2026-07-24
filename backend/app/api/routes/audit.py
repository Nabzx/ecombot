"""Authenticated read-only audit APIs (S7).

The audit trail is already PII-safe (identifiers, statuses, hashes only). The full log
and chain verification need ``outbox_inspect`` (supervisors); the per-correlation trace
view is available to anyone with ``action_status_read`` so an agent can follow a ticket
they can access. There is no endpoint that writes or mutates an audit event.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.repository import AuditRepository
from app.auth.dependencies import CurrentUser
from app.auth.enums import Permission
from app.db.session import get_session
from app.models.audit import AuditEvent

router = APIRouter(prefix="/api/audit", tags=["audit"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class AuditEventSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    sequence: int
    event_type: str
    actor_user_id: uuid.UUID | None
    actor_role: str
    subject_type: str
    subject_id: uuid.UUID | None
    correlation_id: str
    summary: str
    metadata: dict[str, object]
    previous_hash: str
    entry_hash: str
    occurred_at: str

    @classmethod
    def of(cls, row: AuditEvent) -> AuditEventSummary:
        return cls(
            id=row.id,
            sequence=row.sequence,
            event_type=row.event_type,
            actor_user_id=row.actor_user_id,
            actor_role=row.actor_role,
            subject_type=row.subject_type,
            subject_id=row.subject_id,
            correlation_id=row.correlation_id,
            summary=row.summary,
            metadata=row.metadata_json,
            previous_hash=row.previous_hash,
            entry_hash=row.entry_hash,
            occurred_at=row.occurred_at.isoformat(),
        )


class ChainVerificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    checked: int
    broken_sequence: int | None = None
    reason: str | None = None


def _require(user: CurrentUser, permission: Permission) -> None:
    if not user.has(permission):
        raise HTTPException(status_code=403, detail="missing permission")


@router.get("")
async def list_audit_events(
    session: SessionDep,
    user: CurrentUser,
    event_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[AuditEventSummary]:
    _require(user, Permission.OUTBOX_INSPECT)
    rows = await AuditRepository(session).list_events(
        event_type=event_type, limit=limit, offset=offset
    )
    return [AuditEventSummary.of(row) for row in rows]


@router.get("/verify")
async def verify_chain(
    session: SessionDep, user: CurrentUser
) -> ChainVerificationResponse:
    _require(user, Permission.OUTBOX_INSPECT)
    result = await AuditRepository(session).verify_chain()
    return ChainVerificationResponse(
        ok=result.ok,
        checked=result.checked,
        broken_sequence=result.broken_sequence,
        reason=result.reason,
    )


@router.get("/correlation/{correlation_id}")
async def trace_correlation(
    correlation_id: str, session: SessionDep, user: CurrentUser
) -> list[AuditEventSummary]:
    # Following one ticket's journey is available to agents and supervisors alike.
    _require(user, Permission.ACTION_STATUS_READ)
    rows = await AuditRepository(session).list_for_correlation(correlation_id)
    return [AuditEventSummary.of(row) for row in rows]


@router.get("/{event_id}")
async def get_audit_event(
    event_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> AuditEventSummary:
    _require(user, Permission.OUTBOX_INSPECT)
    row = await AuditRepository(session).get(event_id)
    if row is None:
        raise HTTPException(status_code=404, detail="audit event not found")
    return AuditEventSummary.of(row)
