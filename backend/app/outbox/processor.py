"""The outbox processor: run one job through exactly-once execution (S6).

This is where at-least-once delivery becomes exactly-once effect. The processor:

* records an immutable attempt row (so a crash mid-execution is always explained),
* re-verifies the payload and runs final deterministic revalidation against locked rows,
* treats an already-succeeded idempotency key as idempotent success (no second effect),
* applies the action's effect, the outbox/approval/proposal/workflow updates and the
  final checkpoint **in a single transaction**, and
* classifies any failure into retry / dead-letter / manual-handling.

The processor never approves anything and never calls a model.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.actions.context import ActionExecutionContext, ActionExecutionResult
from app.actions.enums import RefundEntryType
from app.actions.errors import (
    ExecutionError,
    ExecutionErrorCode,
    ExecutionErrorKind,
    business,
)
from app.actions.registry import get_handler_spec
from app.actions.repository import (
    ExecutedActionRepository,
    LedgerRefundHistory,
    RefundLedgerRepository,
    result_hash,
)
from app.actions.revalidation import revalidate_before_execution
from app.approvals.enums import ApprovalStatus
from app.approvals.repository import ApprovalRequestRepository
from app.audit.enums import AuditEventType
from app.audit.service import AuditService
from app.core.config import Settings, get_settings
from app.models.approval import ApprovalRequest
from app.models.enums import TicketStatus
from app.models.execution import ExecutedAction, RefundLedgerEntry
from app.models.order import Order
from app.models.outbox import OutboxJob
from app.models.ticket import Ticket
from app.models.workflow import ProposedAction, WorkflowRun
from app.outbox.enums import UNCLAIMABLE_STATUSES, OutboxStatus
from app.outbox.payload import OutboxJobData, PayloadError, load_payload
from app.outbox.repository import OutboxAttemptRepository, OutboxRepository
from app.outbox.retry import next_attempt_at
from app.rules.clock import Clock, SystemClock
from app.tracing.exporters import default_exporter
from app.tracing.spans import Tracer
from app.workflows.checkpointing import build_snapshot, restore_state
from app.workflows.definition import STATE_SCHEMA_VERSION
from app.workflows.enums import ProposedActionStatus, WorkflowState, is_terminal
from app.workflows.registry import get_definition
from app.workflows.repository import WorkflowRepository

FailureInjector = Callable[[OutboxJobData, int], ExecutionError | None]


class ProcessOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    DUPLICATE = "duplicate"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTER = "dead_letter"
    FAILED = "failed"
    MANUAL_ACTION_REQUIRED = "manual_action_required"
    SKIPPED = "skipped"


@dataclass
class ProcessResult:
    outcome: ProcessOutcome
    job_id: uuid.UUID
    error_code: str | None = None
    business_effect_reference: str | None = None


class OutboxProcessor:
    """Processes a single outbox job with exactly-once business effects."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
        failure_injector: FailureInjector | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._factory = session_factory
        self._settings = settings or get_settings()
        self._clock = clock or SystemClock()
        self._injector = failure_injector
        self._tracer = tracer or Tracer(default_exporter(), clock=self._clock)

    def _now(self) -> datetime:
        value = self._clock.now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    # -- public entry ----------------------------------------------------------------
    async def process_job(self, job_id: uuid.UUID) -> ProcessResult:
        attempt_number = await self._start_attempt(job_id)
        if attempt_number is None:
            return ProcessResult(ProcessOutcome.SKIPPED, job_id)
        with self._tracer.trace(
            "outbox.process_job", trace_id=job_id.hex[:16], job_id=str(job_id)
        ):
            try:
                async with self._factory() as session:
                    result = await self._execute(session, job_id, attempt_number)
                    await session.commit()
                    return result
            except ExecutionError as exc:
                return await self._handle_failure(job_id, attempt_number, exc)

    # -- phase 1: attempt start ------------------------------------------------------
    async def _start_attempt(self, job_id: uuid.UUID) -> int | None:
        async with self._factory() as session:
            repo = OutboxRepository(session)
            job = await repo.get_for_update(job_id)
            if job is None or job.status in UNCLAIMABLE_STATUSES:
                return None
            attempts = OutboxAttemptRepository(session)
            number = await attempts.next_attempt_number(job_id)
            await attempts.start(
                job_id=job_id,
                attempt_number=number,
                worker_id=self._settings.worker_id,
                previous_status=job.status.value,
                lease_expires_at=job.lease_expires_at,
                now=self._now(),
            )
            await repo.mark_processing(job)
            await session.commit()
            return number

    # -- phase 2: execution (one atomic transaction) ---------------------------------
    async def _execute(
        self, session: AsyncSession, job_id: uuid.UUID, attempt_number: int
    ) -> ProcessResult:
        repo = OutboxRepository(session)
        job = await repo.get_for_update(job_id)
        if job is None:
            raise business(ExecutionErrorCode.ORDER_NOT_FOUND, "job vanished")
        if job.status == OutboxStatus.SUCCEEDED:
            await self._finish_attempt(session, job_id, attempt_number, "succeeded")
            return ProcessResult(ProcessOutcome.DUPLICATE, job_id)

        try:
            payload = load_payload(job.payload_json, job.payload_hash)
        except PayloadError as exc:
            raise business(ExecutionErrorCode.PAYLOAD_TAMPERED, str(exc)) from exc

        if self._injector is not None:
            injected = self._injector(payload, attempt_number)
            if injected is not None:
                raise injected

        now = self._now()

        # Idempotent success first: a prior committed effect for this business action
        # wins, before any revalidation — the workflow is already terminal by then.
        executed_repo = ExecutedActionRepository(session)
        existing = await executed_repo.get_by_idempotency_key(
            payload.business_idempotency_key
        )
        if existing is not None:
            await repo.mark_succeeded(job, now=now)
            await self._finish_attempt(session, job_id, attempt_number, "succeeded")
            return ProcessResult(
                ProcessOutcome.DUPLICATE,
                job_id,
                business_effect_reference=existing.business_effect_reference,
            )

        approvals = ApprovalRequestRepository(session)
        approval = await approvals.get_for_update(payload.approval_request_id)
        if approval is None:
            raise business(
                ExecutionErrorCode.APPROVAL_NOT_EXECUTABLE, "approval not found"
            )
        proposal = await session.get(ProposedAction, payload.proposed_action_id)
        run = await WorkflowRepository(session).get(payload.workflow_run_id)
        if proposal is None or run is None:
            raise business(
                ExecutionErrorCode.WORKFLOW_NOT_EXECUTABLE, "workflow/proposal missing"
            )

        order = await self._lock_order(session, payload.order_id)
        with self._tracer.span(
            "execution.revalidate", action=payload.action_type.value
        ):
            await revalidate_before_execution(
                session,
                payload=payload,
                approval=approval,
                proposal=proposal,
                run=run,
                order=order,
                now=now,
            )

        spec = get_handler_spec(payload.action_type)
        if spec is None:
            raise business(
                ExecutionErrorCode.UNSUPPORTED_ACTION,
                f"no handler for {payload.action_type.value}",
            )
        if order is None:
            raise business(ExecutionErrorCode.ORDER_NOT_FOUND, "order not found")

        # Enter the transient executing state (records a step + checkpoint).
        await self._record_transition(
            session, run, WorkflowState.EXECUTING_ACTION, "outbox_job_started", now
        )

        ctx = ActionExecutionContext(
            worker_id=self._settings.worker_id,
            clock=self._clock,
            refund_history=LedgerRefundHistory(session),
            correlation_id=run.correlation_id,
            attempt_number=attempt_number,
        )
        with self._tracer.span("execution.handler", handler=spec.handler_version):
            result = await spec.handler.execute(
                session, ctx, payload, order, order.shipment
            )
        with self._tracer.span("execution.apply_effect"):
            await self._apply_success(
                session,
                job=job,
                approval=approval,
                proposal=proposal,
                run=run,
                order=order,
                payload=payload,
                result=result,
                now=now,
            )
        await self._finish_attempt(session, job_id, attempt_number, "succeeded")
        return ProcessResult(
            ProcessOutcome.SUCCEEDED,
            job_id,
            business_effect_reference=result.business_effect_reference,
        )

    async def _apply_success(
        self,
        session: AsyncSession,
        *,
        job: object,
        approval: ApprovalRequest,
        proposal: ProposedAction,
        run: WorkflowRun,
        order: Order,
        payload: OutboxJobData,
        result: ActionExecutionResult,
        now: datetime,
    ) -> None:
        # 1. Immutable executed-action record (unique idempotency key + outbox job).
        executed = ExecutedAction(
            approval_request_id=approval.id,
            outbox_job_id=payload.outbox_job_id,
            workflow_run_id=run.id,
            ticket_id=approval.ticket_id,
            order_id=order.id,
            action_type=payload.action_type.value,
            idempotency_key=payload.business_idempotency_key,
            amount_pence=result.amount_pence,
            currency="GBP",
            business_effect_reference=result.business_effect_reference,
            precondition_snapshot_json=result.precondition_snapshot,
            precondition_snapshot_hash=result_hash(result.precondition_snapshot),
            result_json=result.result_json,
            result_hash=result_hash(result.result_json),
            executed_by=self._settings.worker_id,
            started_at=now,
            completed_at=now,
            created_at=now,
        )
        await ExecutedActionRepository(session).create_succeeded(executed)

        # 2. Exactly one refund-ledger entry, when the action moves money.
        if result.ledger_amount_pence is not None:
            await RefundLedgerRepository(session).add_entry(
                RefundLedgerEntry(
                    executed_action_id=executed.id,
                    order_id=order.id,
                    order_item_id=result.order_item_id,
                    amount_pence=result.ledger_amount_pence,
                    currency="GBP",
                    idempotency_key=payload.business_idempotency_key,
                    entry_type=RefundEntryType.REFUND,
                    reference=result.business_effect_reference,
                    created_at=now,
                )
            )

        # 3. Order status change.
        if result.new_order_status is not None:
            order.status = result.new_order_status

        # 4. Outbox / approval / proposal completion.
        await OutboxRepository(session).mark_succeeded(job, now=now)  # type: ignore[arg-type]
        approval.status = ApprovalStatus.EXECUTED
        approval.executed_at = now
        proposal.status = ProposedActionStatus.COMPLETED

        # 5. Terminal workflow transition + final checkpoint.
        await self._record_transition(
            session, run, WorkflowState.ACTION_SUCCEEDED, "action_succeeded", now
        )

        # 6. Resolve the ticket where appropriate.
        ticket = await session.get(Ticket, approval.ticket_id)
        if ticket is not None and ticket.status not in (
            TicketStatus.closed,
            TicketStatus.resolved,
        ):
            ticket.status = TicketStatus.resolved

        # 7. Audit the (simulated) effect in this same transaction.
        await AuditService(session).record(
            AuditEventType.ACTION_EXECUTED,
            occurred_at=now,
            subject_type="executed_action",
            subject_id=executed.id,
            actor_role="system",
            summary=f"simulated {payload.action_type.value} executed",
            metadata={
                "action_type": payload.action_type.value,
                "reference": result.business_effect_reference,
                "amount_pence": result.amount_pence,
                "outbox_job_id": str(payload.outbox_job_id),
                "simulated": True,
            },
            correlation_id=payload.business_idempotency_key,
        )
        await session.flush()

    # -- phase 3: failure classification (separate transaction) ----------------------
    async def _handle_failure(
        self, job_id: uuid.UUID, attempt_number: int, exc: ExecutionError
    ) -> ProcessResult:
        async with self._factory() as session:
            repo = OutboxRepository(session)
            job = await repo.get_for_update(job_id)
            if job is None:
                return ProcessResult(ProcessOutcome.SKIPPED, job_id)
            approval = await ApprovalRequestRepository(session).get_for_update(
                job.approval_request_id
            )
            run = (
                await WorkflowRepository(session).get(approval.workflow_run_id)
                if approval is not None
                else None
            )
            now = self._now()

            if exc.kind is ExecutionErrorKind.RETRYABLE_TECHNICAL:
                if attempt_number < job.maximum_attempts:
                    schedule_at = next_attempt_at(
                        now=now,
                        attempt=attempt_number,
                        base_seconds=self._settings.outbox_retry_base_seconds,
                        max_seconds=self._settings.outbox_retry_max_seconds,
                        jitter_ratio=self._settings.outbox_retry_jitter,
                        job_id=job.id,
                    )
                    await repo.schedule_retry(
                        job,
                        next_attempt_at=schedule_at,
                        error_code=exc.code.value,
                        error_message=exc.message,
                    )
                    await self._finish_attempt(
                        session,
                        job_id,
                        attempt_number,
                        "retry_scheduled",
                        error=exc,
                        retryable=True,
                    )
                    await session.commit()
                    return ProcessResult(
                        ProcessOutcome.RETRY_SCHEDULED,
                        job_id,
                        error_code=exc.code.value,
                    )
                await repo.mark_dead_letter(
                    job, now=now, error_code=exc.code.value, error_message=exc.message
                )
                await self._fail_approval_and_workflow(
                    session, approval, run, WorkflowState.ACTION_FAILED, exc, now
                )
                await self._finish_attempt(
                    session,
                    job_id,
                    attempt_number,
                    "dead_letter",
                    error=exc,
                    retryable=True,
                )
                await self._audit_failure(
                    session, job, AuditEventType.ACTION_DEAD_LETTERED, exc, now
                )
                await session.commit()
                return ProcessResult(
                    ProcessOutcome.DEAD_LETTER, job_id, error_code=exc.code.value
                )

            # Non-retryable: precondition change → manual handling; else → failed.
            destination = (
                WorkflowState.MANUAL_ACTION_REQUIRED
                if exc.kind is ExecutionErrorKind.PRECONDITION_CHANGED
                else WorkflowState.ACTION_FAILED
            )
            await repo.mark_failed(
                job, now=now, error_code=exc.code.value, error_message=exc.message
            )
            await self._fail_approval_and_workflow(
                session, approval, run, destination, exc, now
            )
            await self._finish_attempt(
                session,
                job_id,
                attempt_number,
                destination.value,
                error=exc,
                retryable=False,
            )
            await self._audit_failure(
                session,
                job,
                (
                    AuditEventType.ACTION_MANUAL_REQUIRED
                    if destination is WorkflowState.MANUAL_ACTION_REQUIRED
                    else AuditEventType.ACTION_FAILED
                ),
                exc,
                now,
            )
            await session.commit()
            outcome = (
                ProcessOutcome.MANUAL_ACTION_REQUIRED
                if destination is WorkflowState.MANUAL_ACTION_REQUIRED
                else ProcessOutcome.FAILED
            )
            return ProcessResult(outcome, job_id, error_code=exc.code.value)

    async def _audit_failure(
        self,
        session: AsyncSession,
        job: OutboxJob,
        event_type: AuditEventType,
        exc: ExecutionError,
        now: datetime,
    ) -> None:
        await AuditService(session).record(
            event_type,
            occurred_at=now,
            subject_type="outbox_job",
            subject_id=job.id,
            actor_role="system",
            summary=f"execution {event_type.value}: {exc.code.value}",
            metadata={
                "action_type": job.action_type,
                "error_code": exc.code.value,
                "error_kind": exc.kind.value,
                "attempt_count": job.attempt_count,
            },
            correlation_id=job.idempotency_key,
        )

    async def _fail_approval_and_workflow(
        self,
        session: AsyncSession,
        approval: ApprovalRequest | None,
        run: WorkflowRun | None,
        destination: WorkflowState,
        exc: ExecutionError,
        now: datetime,
    ) -> None:
        if approval is not None and approval.status == ApprovalStatus.EXECUTION_PENDING:
            approval.status = ApprovalStatus.EXECUTION_FAILED
        if run is None:
            return
        # Pass through the transient executing state so the state machine stays legal.
        if run.current_state == WorkflowState.APPROVED_PENDING_EXECUTION:
            await self._record_transition(
                session, run, WorkflowState.EXECUTING_ACTION, "outbox_job_started", now
            )
        await self._record_transition(
            session,
            run,
            destination,
            destination.value,
            now,
            reason=exc.code.value,
        )

    # -- helpers ---------------------------------------------------------------------
    async def _lock_order(
        self, session: AsyncSession, order_id: uuid.UUID | None
    ) -> Order | None:
        if order_id is None:
            return None
        # Lock the order row, then load its items and shipment (identity-mapped).
        locked = await session.get(Order, order_id, with_for_update=True)
        if locked is None:
            return None
        from app.repositories.order import OrderRepository

        return await OrderRepository(session).get_with_items(order_id)

    async def _finish_attempt(
        self,
        session: AsyncSession,
        job_id: uuid.UUID,
        attempt_number: int,
        result_status: str,
        *,
        error: ExecutionError | None = None,
        retryable: bool | None = None,
    ) -> None:
        attempts = OutboxAttemptRepository(session)
        rows = await attempts.list_for_job(job_id)
        current = next((a for a in rows if a.attempt_number == attempt_number), None)
        if current is None:
            return
        await attempts.finish(
            current,
            result_status=result_status,
            now=self._now(),
            error_code=error.code.value if error else None,
            error_message=error.message if error else None,
            retryable=retryable,
        )

    async def _record_transition(
        self,
        session: AsyncSession,
        run: WorkflowRun,
        destination: WorkflowState,
        step_name: str,
        now: datetime,
        *,
        reason: str | None = None,
    ) -> None:
        definition = get_definition(run.workflow_version)
        if not definition.is_valid_transition(run.current_state, destination):
            raise business(
                ExecutionErrorCode.WORKFLOW_NOT_EXECUTABLE,
                f"illegal transition {run.current_state.value}->{destination.value}",
            )
        repo = WorkflowRepository(session)
        index = run.step_index
        checkpoint = await repo.get_latest_checkpoint(run.id)
        state = restore_state(checkpoint.snapshot_json) if checkpoint else None
        metadata: dict[str, object] = {
            "actor_role": "system",
            "step": step_name,
        }
        if reason is not None:
            metadata["reason"] = reason
        step = await repo.start_step(
            run_id=run.id,
            step_index=index,
            step_name=step_name,
            source_state=run.current_state,
            attempt=1,
            input_hash="",
            input_summary_json=metadata,
            now=now,
        )
        new_index = index + 1
        await repo.complete_step(
            step,
            destination_state=destination,
            output_hash=None,
            output_summary_json=metadata,
            latency_ms=0,
            now=now,
            model_call_ids=[],
            tool_call_ids=[],
            citation_ids=[],
        )
        if state is not None:
            updated = state.model_copy(
                update={
                    "current_state": destination,
                    "step_index": new_index,
                    "current_step": step_name,
                }
            )
            # Keep the snapshot a valid, hash-matching state; metadata is on the step.
            snapshot_json, digest = build_snapshot(updated)
            new_checkpoint = await repo.append_checkpoint(
                run_id=run.id,
                step_index=new_index,
                state=destination,
                state_schema_version=STATE_SCHEMA_VERSION,
                snapshot_json=snapshot_json,
                snapshot_hash=digest,
                now=now,
            )
            await repo.set_last_checkpoint(run, new_checkpoint.id)
        if is_terminal(destination):
            await repo.mark_terminal(run, state=destination, now=now)
        else:
            await repo.update_state(
                run, state=destination, step_index=new_index, current_step=step_name
            )
