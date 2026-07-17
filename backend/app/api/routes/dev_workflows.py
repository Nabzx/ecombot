"""Development-only, workflow inspection and control endpoints.

Mounted only when ``ENVIRONMENT`` is development or test. No consequential execution, no
arbitrary workflow name/version, no arbitrary provider base URL, PII-safe output and
paginated lists. Uses the workflow service rather than duplicating logic.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.db.session import get_session, get_sessionmaker
from app.workflows.enums import ReplayMode
from app.workflows.repository import WorkflowRepository
from app.workflows.results import WorkflowRunResult
from app.workflows.service import (
    CancelWorkflowRequest,
    ReplayWorkflowRequest,
    ResumeWorkflowRequest,
    StartWorkflowRequest,
    SupportWorkflowService,
)

router = APIRouter(prefix="/api/dev/workflows", tags=["dev-workflows"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _service() -> SupportWorkflowService:
    return SupportWorkflowService(session_factory=get_sessionmaker())


class StartBody(BaseModel):
    ticket_reference: str = Field(min_length=1, max_length=40)
    process_immediately: bool = True


class ReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=200)


class ReplayBody(BaseModel):
    mode: ReplayMode = ReplayMode.DETERMINISTIC_MOCK


@router.post("/start")
async def start(body: StartBody) -> WorkflowRunResult:
    return await _service().start(
        StartWorkflowRequest(
            ticket_reference=body.ticket_reference,
            process_immediately=body.process_immediately,
        )
    )


@router.post("/{run_id}/run")
async def run(run_id: uuid.UUID) -> WorkflowRunResult:
    return await _service().run(run_id)


@router.get("")
async def list_runs(
    session: SessionDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict[str, object]:
    runs = await WorkflowRepository(session).list_runs(limit=limit, offset=offset)
    return {
        "runs": [
            {
                "run_id": str(r.id),
                "ticket_id": str(r.ticket_id),
                "state": r.current_state.value,
                "status": r.status.value,
                "step_index": r.step_index,
                "created_at": r.created_at.isoformat(),
            }
            for r in runs
        ]
    }


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID) -> WorkflowRunResult:
    return await _service().summary(run_id)


@router.get("/{run_id}/steps")
async def get_steps(run_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    steps = await WorkflowRepository(session).list_steps(run_id)
    return {
        "steps": [
            {
                "step_index": s.step_index,
                "step_name": s.step_name,
                "source_state": s.source_state.value,
                "destination_state": (
                    s.destination_state.value if s.destination_state else None
                ),
                "status": s.status.value,
                "attempt": s.attempt,
                "model_call_ids": s.model_call_ids,
                "tool_call_ids": s.tool_call_ids,
                "citation_ids": s.citation_ids,
            }
            for s in steps
        ]
    }


@router.get("/{run_id}/checkpoints")
async def get_checkpoints(run_id: uuid.UUID, session: SessionDep) -> dict[str, object]:
    checkpoints = await WorkflowRepository(session).list_checkpoints(run_id)
    return {
        "checkpoints": [
            {
                "step_index": c.step_index,
                "state": c.state.value,
                "snapshot_hash": c.snapshot_hash,
                "state_schema_version": c.state_schema_version,
                "created_at": c.created_at.isoformat(),
            }
            for c in checkpoints
        ]
    }


@router.post("/{run_id}/resume")
async def resume(run_id: uuid.UUID, body: ReasonBody) -> WorkflowRunResult:
    try:
        return await _service().resume(
            ResumeWorkflowRequest(run_id=run_id, reason=body.reason)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/cancel")
async def cancel(run_id: uuid.UUID, body: ReasonBody) -> WorkflowRunResult:
    try:
        return await _service().cancel(
            CancelWorkflowRequest(run_id=run_id, reason=body.reason)
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{run_id}/replay")
async def replay(run_id: uuid.UUID, body: ReplayBody) -> dict[str, object]:
    result = await _service().replay(
        ReplayWorkflowRequest(run_id=run_id, mode=body.mode)
    )
    return {
        "replay": result.replay.model_dump(mode="json"),
        "diff": result.diff.model_dump(mode="json"),
    }
