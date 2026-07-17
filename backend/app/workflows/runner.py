"""The workflow runner: advance a claimed run to the next pause or terminal state.

Transaction discipline: each step persists "started" and commits before external/model
work, then persists "completed" + checkpoint + run-state update and commits atomically.
A crash between the two leaves a started step and the last valid committed checkpoint,
from which resume continues — no consequential action is ever executed.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.llm.redaction import redact_json
from app.models.workflow import WorkflowRun
from app.workflows.checkpointing import build_snapshot
from app.workflows.context import StepExecutionResult, WorkflowExecutionContext
from app.workflows.definition import (
    STATE_SCHEMA_VERSION,
    TransitionSpec,
    is_valid_transition,
    next_handler,
    transition_spec,
)
from app.workflows.enums import (
    WorkflowFailureCode,
    WorkflowState,
    is_active,
    is_paused,
    is_terminal,
)
from app.workflows.handlers import StepHandler, get_handler
from app.workflows.repository import WorkflowRepository
from app.workflows.results import WorkflowRunResult
from app.workflows.state import SupportWorkflowState, snapshot_hash

_TERMINAL_FAILURES: dict[str, WorkflowFailureCode] = {
    "validation_failed": WorkflowFailureCode.VALIDATION_FAILED,
    "dependency_unavailable": WorkflowFailureCode.DEPENDENCY_UNAVAILABLE,
    "model_failed": WorkflowFailureCode.MODEL_FAILED,
    "ownership_blocked": WorkflowFailureCode.OWNERSHIP_BLOCKED,
}


class WorkflowRunner:
    """Advances one workflow run under an exclusive claim."""

    def __init__(self, ctx: WorkflowExecutionContext) -> None:
        self._ctx = ctx
        self._repo = WorkflowRepository(ctx.session)

    async def run(self, run_id: uuid.UUID) -> WorkflowRunResult:
        ctx = self._ctx
        now = ctx.clock.now()
        run = await self._repo.claim(
            run_id,
            worker_id=ctx.worker_id,
            lease_seconds=ctx.limits.lease_seconds,
            now=now,
        )
        if run is None:
            existing = await self._repo.get(run_id)
            if existing is None:
                raise LookupError(f"workflow run {run_id} not found")
            return await self._summary(existing)
        await ctx.session.commit()

        ctx.workflow_run_id = run.id
        state = await self._load_state(run)
        deadline = now + timedelta(seconds=ctx.limits.total_deadline_seconds)
        steps_run = 0

        while is_active(run.current_state):
            if steps_run >= ctx.limits.max_steps_per_invocation:
                break
            if ctx.clock.now() > deadline:
                await self._repo.mark_terminal(
                    run,
                    state=WorkflowState.FAILED_DEPENDENCY,
                    now=ctx.clock.now(),
                    failure_code=WorkflowFailureCode.DEADLINE_EXCEEDED,
                    failure_message="workflow deadline exceeded",
                )
                await ctx.session.commit()
                break
            state = await self._run_one_step(run, state)
            steps_run += 1
            if is_paused(run.current_state) or is_terminal(run.current_state):
                break

        if run.claimed_by == ctx.worker_id and is_active(run.current_state):
            await self._repo.release_claim(run)
            await ctx.session.commit()
        return await self._summary(run)

    async def _run_one_step(
        self, run: WorkflowRun, state: SupportWorkflowState
    ) -> SupportWorkflowState:
        ctx = self._ctx
        spec = transition_spec(run.current_state)
        assert spec is not None  # noqa: S101 - active states always have a spec
        handler = get_handler(spec.handler)
        index = run.step_index

        input_snapshot = redact_json(state.snapshot())
        input_hash = snapshot_hash(input_snapshot)
        attempt = await self._repo.current_attempt(run.id, spec.handler)
        step = await self._repo.start_step(
            run_id=run.id,
            step_index=index,
            step_name=spec.handler,
            source_state=run.current_state,
            attempt=attempt,
            input_hash=input_hash,
            input_summary_json={"state": run.current_state.value},
            now=ctx.clock.now(),
        )
        await ctx.session.commit()  # persist "started" before external work
        ctx.current_step_id = step.id

        result = await self._execute_with_retry(spec, handler, state)

        if not is_valid_transition(run.current_state, result.destination_state):
            await self._repo.fail_step(
                step,
                destination_state=None,
                error_code="internal_error",
                error_message=(
                    f"illegal transition {run.current_state.value}"
                    f"->{result.destination_state.value}"
                ),
                retryable=False,
                latency_ms=0,
                now=ctx.clock.now(),
            )
            await self._repo.mark_terminal(
                run,
                state=WorkflowState.FAILED_DEPENDENCY,
                now=ctx.clock.now(),
                failure_code=WorkflowFailureCode.INTERNAL_ERROR,
                failure_message="illegal transition produced by handler",
            )
            await ctx.session.commit()
            return state

        destination = result.destination_state
        new_index = index + 1
        state = state.model_copy(
            update={
                **result.state_fragment,
                "current_state": destination,
                "step_index": new_index,
                "current_step": next_handler(destination) or destination.value,
                "warnings": [*state.warnings, *result.warnings],
            }
        )
        await self._repo.complete_step(
            step,
            destination_state=destination,
            output_hash=snapshot_hash(redact_json(state.snapshot())),
            output_summary_json={"destination": destination.value},
            latency_ms=0,
            now=ctx.clock.now(),
            model_call_ids=result.model_call_ids,
            tool_call_ids=result.tool_call_ids,
            citation_ids=result.citation_ids,
        )
        snapshot, digest = build_snapshot(state)
        checkpoint = await self._repo.append_checkpoint(
            run_id=run.id,
            step_index=new_index,
            state=destination,
            state_schema_version=STATE_SCHEMA_VERSION,
            snapshot_json=snapshot,
            snapshot_hash=digest,
            now=ctx.clock.now(),
        )
        if is_terminal(destination):
            await self._repo.mark_terminal(
                run,
                state=destination,
                now=ctx.clock.now(),
                failure_code=_TERMINAL_FAILURES.get(result.failure_code or ""),
                failure_message=result.error_message,
            )
        else:
            await self._repo.update_state(
                run,
                state=destination,
                step_index=new_index,
                current_step=next_handler(destination) or destination.value,
            )
            if is_paused(destination):
                await self._repo.release_claim(run)
        await self._repo.set_last_checkpoint(run, checkpoint.id)
        await ctx.session.commit()  # completed + checkpoint + state, atomically
        return state

    async def _execute_with_retry(
        self, spec: TransitionSpec, handler: StepHandler, state: SupportWorkflowState
    ) -> StepExecutionResult:
        attempts = spec.retry_max_attempts + 1
        result = await handler(self._ctx, state)
        while (
            result.retryable and attempts > 1 and is_terminal(result.destination_state)
        ):
            attempts -= 1
            result = await handler(self._ctx, state)
        return result

    async def _load_state(self, run: WorkflowRun) -> SupportWorkflowState:
        checkpoint = await self._repo.get_latest_checkpoint(run.id)
        if checkpoint is not None:
            from app.workflows.checkpointing import restore_state, verify_checkpoint

            verify_checkpoint(
                checkpoint.snapshot_json,
                checkpoint.snapshot_hash,
                checkpoint.state_schema_version,
            )
            return restore_state(checkpoint.snapshot_json)
        # No checkpoint yet: build the initial state from the run and its ticket.
        from app.repositories.ticket import TicketRepository

        ticket = await TicketRepository(self._ctx.session).get_with_messages(
            run.ticket_id
        )
        reference = ticket.ticket_reference if ticket else ""
        return SupportWorkflowState(
            workflow_run_id=run.id,
            workflow_name=run.workflow_name,
            workflow_version=run.workflow_version,
            ticket_id=run.ticket_id,
            ticket_reference=reference,
            correlation_id=run.correlation_id,
            current_state=run.current_state,
            current_step=run.current_step,
            step_index=run.step_index,
        )

    async def summarise(self, run: WorkflowRun) -> WorkflowRunResult:
        return await self._summary(run)

    async def _summary(self, run: WorkflowRun) -> WorkflowRunResult:
        checkpoint = await self._repo.get_latest_checkpoint(run.id)
        steps = await self._repo.list_steps(run.id)
        checkpoints = await self._repo.list_checkpoints(run.id)
        state = None
        if checkpoint is not None:
            state = SupportWorkflowState.model_validate(checkpoint.snapshot_json)
        classification = None
        if state and state.classification:
            classification = str(state.classification.get("category"))
        draft = state.draft_response if state else None
        return WorkflowRunResult(
            run_id=run.id,
            ticket_id=run.ticket_id,
            ticket_reference=state.ticket_reference if state else "",
            workflow_name=run.workflow_name,
            workflow_version=run.workflow_version,
            status=run.status,
            state=run.current_state,
            step_count=len([s for s in steps if s.status.value == "completed"]),
            checkpoint_count=len(checkpoints),
            classification=classification,
            resolved_customer_id=state.resolved_customer_id if state else None,
            resolved_order_id=state.resolved_order_id if state else None,
            risk_level=state.risk_level if state else None,
            recommended_route=state.recommended_route if state else None,
            proposed_action=state.proposed_action if state else None,
            approval_required=state.approval_required if state else False,
            required_role=state.required_role if state else None,
            draft_subject=(
                str(draft.get("subject")) if isinstance(draft, dict) else None
            ),
            citation_ids=state.policy_citations if state else [],
            warnings=state.warnings if state else [],
            missing_information=state.missing_information if state else [],
            failure_code=run.failure_code.value if run.failure_code else None,
            failure_message=run.failure_message,
            retry_count=run.retry_count,
            resume_count=run.resume_count,
            replay_source_run_id=run.replay_source_run_id,
        )
