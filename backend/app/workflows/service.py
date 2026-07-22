"""High-level workflow service: start, run, resume, cancel and replay.

Owns the session and execution context; keeps routes/CLI thin. Uses the deterministic
seed-reference clock so date-based rules (e.g. return day-30 vs day-31) are reproducible
against the synthetic data. No consequential action is ever executed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.llm.service import ModelService
from app.repositories.ticket import TicketRepository
from app.rules.clock import Clock, seed_reference_clock
from app.workflows.checkpointing import build_snapshot
from app.workflows.context import WorkflowExecutionContext, WorkflowLimits
from app.workflows.definition import (
    STATE_SCHEMA_VERSION,
    WORKFLOW_NAME,
)
from app.workflows.enums import (
    ReplayMode,
    TriggerType,
    WorkflowFailureCode,
    WorkflowState,
    WorkflowStatus,
    is_terminal,
)
from app.workflows.registry import DEFAULT_WORKFLOW_VERSION
from app.workflows.repository import WorkflowRepository
from app.workflows.results import (
    WorkflowDiff,
    WorkflowReplayResult,
    WorkflowRunResult,
)
from app.workflows.runner import WorkflowRunner
from app.workflows.state import SupportWorkflowState


@dataclass
class StartWorkflowRequest:
    ticket_reference: str | None = None
    ticket_id: uuid.UUID | None = None
    trigger_type: TriggerType = TriggerType.TICKET_RECEIVED
    initiated_by_user_id: uuid.UUID | None = None
    requested_provider: str | None = None
    correlation_id: str | None = None
    process_immediately: bool = True
    mock_scenario: str = ""
    # New runs default to support-ticket-v2 (approval/execution capable); v1 can still
    # be requested explicitly and remains replayable.
    workflow_version: str = DEFAULT_WORKFLOW_VERSION


@dataclass
class ResumeWorkflowRequest:
    run_id: uuid.UUID
    reason: str | None = None


@dataclass
class CancelWorkflowRequest:
    run_id: uuid.UUID
    reason: str


@dataclass
class ReplayWorkflowRequest:
    run_id: uuid.UUID
    mode: ReplayMode = ReplayMode.DETERMINISTIC_MOCK


@dataclass
class SupportWorkflowService:
    session_factory: async_sessionmaker[AsyncSession]
    model_service: ModelService = field(default_factory=ModelService)
    worker_id: str = "workflow-worker-1"
    limits: WorkflowLimits = field(default_factory=WorkflowLimits)

    def _context(
        self, session: AsyncSession, correlation_id: str, *, mock_scenario: str = ""
    ) -> WorkflowExecutionContext:
        return WorkflowExecutionContext(
            session=session,
            correlation_id=correlation_id,
            worker_id=self.worker_id,
            clock=self._clock(),
            model_service=self.model_service,
            limits=self.limits,
            mock_scenario=mock_scenario,
        )

    @staticmethod
    def _clock() -> Clock:
        return seed_reference_clock()

    async def start(self, request: StartWorkflowRequest) -> WorkflowRunResult:
        async with self.session_factory() as session:
            repo = WorkflowRepository(session)
            tickets = TicketRepository(session)
            ticket = None
            if request.ticket_id is not None:
                ticket = await tickets.get(request.ticket_id)
            elif request.ticket_reference is not None:
                ticket = await tickets.get_by_reference(request.ticket_reference)
            if ticket is None:
                raise LookupError("ticket not found")

            existing = await repo.get_active_for_ticket(
                ticket.id, WORKFLOW_NAME, request.workflow_version
            )
            if existing is not None:
                return await self._summary(session, existing.id)

            correlation_id = request.correlation_id or f"wf-{uuid.uuid4().hex[:16]}"
            run = await repo.create_run(
                workflow_name=WORKFLOW_NAME,
                workflow_version=request.workflow_version,
                state_schema_version=STATE_SCHEMA_VERSION,
                ticket_id=ticket.id,
                correlation_id=correlation_id,
                trigger_type=request.trigger_type,
                initial_state=WorkflowState.RECEIVED,
                initial_step="receive",
                now=self._clock().now(),
                initiated_by_user_id=request.initiated_by_user_id,
            )
            state = SupportWorkflowState(
                workflow_run_id=run.id,
                workflow_name=WORKFLOW_NAME,
                workflow_version=request.workflow_version,
                ticket_id=ticket.id,
                ticket_reference=ticket.ticket_reference,
                correlation_id=correlation_id,
            )
            snapshot, digest = build_snapshot(state)
            checkpoint = await repo.append_checkpoint(
                run_id=run.id,
                step_index=0,
                state=WorkflowState.RECEIVED,
                state_schema_version=STATE_SCHEMA_VERSION,
                snapshot_json=snapshot,
                snapshot_hash=digest,
                now=self._clock().now(),
            )
            await repo.set_last_checkpoint(run, checkpoint.id)
            await session.commit()
            run_id = run.id

        if request.process_immediately:
            return await self._run(run_id, mock_scenario=request.mock_scenario)
        return await self.summary(run_id)

    async def run(self, run_id: uuid.UUID) -> WorkflowRunResult:
        return await self._run(run_id)

    async def _run(
        self, run_id: uuid.UUID, *, mock_scenario: str = ""
    ) -> WorkflowRunResult:
        async with self.session_factory() as session:
            ctx = self._context(
                session, f"wf-{uuid.uuid4().hex[:12]}", mock_scenario=mock_scenario
            )
            runner = WorkflowRunner(ctx)
            return await runner.run(run_id)

    async def resume(self, request: ResumeWorkflowRequest) -> WorkflowRunResult:
        async with self.session_factory() as session:
            repo = WorkflowRepository(session)
            run = await repo.get(request.run_id)
            if run is None:
                raise LookupError("workflow run not found")
            if is_terminal(run.current_state):
                raise ValueError("cannot resume a terminal run")
            if run.status == WorkflowStatus.PAUSED and not request.reason:
                raise ValueError("resuming a paused run requires an explicit reason")
            await repo.increment_resume(run)
            run.metadata_json = {
                **run.metadata_json,
                "resume_reason": request.reason or "internal",
            }
            await session.commit()
        # Continue from the last valid checkpoint (crash recovery for active runs).
        return await self._run(request.run_id)

    async def cancel(self, request: CancelWorkflowRequest) -> WorkflowRunResult:
        async with self.session_factory() as session:
            repo = WorkflowRepository(session)
            run = await repo.get(request.run_id)
            if run is None:
                raise LookupError("workflow run not found")
            if is_terminal(run.current_state):
                raise ValueError("cannot cancel a terminal run")
            run.metadata_json = {
                **run.metadata_json,
                "cancellation_reason": request.reason,
            }
            await repo.cancel_proposals(run.id)
            await repo.mark_terminal(
                run,
                state=WorkflowState.CANCELLED,
                now=self._clock().now(),
                failure_code=WorkflowFailureCode.CANCELLED,
                failure_message=request.reason,
            )
            # Preserve a final checkpoint for the cancelled run.
            checkpoint = await repo.get_latest_checkpoint(run.id)
            if checkpoint is not None:
                state = SupportWorkflowState.model_validate(checkpoint.snapshot_json)
                state = state.model_copy(
                    update={"current_state": WorkflowState.CANCELLED}
                )
                snapshot, digest = build_snapshot(state)
                await repo.append_checkpoint(
                    run_id=run.id,
                    step_index=run.step_index + 1,
                    state=WorkflowState.CANCELLED,
                    state_schema_version=STATE_SCHEMA_VERSION,
                    snapshot_json=snapshot,
                    snapshot_hash=digest,
                    now=self._clock().now(),
                )
            await session.commit()
            return await self._summary(session, run.id)

    async def replay(self, request: ReplayWorkflowRequest) -> WorkflowReplayResult:
        async with self.session_factory() as session:
            repo = WorkflowRepository(session)
            source = await repo.get(request.run_id)
            if source is None:
                raise LookupError("source run not found")
            source_summary = await self._summary(session, source.id)
            correlation_id = f"replay-{uuid.uuid4().hex[:12]}"
            replay_run = await repo.create_run(
                workflow_name=source.workflow_name,
                workflow_version=source.workflow_version,
                state_schema_version=STATE_SCHEMA_VERSION,
                ticket_id=source.ticket_id,
                correlation_id=correlation_id,
                trigger_type=TriggerType.REPLAY,
                initial_state=WorkflowState.RECEIVED,
                initial_step="receive",
                now=self._clock().now(),
                replay_source_run_id=source.id,
            )
            ticket_reference = source_summary.ticket_reference
            state = SupportWorkflowState(
                workflow_run_id=replay_run.id,
                workflow_name=source.workflow_name,
                workflow_version=source.workflow_version,
                ticket_id=source.ticket_id,
                ticket_reference=ticket_reference,
                correlation_id=correlation_id,
            )
            snapshot, digest = build_snapshot(state)
            cp = await repo.append_checkpoint(
                run_id=replay_run.id,
                step_index=0,
                state=WorkflowState.RECEIVED,
                state_schema_version=STATE_SCHEMA_VERSION,
                snapshot_json=snapshot,
                snapshot_hash=digest,
                now=self._clock().now(),
            )
            await repo.set_last_checkpoint(replay_run, cp.id)
            await session.commit()
            replay_id = replay_run.id

        replay_summary = await self._run(replay_id)
        diff = _diff(source.id, replay_id, source_summary, replay_summary)
        return WorkflowReplayResult(replay=replay_summary, diff=diff)

    async def summary(self, run_id: uuid.UUID) -> WorkflowRunResult:
        async with self.session_factory() as session:
            return await self._summary(session, run_id)

    async def _summary(
        self, session: AsyncSession, run_id: uuid.UUID
    ) -> WorkflowRunResult:
        run = await WorkflowRepository(session).get(run_id)
        if run is None:
            raise LookupError("workflow run not found")
        ctx = self._context(session, "summary")
        return await WorkflowRunner(ctx).summarise(run)


_DIFF_FIELDS = (
    "state",
    "status",
    "classification",
    "resolved_customer_id",
    "resolved_order_id",
    "risk_level",
    "recommended_route",
    "proposed_action",
    "approval_required",
    "failure_code",
    "step_count",
)


def _diff(
    source_id: uuid.UUID,
    replay_id: uuid.UUID,
    source: WorkflowRunResult,
    replay: WorkflowRunResult,
) -> WorkflowDiff:
    fields: dict[str, dict[str, object]] = {}
    for name in _DIFF_FIELDS:
        s_val = getattr(source, name)
        r_val = getattr(replay, name)
        s_norm = s_val.value if hasattr(s_val, "value") else s_val
        r_norm = r_val.value if hasattr(r_val, "value") else r_val
        fields[name] = {"source": s_norm, "replay": r_norm}
    return WorkflowDiff(source_run_id=source_id, replay_run_id=replay_id, fields=fields)
