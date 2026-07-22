"""Authenticated approval endpoints (S6).

Every route requires a bearer token; the actor is always taken from the token, never
from the request body, so an approval can never be attributed to someone else. Responses
are PII-safe summaries of the approval and its immutable decision history.

Execution is out of scope for this increment: approving moves the workflow to
``approved_pending_execution`` and queues nothing.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.idempotency import (
    IdempotencyKeyError,
    IdempotencyOutcome,
    IdempotencyStore,
    request_hash,
    validate_key,
)
from app.approvals.enums import ApprovalStatus
from app.approvals.errors import ApprovalError, http_status_for
from app.approvals.repository import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
)
from app.approvals.service import (
    ApprovalService,
    ApproveRequest,
    CancelApprovalRequest,
    CreateApprovalRequest,
    EditApprovalRequest,
    RejectRequest,
)
from app.auth.dependencies import CurrentUser
from app.auth.enums import Permission
from app.auth.models import AuthenticatedUser
from app.db.session import get_session
from app.models.approval import ApprovalDecision, ApprovalRequest
from app.rules.clock import seed_reference_clock

router = APIRouter(prefix="/api", tags=["approvals"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKeyHeader = Annotated[str | None, Header(alias="Idempotency-Key")]


# --- schemas ------------------------------------------------------------------------
class ApprovalSummary(BaseModel):
    """A PII-safe view of an approval request (no customer contact details)."""

    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    status: ApprovalStatus
    action_type: str
    risk_level: str
    required_role: str | None
    workflow_run_id: uuid.UUID
    ticket_id: uuid.UUID
    order_id: uuid.UUID | None
    requester_user_id: uuid.UUID
    requested_amount_pence: int | None
    maximum_allowed_amount_pence: int | None
    approved_amount_pence: int | None
    idempotency_key: str
    policy_citation_ids: list[str]
    evidence_snapshot_hash: str
    draft_response_subject: str | None
    request_reason: str | None
    created_at: str
    expires_at: str
    decided_at: str | None

    @classmethod
    def of(cls, row: ApprovalRequest) -> ApprovalSummary:
        return cls(
            id=row.id,
            status=row.status,
            action_type=row.action_type,
            risk_level=row.risk_level,
            required_role=row.required_role,
            workflow_run_id=row.workflow_run_id,
            ticket_id=row.ticket_id,
            order_id=row.order_id,
            requester_user_id=row.requester_user_id,
            requested_amount_pence=row.requested_amount_pence,
            maximum_allowed_amount_pence=row.maximum_allowed_amount_pence,
            approved_amount_pence=row.approved_amount_pence,
            idempotency_key=row.idempotency_key,
            policy_citation_ids=list(row.policy_citation_ids),
            evidence_snapshot_hash=row.evidence_snapshot_hash,
            draft_response_subject=row.draft_response_subject,
            request_reason=row.request_reason,
            created_at=row.created_at.isoformat(),
            expires_at=row.expires_at.isoformat(),
            decided_at=row.decided_at.isoformat() if row.decided_at else None,
        )


class DecisionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: uuid.UUID
    decision: str
    actor_user_id: uuid.UUID | None
    actor_role: str
    previous_status: str
    new_status: str
    reason: str | None
    requested_amount_pence: int | None
    decided_amount_pence: int | None
    created_at: str

    @classmethod
    def of(cls, row: ApprovalDecision) -> DecisionSummary:
        return cls(
            id=row.id,
            decision=row.decision.value,
            actor_user_id=row.actor_user_id,
            actor_role=row.actor_role,
            previous_status=row.previous_status,
            new_status=row.new_status,
            reason=row.reason,
            requested_amount_pence=row.requested_amount_pence,
            decided_amount_pence=row.decided_amount_pence,
            created_at=row.created_at.isoformat(),
        )


class DecisionOutcome(BaseModel):
    """The result of a decision, including what was deliberately *not* done."""

    model_config = ConfigDict(extra="forbid")

    approval: ApprovalSummary
    workflow_state: str
    outbox_job_created: bool = False


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposed_action_id: uuid.UUID
    request_reason: str | None = Field(default=None, max_length=1000)
    requested_amount_pence: int | None = Field(default=None, gt=0)


class EditBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_response_subject: str | None = Field(default=None, max_length=300)
    draft_response_body: str | None = Field(default=None, max_length=20000)
    request_reason: str | None = Field(default=None, max_length=1000)
    approved_amount_pence: int | None = Field(default=None, gt=0)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=1000)
    approved_amount_pence: int | None = Field(default=None, gt=0)
    draft_response_subject: str | None = Field(default=None, max_length=300)
    draft_response_body: str | None = Field(default=None, max_length=20000)


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=1000)


# --- helpers ------------------------------------------------------------------------
def _service(session: AsyncSession) -> ApprovalService:
    return ApprovalService(session, clock=seed_reference_clock())


def _fail(exc: ApprovalError) -> HTTPException:
    return HTTPException(
        status_code=http_status_for(exc.code),
        detail={"code": exc.code.value, "message": exc.message},
    )


async def _load(session: AsyncSession, approval_id: uuid.UUID) -> ApprovalRequest:
    row = await ApprovalRequestRepository(session).get(approval_id)
    if row is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return row


async def _outcome(
    session: AsyncSession, approval_id: uuid.UUID, state: str
) -> DecisionOutcome:
    return DecisionOutcome(
        approval=ApprovalSummary.of(await _load(session, approval_id)),
        workflow_state=state,
        outbox_job_created=False,
    )


async def _guard_idempotency(
    session: AsyncSession,
    key: str | None,
    *,
    actor: AuthenticatedUser,
    operation: str,
    payload: dict[str, object],
) -> tuple[str, str] | None:
    """Return (key, hash) for a new request, or None when no key was supplied.

    A replay of the same key+payload is reported to the caller as a conflict-free
    no-op by the route; a different payload under the same key is a 409.
    """
    if key is None:
        return None
    try:
        checked = validate_key(key)
    except IdempotencyKeyError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    digest = request_hash(payload)
    lookup = await IdempotencyStore(session).lookup(
        key=checked, actor_id=actor.user_id, operation=operation, req_hash=digest
    )
    if lookup.outcome is IdempotencyOutcome.CONFLICT:
        raise HTTPException(
            status_code=409, detail="idempotency key reused with a different payload"
        )
    if lookup.outcome is IdempotencyOutcome.REPLAY:
        raise _ReplayError(lookup.response_entity_id)
    return checked, digest


class _ReplayError(Exception):
    """Internal signal: this request already succeeded under the same key."""

    def __init__(self, entity_id: uuid.UUID | None) -> None:
        super().__init__("replay")
        self.entity_id = entity_id


# --- queue --------------------------------------------------------------------------
@router.get("/approvals")
async def list_approvals(
    session: SessionDep,
    user: CurrentUser,
    status: ApprovalStatus | None = None,
    risk_level: str | None = None,
    action_type: str | None = None,
    workflow_run_id: uuid.UUID | None = None,
    mine: bool = False,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> list[ApprovalSummary]:
    """The approval queue: expiring soonest, then highest risk, then oldest."""
    if not user.has(Permission.APPROVAL_QUEUE_READ):
        raise HTTPException(status_code=403, detail="missing permission")
    rows = await ApprovalRequestRepository(session).list_queue(
        status=status,
        risk_level=risk_level,
        action_type=action_type,
        workflow_run_id=workflow_run_id,
        requester_user_id=user.user_id if mine else None,
        limit=limit,
        offset=offset,
    )
    return [ApprovalSummary.of(row) for row in rows]


@router.get("/approvals/{approval_id}")
async def get_approval(
    approval_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> ApprovalSummary:
    if not user.has(Permission.APPROVAL_QUEUE_READ):
        raise HTTPException(status_code=403, detail="missing permission")
    return ApprovalSummary.of(await _load(session, approval_id))


@router.get("/approvals/{approval_id}/decisions")
async def list_decisions(
    approval_id: uuid.UUID, session: SessionDep, user: CurrentUser
) -> list[DecisionSummary]:
    """The full, append-only decision history for one approval."""
    if not user.has(Permission.APPROVAL_QUEUE_READ):
        raise HTTPException(status_code=403, detail="missing permission")
    await _load(session, approval_id)
    rows = await ApprovalDecisionRepository(session).list_for_request(approval_id)
    return [DecisionSummary.of(row) for row in rows]


# --- creation and editing -----------------------------------------------------------
@router.post("/proposed-actions/{action_id}/approval", status_code=201)
async def create_approval(
    action_id: uuid.UUID,
    body: CreateBody | None,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> ApprovalSummary:
    payload = body or CreateBody(proposed_action_id=action_id)
    try:
        guard = await _guard_idempotency(
            session,
            idempotency_key,
            actor=user,
            operation="create_approval",
            payload={"action_id": str(action_id), "reason": payload.request_reason},
        )
    except _ReplayError as replay:
        if replay.entity_id is not None:
            return ApprovalSummary.of(await _load(session, replay.entity_id))
        raise HTTPException(status_code=409, detail="duplicate request") from None

    try:
        result = await _service(session).create_request(
            CreateApprovalRequest(
                proposed_action_id=action_id,
                request_reason=payload.request_reason,
                requested_amount_pence=payload.requested_amount_pence,
            ),
            user,
        )
    except ApprovalError as exc:
        raise _fail(exc) from exc

    if guard is not None:
        await IdempotencyStore(session).store(
            key=guard[0],
            actor_id=user.user_id,
            operation="create_approval",
            req_hash=guard[1],
            entity_id=result.approval_id,
            now=seed_reference_clock().now(),
        )
    await session.commit()
    return ApprovalSummary.of(await _load(session, result.approval_id))


@router.patch("/approvals/{approval_id}")
async def edit_approval(
    approval_id: uuid.UUID, body: EditBody, session: SessionDep, user: CurrentUser
) -> ApprovalSummary:
    try:
        await _service(session).edit(
            approval_id,
            EditApprovalRequest(
                draft_response_subject=body.draft_response_subject,
                draft_response_body=body.draft_response_body,
                request_reason=body.request_reason,
                approved_amount_pence=body.approved_amount_pence,
            ),
            user,
        )
    except ApprovalError as exc:
        raise _fail(exc) from exc
    await session.commit()
    return ApprovalSummary.of(await _load(session, approval_id))


# --- decisions ----------------------------------------------------------------------
@router.post("/approvals/{approval_id}/approve")
async def approve(
    approval_id: uuid.UUID,
    body: ApproveBody | None,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKeyHeader = None,
) -> DecisionOutcome:
    payload = body or ApproveBody()
    try:
        guard = await _guard_idempotency(
            session,
            idempotency_key,
            actor=user,
            operation="approve",
            payload={
                "approval_id": str(approval_id),
                "amount": payload.approved_amount_pence,
            },
        )
    except _ReplayError:
        row = await _load(session, approval_id)
        return await _outcome(session, approval_id, row.status.value)

    try:
        result = await _service(session).approve(
            approval_id,
            ApproveRequest(
                reason=payload.reason,
                approved_amount_pence=payload.approved_amount_pence,
                draft_response_subject=payload.draft_response_subject,
                draft_response_body=payload.draft_response_body,
            ),
            user,
        )
    except ApprovalError as exc:
        raise _fail(exc) from exc

    if guard is not None:
        await IdempotencyStore(session).store(
            key=guard[0],
            actor_id=user.user_id,
            operation="approve",
            req_hash=guard[1],
            entity_id=approval_id,
            now=seed_reference_clock().now(),
        )
    await session.commit()
    return await _outcome(session, approval_id, result.workflow_state.value)


@router.post("/approvals/{approval_id}/reject")
async def reject(
    approval_id: uuid.UUID, body: ReasonBody, session: SessionDep, user: CurrentUser
) -> DecisionOutcome:
    try:
        result = await _service(session).reject(
            approval_id, RejectRequest(reason=body.reason), user
        )
    except ApprovalError as exc:
        raise _fail(exc) from exc
    await session.commit()
    return await _outcome(session, approval_id, result.workflow_state.value)


@router.post("/approvals/{approval_id}/cancel")
async def cancel(
    approval_id: uuid.UUID, body: ReasonBody, session: SessionDep, user: CurrentUser
) -> DecisionOutcome:
    try:
        result = await _service(session).cancel(
            approval_id, CancelApprovalRequest(reason=body.reason), user
        )
    except ApprovalError as exc:
        raise _fail(exc) from exc
    await session.commit()
    return await _outcome(session, approval_id, result.workflow_state.value)
