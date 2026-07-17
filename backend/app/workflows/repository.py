"""Async repositories for workflow runs, checkpoints, steps, tool calls and proposals.

Concurrency control is **lease-based with row locking**: a worker claims a run via
``SELECT ... FOR UPDATE SKIP LOCKED`` (so a competing worker simply skips a locked row),
sets a time-bounded lease and bumps an optimistic ``lock_version``. Paused and terminal
runs are never claimable, and an expired lease is reclaimable — this needs no Redis.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.workflow import (
    ProposedAction,
    WorkflowCheckpoint,
    WorkflowRun,
    WorkflowStep,
    WorkflowToolCall,
)
from app.workflows.enums import (
    ProposedActionStatus,
    StepStatus,
    TriggerType,
    WorkflowFailureCode,
    WorkflowState,
    WorkflowStatus,
    status_for_state,
)

# Statuses a run may be claimed and advanced from.
CLAIMABLE_STATUSES: frozenset[WorkflowStatus] = frozenset(
    {WorkflowStatus.PENDING, WorkflowStatus.RUNNING}
)


class WorkflowRepository:
    """All workflow persistence operations over a single async session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # -- runs -----------------------------------------------------------------------
    async def create_run(
        self,
        *,
        workflow_name: str,
        workflow_version: str,
        state_schema_version: str,
        ticket_id: uuid.UUID,
        correlation_id: str,
        trigger_type: TriggerType,
        initial_state: WorkflowState,
        initial_step: str,
        now: datetime,
        initiated_by_user_id: uuid.UUID | None = None,
        replay_source_run_id: uuid.UUID | None = None,
    ) -> WorkflowRun:
        run = WorkflowRun(
            workflow_name=workflow_name,
            workflow_version=workflow_version,
            state_schema_version=state_schema_version,
            ticket_id=ticket_id,
            status=status_for_state(initial_state),
            current_state=initial_state,
            current_step=initial_step,
            step_index=0,
            correlation_id=correlation_id,
            trigger_type=trigger_type,
            initiated_by_user_id=initiated_by_user_id,
            started_at=now,
            retry_count=0,
            resume_count=0,
            replay_source_run_id=replay_source_run_id,
            metadata_json={},
            lock_version=0,
        )
        self._session.add(run)
        await self._session.flush()
        return run

    async def get(self, run_id: uuid.UUID) -> WorkflowRun | None:
        return await self._session.get(WorkflowRun, run_id)

    async def get_active_for_ticket(
        self, ticket_id: uuid.UUID, workflow_name: str, workflow_version: str
    ) -> WorkflowRun | None:
        active = {
            WorkflowStatus.PENDING,
            WorkflowStatus.RUNNING,
            WorkflowStatus.PAUSED,
        }
        stmt = (
            select(WorkflowRun)
            .where(
                WorkflowRun.ticket_id == ticket_id,
                WorkflowRun.workflow_name == workflow_name,
                WorkflowRun.workflow_version == workflow_version,
                WorkflowRun.status.in_(active),
            )
            .order_by(WorkflowRun.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_runs(
        self,
        *,
        state: WorkflowState | None = None,
        status: WorkflowStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[WorkflowRun]:
        stmt = select(WorkflowRun)
        if state is not None:
            stmt = stmt.where(WorkflowRun.current_state == state)
        if status is not None:
            stmt = stmt.where(WorkflowRun.status == status)
        stmt = stmt.order_by(WorkflowRun.created_at.desc()).limit(limit).offset(offset)
        return list(await self._session.scalars(stmt))

    async def claim(
        self,
        run_id: uuid.UUID,
        *,
        worker_id: str,
        lease_seconds: int,
        now: datetime,
    ) -> WorkflowRun | None:
        """Claim a run for exclusive processing, or return None if not claimable.

        Uses ``FOR UPDATE SKIP LOCKED`` so a competing worker holding the row lock makes
        this call return None rather than block.
        """
        run = await self._session.scalar(
            select(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .with_for_update(skip_locked=True)
        )
        if run is None:
            return None
        if run.status not in CLAIMABLE_STATUSES:
            return None
        if (
            run.claimed_by is not None
            and run.claim_expires_at is not None
            and run.claim_expires_at > now
        ):
            return None  # a live claim by another worker
        run.claimed_by = worker_id
        run.claim_expires_at = now + timedelta(seconds=lease_seconds)
        run.lock_version += 1
        await self._session.flush()
        return run

    async def release_claim(self, run: WorkflowRun) -> None:
        run.claimed_by = None
        run.claim_expires_at = None
        await self._session.flush()

    async def update_state(
        self,
        run: WorkflowRun,
        *,
        state: WorkflowState,
        step_index: int,
        current_step: str,
    ) -> None:
        run.current_state = state
        run.status = status_for_state(state)
        run.step_index = step_index
        run.current_step = current_step
        run.lock_version += 1
        await self._session.flush()

    async def increment_retry(self, run: WorkflowRun) -> None:
        run.retry_count += 1
        await self._session.flush()

    async def increment_resume(self, run: WorkflowRun) -> None:
        run.resume_count += 1
        await self._session.flush()

    async def mark_terminal(
        self,
        run: WorkflowRun,
        *,
        state: WorkflowState,
        now: datetime,
        failure_code: WorkflowFailureCode | None = None,
        failure_message: str | None = None,
    ) -> None:
        run.current_state = state
        run.status = status_for_state(state)
        run.finished_at = now
        run.failure_code = failure_code
        run.failure_message = failure_message
        run.claimed_by = None
        run.claim_expires_at = None
        run.lock_version += 1
        await self._session.flush()

    async def set_last_checkpoint(
        self, run: WorkflowRun, checkpoint_id: uuid.UUID
    ) -> None:
        run.last_checkpoint_id = checkpoint_id
        await self._session.flush()

    # -- checkpoints ----------------------------------------------------------------
    async def append_checkpoint(
        self,
        *,
        run_id: uuid.UUID,
        step_index: int,
        state: WorkflowState,
        state_schema_version: str,
        snapshot_json: dict[str, object],
        snapshot_hash: str,
        now: datetime,
    ) -> WorkflowCheckpoint:
        checkpoint = WorkflowCheckpoint(
            workflow_run_id=run_id,
            step_index=step_index,
            state=state,
            state_schema_version=state_schema_version,
            snapshot_json=snapshot_json,
            snapshot_hash=snapshot_hash,
            created_at=now,
        )
        self._session.add(checkpoint)
        await self._session.flush()
        return checkpoint

    async def get_latest_checkpoint(
        self, run_id: uuid.UUID
    ) -> WorkflowCheckpoint | None:
        stmt = (
            select(WorkflowCheckpoint)
            .where(WorkflowCheckpoint.workflow_run_id == run_id)
            .order_by(WorkflowCheckpoint.step_index.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def get_checkpoint_by_step(
        self, run_id: uuid.UUID, step_index: int
    ) -> WorkflowCheckpoint | None:
        stmt = select(WorkflowCheckpoint).where(
            WorkflowCheckpoint.workflow_run_id == run_id,
            WorkflowCheckpoint.step_index == step_index,
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_checkpoints(self, run_id: uuid.UUID) -> list[WorkflowCheckpoint]:
        return list(
            await self._session.scalars(
                select(WorkflowCheckpoint)
                .where(WorkflowCheckpoint.workflow_run_id == run_id)
                .order_by(WorkflowCheckpoint.step_index)
            )
        )

    # -- steps ----------------------------------------------------------------------
    async def start_step(
        self,
        *,
        run_id: uuid.UUID,
        step_index: int,
        step_name: str,
        source_state: WorkflowState,
        attempt: int,
        input_hash: str,
        input_summary_json: dict[str, object],
        now: datetime,
    ) -> WorkflowStep:
        step = WorkflowStep(
            workflow_run_id=run_id,
            step_index=step_index,
            step_name=step_name,
            source_state=source_state,
            status=StepStatus.STARTED,
            attempt=attempt,
            started_at=now,
            input_hash=input_hash,
            input_summary_json=input_summary_json,
            retryable=False,
            model_call_ids=[],
            tool_call_ids=[],
            citation_ids=[],
            metadata_json={},
        )
        self._session.add(step)
        await self._session.flush()
        return step

    async def complete_step(
        self,
        step: WorkflowStep,
        *,
        destination_state: WorkflowState,
        output_hash: str | None,
        output_summary_json: dict[str, object],
        latency_ms: int,
        now: datetime,
        model_call_ids: list[str],
        tool_call_ids: list[str],
        citation_ids: list[str],
    ) -> None:
        step.status = StepStatus.COMPLETED
        step.destination_state = destination_state
        step.output_hash = output_hash
        step.output_summary_json = output_summary_json
        step.latency_ms = latency_ms
        step.finished_at = now
        step.model_call_ids = model_call_ids
        step.tool_call_ids = tool_call_ids
        step.citation_ids = citation_ids
        await self._session.flush()

    async def fail_step(
        self,
        step: WorkflowStep,
        *,
        destination_state: WorkflowState | None,
        error_code: str,
        error_message: str,
        retryable: bool,
        latency_ms: int,
        now: datetime,
    ) -> None:
        step.status = StepStatus.FAILED
        step.destination_state = destination_state
        step.error_code = error_code
        step.error_message = error_message
        step.retryable = retryable
        step.latency_ms = latency_ms
        step.finished_at = now
        await self._session.flush()

    async def list_steps(self, run_id: uuid.UUID) -> list[WorkflowStep]:
        return list(
            await self._session.scalars(
                select(WorkflowStep)
                .where(WorkflowStep.workflow_run_id == run_id)
                .order_by(WorkflowStep.step_index, WorkflowStep.started_at)
            )
        )

    async def current_attempt(self, run_id: uuid.UUID, step_name: str) -> int:
        steps = await self._session.scalars(
            select(WorkflowStep).where(
                WorkflowStep.workflow_run_id == run_id,
                WorkflowStep.step_name == step_name,
            )
        )
        return len(list(steps)) + 1

    # -- tool calls -----------------------------------------------------------------
    async def record_tool_call(
        self,
        *,
        run_id: uuid.UUID,
        step_id: uuid.UUID,
        tool_name: str,
        tool_version: str,
        status: str,
        input_json: dict[str, object],
        output_json: dict[str, object] | None,
        error_code: str | None,
        retryable: bool,
        duration_ms: int,
        correlation_id: str,
        now: datetime,
    ) -> WorkflowToolCall:
        call = WorkflowToolCall(
            workflow_run_id=run_id,
            workflow_step_id=step_id,
            tool_name=tool_name,
            tool_version=tool_version,
            status=status,
            input_json=input_json,
            output_json=output_json,
            error_code=error_code,
            retryable=retryable,
            duration_ms=duration_ms,
            correlation_id=correlation_id,
            created_at=now,
        )
        self._session.add(call)
        await self._session.flush()
        return call

    # -- proposed actions -----------------------------------------------------------
    async def supersede_proposals(self, run_id: uuid.UUID) -> None:
        proposals = await self._session.scalars(
            select(ProposedAction).where(
                ProposedAction.workflow_run_id == run_id,
                ProposedAction.status.notin_(
                    [
                        ProposedActionStatus.SUPERSEDED,
                        ProposedActionStatus.CANCELLED,
                    ]
                ),
            )
        )
        for proposal in proposals:
            proposal.status = ProposedActionStatus.SUPERSEDED
        await self._session.flush()

    async def create_proposal(
        self,
        *,
        run_id: uuid.UUID,
        ticket_id: uuid.UUID,
        action_type: str,
        status: ProposedActionStatus,
        risk_level: str,
        required_role: str | None,
        approval_required: bool,
        amount_pence: int | None,
        idempotency_key: str | None,
        draft_response_subject: str,
        draft_response_body: str,
        citation_ids: list[str],
        rule_result_json: dict[str, object],
        decision_summary_json: dict[str, object],
    ) -> ProposedAction:
        proposal = ProposedAction(
            workflow_run_id=run_id,
            ticket_id=ticket_id,
            action_type=action_type,
            status=status,
            risk_level=risk_level,
            required_role=required_role,
            approval_required=approval_required,
            amount_pence=amount_pence,
            idempotency_key=idempotency_key,
            draft_response_subject=draft_response_subject,
            draft_response_body=draft_response_body,
            citation_ids=citation_ids,
            rule_result_json=rule_result_json,
            decision_summary_json=decision_summary_json,
        )
        self._session.add(proposal)
        await self._session.flush()
        return proposal

    async def get_current_proposal(self, run_id: uuid.UUID) -> ProposedAction | None:
        stmt = (
            select(ProposedAction)
            .where(
                ProposedAction.workflow_run_id == run_id,
                ProposedAction.status.notin_(
                    [
                        ProposedActionStatus.SUPERSEDED,
                        ProposedActionStatus.CANCELLED,
                    ]
                ),
            )
            .order_by(ProposedAction.created_at.desc())
            .limit(1)
        )
        return (await self._session.execute(stmt)).scalar_one_or_none()

    async def list_proposals(self, run_id: uuid.UUID) -> list[ProposedAction]:
        return list(
            await self._session.scalars(
                select(ProposedAction)
                .where(ProposedAction.workflow_run_id == run_id)
                .order_by(ProposedAction.created_at)
            )
        )

    async def cancel_proposals(self, run_id: uuid.UUID) -> None:
        proposals = await self._session.scalars(
            select(ProposedAction).where(
                ProposedAction.workflow_run_id == run_id,
                ProposedAction.status.notin_([ProposedActionStatus.CANCELLED]),
            )
        )
        for proposal in proposals:
            proposal.status = ProposedActionStatus.CANCELLED
        await self._session.flush()
