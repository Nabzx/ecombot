"""The human-approval service (S6).

Creates approval requests from a proposed action, lets a Supervisor review/edit within
strict limits, and records approve / reject / cancel / expire decisions. Every decision
is an immutable row, moves the ``support-ticket-v2`` workflow through a validated
transition, and writes a workflow step plus checkpoint.

A successful approval for an automatically executable action atomically records the
decision, moves the workflow to ``approved_pending_execution`` and enqueues exactly one
durable outbox job (approval status ``execution_pending``). An approved action that is
not auto-executable is routed to ``manual_action_required`` with no job. Execution is
performed by the outbox worker, never here.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.enums import PROPOSED_TO_EXECUTION, ExecutionActionType
from app.actions.errors import ExecutionErrorCode
from app.approvals.enums import (
    ApprovalDecisionType,
    ApprovalStatus,
    is_valid_approval_transition,
)
from app.approvals.errors import ApprovalError, ApprovalErrorCode
from app.approvals.repository import (
    ApprovalDecisionRepository,
    ApprovalRequestRepository,
)
from app.approvals.snapshot import (
    ApprovalSnapshot,
    SnapshotError,
    build_business_idempotency_key,
    compute_snapshot_hash,
    hash_text,
    verify_snapshot,
)
from app.audit.enums import AuditEventType
from app.audit.service import AuditService
from app.auth.enums import Permission
from app.auth.models import AuthenticatedUser
from app.core.config import Settings, get_settings
from app.models.approval import ApprovalRequest
from app.models.enums import UserRole
from app.models.outbox import OutboxJob
from app.models.ticket import Ticket
from app.models.workflow import ProposedAction, WorkflowRun
from app.outbox.enums import OutboxStatus
from app.outbox.payload import OutboxJobData
from app.outbox.repository import OutboxRepository
from app.repositories.order import OrderRepository
from app.rules.clock import Clock, SystemClock
from app.workflows.checkpointing import build_snapshot, restore_state
from app.workflows.definition import STATE_SCHEMA_VERSION
from app.workflows.enums import (
    ProposedActionStatus,
    WorkflowState,
    is_terminal,
    is_valid_proposed_action_transition,
)
from app.workflows.registry import WORKFLOW_V2_VERSION, get_definition
from app.workflows.repository import WorkflowRepository

SYSTEM_ACTOR_ROLE = "system"

# Only these (technical) job failure codes may be retried by a Supervisor.
_RETRYABLE_JOB_ERROR_CODES: frozenset[str] = frozenset(
    {
        ExecutionErrorCode.TRANSIENT_DEPENDENCY.value,
        ExecutionErrorCode.LEASE_LOST.value,
        ExecutionErrorCode.INJECTED_FAILURE.value,
    }
)


# --- request/result types -----------------------------------------------------------
@dataclass
class CreateApprovalRequest:
    proposed_action_id: uuid.UUID
    request_reason: str | None = None
    requested_amount_pence: int | None = None


@dataclass
class ApproveRequest:
    reason: str | None = None
    approved_amount_pence: int | None = None
    draft_response_subject: str | None = None
    draft_response_body: str | None = None


@dataclass
class RejectRequest:
    reason: str


@dataclass
class CancelApprovalRequest:
    reason: str


@dataclass
class RetryApprovalRequest:
    reason: str | None = None


@dataclass
class EditApprovalRequest:
    draft_response_subject: str | None = None
    draft_response_body: str | None = None
    request_reason: str | None = None
    approved_amount_pence: int | None = None


@dataclass
class ApprovalResult:
    approval_id: uuid.UUID
    status: ApprovalStatus
    workflow_run_id: uuid.UUID
    workflow_state: WorkflowState
    snapshot_hash: str
    requested_amount_pence: int | None = None
    maximum_allowed_amount_pence: int | None = None
    approved_amount_pence: int | None = None
    created: bool = True
    outbox_job_created: bool = False
    outbox_job_id: uuid.UUID | None = None
    manual_action_required: bool = False


@dataclass
class ExpirySweepResult:
    expired_ids: list[uuid.UUID] = field(default_factory=list)
    skipped: int = 0

    @property
    def expired_count(self) -> int:
        return len(self.expired_ids)


class ApprovalService:
    """Approval creation, editing, decisions, expiry and atomic outbox enqueue."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._clock = clock or SystemClock()
        self._requests = ApprovalRequestRepository(session)
        self._decisions = ApprovalDecisionRepository(session)
        self._workflows = WorkflowRepository(session)
        self._audit = AuditService(session)

    async def _audit_approval(
        self,
        event_type: AuditEventType,
        approval: ApprovalRequest,
        *,
        actor: AuthenticatedUser | None,
        now: datetime,
        summary: str,
        extra: dict[str, object] | None = None,
    ) -> None:
        """Record an audit event for an approval, in this same transaction."""
        metadata: dict[str, object] = {
            "approval_id": str(approval.id),
            "action_type": approval.action_type,
            "risk_level": approval.risk_level,
            "status": approval.status.value,
        }
        if extra:
            metadata.update(extra)
        await self._audit.record(
            event_type,
            occurred_at=now,
            subject_type="approval",
            subject_id=approval.id,
            actor_user_id=actor.user_id if actor else None,
            actor_role=actor.role.value if actor else SYSTEM_ACTOR_ROLE,
            summary=summary,
            metadata=metadata,
            correlation_id=approval.idempotency_key,
        )

    # -- creation --------------------------------------------------------------------
    async def create_request(
        self, request: CreateApprovalRequest, actor: AuthenticatedUser
    ) -> ApprovalResult:
        self._require_active(actor)
        self._require_permission(actor, Permission.APPROVAL_REQUEST_CREATE)

        proposal = await self._session.get(ProposedAction, request.proposed_action_id)
        if proposal is None:
            raise ApprovalError(
                ApprovalErrorCode.PROPOSAL_NOT_FOUND, "proposed action not found"
            )
        if proposal.status == ProposedActionStatus.BLOCKED:
            raise ApprovalError(
                ApprovalErrorCode.BLOCKED_PROPOSAL,
                "a blocked proposal can never be approved",
            )
        if proposal.status == ProposedActionStatus.SUPERSEDED:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_SUPERSEDED, "proposal has been superseded"
            )
        if proposal.status != ProposedActionStatus.AWAITING_APPROVAL:
            raise ApprovalError(
                ApprovalErrorCode.PROPOSAL_NOT_AWAITING_APPROVAL,
                f"proposal status is {proposal.status.value}",
            )
        if not proposal.approval_required:
            raise ApprovalError(
                ApprovalErrorCode.PROPOSAL_NOT_AWAITING_APPROVAL,
                "proposal does not require supervisor approval",
            )

        run = await self._load_v2_run_awaiting_approval(proposal.workflow_run_id)

        # An identical open request is returned rather than duplicated (idempotent).
        existing = await self._requests.get_open_for_action(proposal.id)
        if existing is not None:
            return self._result(existing, created=False)

        requested, maximum = await self._resolve_amounts(proposal, request)
        if requested is not None and maximum is not None and requested > maximum:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_MAXIMUM,
                "requested amount exceeds the deterministic maximum",
            )

        now = self._now()
        idempotency_key = build_business_idempotency_key(
            action_type=proposal.action_type,
            order_id=await self._order_id_for(proposal),
            amount_pence=requested,
        )
        snapshot = await self._build_snapshot(
            proposal=proposal,
            run=run,
            requester_id=actor.user_id,
            requested=requested,
            maximum=maximum,
            idempotency_key=idempotency_key,
            now=now,
        )
        snapshot_hash = compute_snapshot_hash(snapshot)

        approval = ApprovalRequest(
            workflow_run_id=run.id,
            proposed_action_id=proposal.id,
            ticket_id=proposal.ticket_id,
            order_id=snapshot.order_id,
            requester_user_id=actor.user_id,
            status=ApprovalStatus.PENDING,
            action_type=proposal.action_type,
            risk_level=proposal.risk_level,
            required_role=proposal.required_role,
            requested_amount_pence=requested,
            maximum_allowed_amount_pence=maximum,
            approved_amount_pence=None,
            idempotency_key=idempotency_key,
            policy_citation_ids=list(proposal.citation_ids),
            policy_version_ids=list(snapshot.policy_version_ids),
            rule_result_json=dict(proposal.rule_result_json),
            rule_result_hash=hash_text(str(sorted(proposal.rule_result_json.items()))),
            evidence_snapshot_json=snapshot.model_dump(mode="json"),
            evidence_snapshot_hash=snapshot_hash,
            draft_response_subject=proposal.draft_response_subject,
            draft_response_body=proposal.draft_response_body,
            request_reason=request.request_reason,
            # created_at is set from the same clock as expires_at so the deterministic
            # seed clock cannot violate the expires_at > created_at constraint.
            created_at=now,
            expires_at=now + timedelta(hours=self._settings.approval_expiry_hours),
        )
        await self._requests.create(approval)
        await self._audit_approval(
            AuditEventType.APPROVAL_REQUESTED,
            approval,
            actor=actor,
            now=now,
            summary="approval requested by agent",
        )
        # The workflow stays paused in awaiting_approval; creation is not a decision.
        return self._result(approval, created=True)

    # -- editing ---------------------------------------------------------------------
    async def edit(
        self,
        approval_id: uuid.UUID,
        request: EditApprovalRequest,
        actor: AuthenticatedUser,
    ) -> ApprovalResult:
        self._require_active(actor)
        approval = await self._requests.get_for_update(approval_id)
        if approval is None:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_NOT_FOUND, "approval not found"
            )
        if approval.status != ApprovalStatus.PENDING:
            raise ApprovalError(
                ApprovalErrorCode.EDIT_NOT_ALLOWED,
                "only a pending approval can be edited",
            )
        is_requester = actor.user_id == approval.requester_user_id
        if not (is_requester or actor.is_supervisor):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only the requester or a supervisor may edit this request",
            )
        if request.approved_amount_pence is not None and not actor.is_supervisor:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only a supervisor may change the amount",
            )

        if request.draft_response_subject is not None:
            approval.draft_response_subject = request.draft_response_subject
        if request.draft_response_body is not None:
            approval.draft_response_body = request.draft_response_body
        if request.request_reason is not None:
            approval.request_reason = request.request_reason
        if request.approved_amount_pence is not None:
            self._validate_amount(approval, request.approved_amount_pence)
            approval.approved_amount_pence = request.approved_amount_pence

        await self._rehash(approval)
        return self._result(approval, created=False)

    # -- decisions -------------------------------------------------------------------
    async def approve(
        self,
        approval_id: uuid.UUID,
        request: ApproveRequest,
        actor: AuthenticatedUser,
    ) -> ApprovalResult:
        approval = await self._lock_pending_for_decision(approval_id, actor)
        self._require_permission(actor, Permission.APPROVAL_DECIDE)
        if not actor.is_supervisor:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only a supervisor may approve",
            )
        # A requester can never approve their own proposal, whatever their role.
        if actor.user_id == approval.requester_user_id:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_SELF_DECISION_FORBIDDEN,
                "the requester may not approve their own request",
            )
        self._verify_snapshot(approval)
        run = await self._load_v2_run_awaiting_approval(approval.workflow_run_id)

        # Optional supervisor edits applied inside the decision transaction.
        if request.draft_response_subject is not None:
            approval.draft_response_subject = request.draft_response_subject
        if request.draft_response_body is not None:
            approval.draft_response_body = request.draft_response_body
        amount = request.approved_amount_pence
        if amount is None:
            amount = approval.requested_amount_pence
        if amount is not None:
            self._validate_amount(approval, amount)
        approval.approved_amount_pence = amount

        now = self._now()
        self._transition(approval, ApprovalStatus.APPROVED)
        approval.decided_at = now
        await self._rehash(approval)
        decision = await self._decisions.append(
            approval_request_id=approval.id,
            decision=ApprovalDecisionType.APPROVE,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            previous_status=ApprovalStatus.PENDING.value,
            new_status=ApprovalStatus.APPROVED.value,
            now=now,
            reason=request.reason,
            requested_amount_pence=approval.requested_amount_pence,
            decided_amount_pence=amount,
            metadata={"snapshot_hash": approval.evidence_snapshot_hash},
        )
        await self._set_proposal_status(
            approval, ProposedActionStatus.APPROVED_PENDING_EXECUTION
        )

        execution_action = PROPOSED_TO_EXECUTION.get(approval.action_type)
        if execution_action is None:
            # Not automatically executable (e.g. replacement / return authorisation):
            # record the approval but route the run straight to a human. No job.
            await self._apply_workflow_transition(
                run,
                destination=WorkflowState.MANUAL_ACTION_REQUIRED,
                step_name="manual_action_required",
                approval=approval,
                actor=actor,
                reason="action is not automatically executable",
            )
            await self._audit_approval(
                AuditEventType.APPROVAL_APPROVED,
                approval,
                actor=actor,
                now=now,
                summary="approved; manual handling required (not auto-executable)",
            )
            return self._result(
                approval,
                created=False,
                state=run.current_state,
                manual_action_required=True,
            )

        await self._apply_workflow_transition(
            run,
            destination=WorkflowState.APPROVED_PENDING_EXECUTION,
            step_name="approval_granted",
            approval=approval,
            actor=actor,
        )

        # Atomically enqueue exactly one durable outbox job and move the approval to
        # execution_pending. The unique idempotency key + one-job-per-approval index
        # make a duplicate job impossible even under a concurrent decision.
        job = await self._create_outbox_job(
            approval=approval,
            decision_id=decision.id,
            run=run,
            execution_action=execution_action,
            now=now,
        )
        self._transition(approval, ApprovalStatus.EXECUTION_PENDING)
        await self._audit_approval(
            AuditEventType.APPROVAL_APPROVED,
            approval,
            actor=actor,
            now=now,
            summary="approved by supervisor",
            extra={"approved_amount_pence": approval.approved_amount_pence},
        )
        await self._audit_approval(
            AuditEventType.OUTBOX_JOB_CREATED,
            approval,
            actor=actor,
            now=now,
            summary="outbox job enqueued for execution",
            extra={"outbox_job_id": str(job.id)},
        )
        return self._result(
            approval,
            created=False,
            state=run.current_state,
            outbox_job_created=True,
            outbox_job_id=job.id,
        )

    async def reject(
        self,
        approval_id: uuid.UUID,
        request: RejectRequest,
        actor: AuthenticatedUser,
    ) -> ApprovalResult:
        if not request.reason or not request.reason.strip():
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_AMOUNT_INVALID,
                "a rejection reason is required",
            )
        approval = await self._lock_pending_for_decision(approval_id, actor)
        self._require_permission(actor, Permission.APPROVAL_DECIDE)
        if not actor.is_supervisor:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only a supervisor may reject",
            )
        if actor.user_id == approval.requester_user_id:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_SELF_DECISION_FORBIDDEN,
                "the requester may not decide their own request",
            )
        run = await self._load_v2_run_awaiting_approval(approval.workflow_run_id)

        now = self._now()
        self._transition(approval, ApprovalStatus.REJECTED)
        approval.decided_at = now
        await self._decisions.append(
            approval_request_id=approval.id,
            decision=ApprovalDecisionType.REJECT,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            previous_status=ApprovalStatus.PENDING.value,
            new_status=ApprovalStatus.REJECTED.value,
            now=now,
            reason=request.reason,
            requested_amount_pence=approval.requested_amount_pence,
        )
        await self._set_proposal_status(approval, ProposedActionStatus.REJECTED)
        await self._apply_workflow_transition(
            run,
            destination=WorkflowState.APPROVAL_REJECTED,
            step_name="approval_rejected",
            approval=approval,
            actor=actor,
        )
        await self._audit_approval(
            AuditEventType.APPROVAL_REJECTED,
            approval,
            actor=actor,
            now=now,
            summary="rejected by supervisor",
        )
        return self._result(approval, created=False, state=run.current_state)

    async def cancel(
        self,
        approval_id: uuid.UUID,
        request: CancelApprovalRequest,
        actor: AuthenticatedUser,
    ) -> ApprovalResult:
        self._require_active(actor)
        approval = await self._requests.get_for_update(approval_id)
        if approval is None:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_NOT_FOUND, "approval not found"
            )
        self._require_pending(approval)
        # The requesting agent, or any supervisor, may cancel.
        if not (actor.user_id == approval.requester_user_id or actor.is_supervisor):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only the requester or a supervisor may cancel",
            )
        run = await self._load_v2_run_awaiting_approval(approval.workflow_run_id)

        now = self._now()
        self._transition(approval, ApprovalStatus.CANCELLED)
        approval.decided_at = now
        await self._decisions.append(
            approval_request_id=approval.id,
            decision=ApprovalDecisionType.CANCEL,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            previous_status=ApprovalStatus.PENDING.value,
            new_status=ApprovalStatus.CANCELLED.value,
            now=now,
            reason=request.reason,
        )
        await self._set_proposal_status(approval, ProposedActionStatus.CANCELLED)
        # Cancelling the approval returns the ticket to a human agent rather than
        # terminating the workflow: the proposal is withdrawn, not refused.
        await self._apply_workflow_transition(
            run,
            destination=WorkflowState.AWAITING_AGENT,
            step_name="approval_cancelled",
            approval=approval,
            actor=actor,
        )
        await self._audit_approval(
            AuditEventType.APPROVAL_CANCELLED,
            approval,
            actor=actor,
            now=now,
            summary="approval request cancelled",
        )
        return self._result(approval, created=False, state=run.current_state)

    # -- expiry ----------------------------------------------------------------------
    async def expire_due_requests(self, *, limit: int = 50) -> ExpirySweepResult:
        """Expire pending approvals whose expiry has passed (system-generated event)."""
        now = self._now()
        due = await self._requests.list_due_for_expiry(now=now, limit=limit)
        result = ExpirySweepResult()
        for approval in due:
            if approval.status != ApprovalStatus.PENDING:
                result.skipped += 1
                continue
            run = await self._workflows.get(approval.workflow_run_id)
            self._transition(approval, ApprovalStatus.EXPIRED)
            approval.decided_at = now
            await self._decisions.append(
                approval_request_id=approval.id,
                decision=ApprovalDecisionType.EXPIRE,
                actor_user_id=None,
                actor_role=SYSTEM_ACTOR_ROLE,
                previous_status=ApprovalStatus.PENDING.value,
                new_status=ApprovalStatus.EXPIRED.value,
                now=now,
                reason="approval expired without a decision",
            )
            if run is not None and run.current_state == WorkflowState.AWAITING_APPROVAL:
                await self._apply_workflow_transition(
                    run,
                    destination=WorkflowState.APPROVAL_EXPIRED,
                    step_name="approval_expired",
                    approval=approval,
                    actor=None,
                )
            await self._audit_approval(
                AuditEventType.APPROVAL_EXPIRED,
                approval,
                actor=None,
                now=now,
                summary="approval expired without a decision",
            )
            result.expired_ids.append(approval.id)
        return result

    # -- supervisor retry authorisation ----------------------------------------------
    async def retry(
        self,
        approval_id: uuid.UUID,
        request: RetryApprovalRequest,
        actor: AuthenticatedUser,
    ) -> ApprovalResult:
        """Authorise re-execution of a technically-failed (or dead-lettered) job.

        Only technical failures are retryable. Snapshot tampering, ownership mismatch,
        over-limit refunds, shipped orders, expired approvals and unsupported actions
        are never retried — those must go through a fresh review.
        """
        self._require_active(actor)
        approval = await self._requests.get_for_update(approval_id)
        if approval is None:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_NOT_FOUND, "approval not found"
            )
        self._require_permission(actor, Permission.APPROVAL_DECIDE)
        if not actor.is_supervisor:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                "only a supervisor may authorise a retry",
            )
        if actor.user_id == approval.requester_user_id:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_SELF_DECISION_FORBIDDEN,
                "the requester may not authorise their own retry",
            )
        if approval.status != ApprovalStatus.EXECUTION_FAILED:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_NOT_PENDING,
                f"approval is {approval.status.value}, not execution_failed",
            )
        if approval.expires_at <= self._now():
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_EXPIRED, "the approval has expired"
            )
        self._verify_snapshot(approval)

        job = await OutboxRepository(self._session).get_by_approval(approval.id)
        if job is None:
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT, "no outbox job to retry"
            )
        if job.status not in (OutboxStatus.FAILED, OutboxStatus.DEAD_LETTER):
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT,
                f"job status {job.status.value} is not retryable",
            )
        # Only a technical/dead-letter failure may be retried, never a business block.
        if job.last_error_code not in _RETRYABLE_JOB_ERROR_CODES:
            raise ApprovalError(
                ApprovalErrorCode.EDIT_NOT_ALLOWED,
                f"failure {job.last_error_code} is not eligible for retry",
            )
        run = await self._workflows.get(approval.workflow_run_id)
        if run is None or run.current_state != WorkflowState.ACTION_FAILED:
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT,
                "workflow is not in action_failed",
            )

        now = self._now()
        self._transition(approval, ApprovalStatus.EXECUTION_PENDING)
        await self._decisions.append(
            approval_request_id=approval.id,
            decision=ApprovalDecisionType.RETRY_AUTHORISED,
            actor_user_id=actor.user_id,
            actor_role=actor.role.value,
            previous_status=ApprovalStatus.EXECUTION_FAILED.value,
            new_status=ApprovalStatus.EXECUTION_PENDING.value,
            now=now,
            reason=request.reason,
        )
        await OutboxRepository(self._session).reset_for_retry(
            job, now=now, maximum_attempts=self._settings.outbox_max_attempts
        )
        await self._apply_workflow_transition(
            run,
            destination=WorkflowState.APPROVED_PENDING_EXECUTION,
            step_name="retry_authorised",
            approval=approval,
            actor=actor,
            reason="supervisor authorised retry",
        )
        await self._audit_approval(
            AuditEventType.APPROVAL_RETRY_AUTHORISED,
            approval,
            actor=actor,
            now=now,
            summary="supervisor authorised execution retry",
            extra={"outbox_job_id": str(job.id)},
        )
        return self._result(
            approval,
            created=False,
            state=run.current_state,
            outbox_job_created=True,
            outbox_job_id=job.id,
        )

    # -- internals -------------------------------------------------------------------
    def _now(self) -> datetime:
        value = self._clock.now()
        return value if value.tzinfo else value.replace(tzinfo=UTC)

    @staticmethod
    def _require_active(actor: AuthenticatedUser) -> None:
        if not actor.is_active:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN, "user is inactive"
            )

    @staticmethod
    def _require_permission(actor: AuthenticatedUser, permission: Permission) -> None:
        if not actor.has(permission):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN,
                f"missing permission: {permission.value}",
            )

    def _require_pending(self, approval: ApprovalRequest) -> None:
        if approval.status == ApprovalStatus.PENDING:
            if approval.expires_at <= self._now():
                raise ApprovalError(
                    ApprovalErrorCode.APPROVAL_EXPIRED, "approval has expired"
                )
            return
        mapping = {
            ApprovalStatus.EXPIRED: ApprovalErrorCode.APPROVAL_EXPIRED,
            ApprovalStatus.CANCELLED: ApprovalErrorCode.APPROVAL_CANCELLED,
            ApprovalStatus.REJECTED: ApprovalErrorCode.APPROVAL_REJECTED,
            ApprovalStatus.SUPERSEDED: ApprovalErrorCode.APPROVAL_SUPERSEDED,
        }
        code = mapping.get(approval.status, ApprovalErrorCode.APPROVAL_NOT_PENDING)
        raise ApprovalError(code, f"approval is {approval.status.value}")

    async def _lock_pending_for_decision(
        self, approval_id: uuid.UUID, actor: AuthenticatedUser
    ) -> ApprovalRequest:
        self._require_active(actor)
        approval = await self._requests.get_for_update(approval_id)
        if approval is None:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_NOT_FOUND, "approval not found"
            )
        self._require_pending(approval)
        return approval

    @staticmethod
    def _transition(approval: ApprovalRequest, destination: ApprovalStatus) -> None:
        if not is_valid_approval_transition(approval.status, destination):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_CONCURRENT_DECISION,
                f"cannot move approval from {approval.status.value} "
                f"to {destination.value}",
            )
        approval.status = destination

    @staticmethod
    def _validate_amount(approval: ApprovalRequest, amount: int) -> None:
        if amount <= 0:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_AMOUNT_INVALID,
                "the approved amount must be positive",
            )
        if (
            approval.requested_amount_pence is not None
            and amount > approval.requested_amount_pence
        ):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_REQUESTED,
                "the approved amount may not exceed the requested amount",
            )
        if (
            approval.maximum_allowed_amount_pence is not None
            and amount > approval.maximum_allowed_amount_pence
        ):
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_MAXIMUM,
                "the approved amount may not exceed the deterministic maximum",
            )

    def _verify_snapshot(self, approval: ApprovalRequest) -> None:
        try:
            verify_snapshot(
                approval.evidence_snapshot_json, approval.evidence_snapshot_hash
            )
        except SnapshotError as exc:
            raise ApprovalError(
                ApprovalErrorCode.APPROVAL_SNAPSHOT_TAMPERED, str(exc)
            ) from exc

    async def _rehash(self, approval: ApprovalRequest) -> None:
        """Recompute the snapshot after a permitted edit (text or amount reduction)."""
        snapshot = ApprovalSnapshot.model_validate(approval.evidence_snapshot_json)
        effective_amount = (
            approval.approved_amount_pence
            if approval.approved_amount_pence is not None
            else approval.requested_amount_pence
        )
        updated = snapshot.model_copy(
            update={
                "draft_response_hash": hash_text(approval.draft_response_body),
                "requested_amount_pence": effective_amount,
                # A text-only edit keeps the key; an amount change produces a new one.
                "idempotency_key": build_business_idempotency_key(
                    action_type=approval.action_type,
                    order_id=approval.order_id,
                    amount_pence=effective_amount,
                ),
            }
        )
        approval.idempotency_key = updated.idempotency_key
        approval.evidence_snapshot_json = updated.model_dump(mode="json")
        approval.evidence_snapshot_hash = compute_snapshot_hash(updated)
        await self._session.flush()

    async def _load_v2_run_awaiting_approval(self, run_id: uuid.UUID) -> WorkflowRun:
        run = await self._workflows.get(run_id)
        if run is None:
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT, "workflow run not found"
            )
        if run.workflow_version != WORKFLOW_V2_VERSION:
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT,
                f"approvals require workflow version {WORKFLOW_V2_VERSION}",
            )
        if run.current_state != WorkflowState.AWAITING_APPROVAL:
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_NOT_AWAITING_APPROVAL,
                f"workflow is {run.current_state.value}",
            )
        return run

    async def _set_proposal_status(
        self, approval: ApprovalRequest, status: ProposedActionStatus
    ) -> None:
        """Move the proposal to a new status, rejecting an illegal transition."""
        proposal = await self._session.get(ProposedAction, approval.proposed_action_id)
        if proposal is None:
            return
        if proposal.status != status and not is_valid_proposed_action_transition(
            proposal.status, status
        ):
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT,
                f"illegal proposal transition {proposal.status.value}->{status.value}",
            )
        proposal.status = status
        await self._session.flush()

    async def _apply_workflow_transition(
        self,
        run: WorkflowRun,
        *,
        destination: WorkflowState,
        step_name: str,
        approval: ApprovalRequest,
        actor: AuthenticatedUser | None,
        reason: str | None = None,
    ) -> None:
        """Move a v2 run through a human-decision transition with step + checkpoint."""
        definition = get_definition(run.workflow_version)
        if not definition.is_valid_transition(run.current_state, destination):
            raise ApprovalError(
                ApprovalErrorCode.WORKFLOW_STATE_CONFLICT,
                f"illegal transition {run.current_state.value}->{destination.value}",
            )
        now = self._now()
        index = run.step_index
        checkpoint = await self._workflows.get_latest_checkpoint(run.id)
        state = (
            restore_state(checkpoint.snapshot_json) if checkpoint is not None else None
        )
        step = await self._workflows.start_step(
            run_id=run.id,
            step_index=index,
            step_name=step_name,
            source_state=run.current_state,
            attempt=1,
            input_hash=approval.evidence_snapshot_hash,
            input_summary_json={
                "approval_request_id": str(approval.id),
                "actor_role": actor.role.value if actor else SYSTEM_ACTOR_ROLE,
            },
            now=now,
        )
        new_index = index + 1
        metadata: dict[str, object] = {
            "approval_request_id": str(approval.id),
            "approval_status": approval.status.value,
            "actor_user_id": str(actor.user_id) if actor else None,
            "actor_role": actor.role.value if actor else SYSTEM_ACTOR_ROLE,
        }
        if reason is not None:
            metadata["reason"] = reason
        await self._workflows.complete_step(
            step,
            destination_state=destination,
            output_hash=approval.evidence_snapshot_hash,
            output_summary_json=metadata,
            latency_ms=0,
            now=now,
            model_call_ids=[],
            tool_call_ids=[],
            citation_ids=list(approval.policy_citation_ids),
        )
        if state is not None:
            updated = state.model_copy(
                update={
                    "current_state": destination,
                    "step_index": new_index,
                    "current_step": step_name,
                    "approval_required": True,
                }
            )
            # The snapshot stays a valid, hash-matching workflow state; the decision
            # metadata lives on the workflow step's summary (never in the checkpoint).
            snapshot_json, digest = build_snapshot(updated)
            new_checkpoint = await self._workflows.append_checkpoint(
                run_id=run.id,
                step_index=new_index,
                state=destination,
                state_schema_version=STATE_SCHEMA_VERSION,
                snapshot_json=snapshot_json,
                snapshot_hash=digest,
                now=now,
            )
            await self._workflows.set_last_checkpoint(run, new_checkpoint.id)
        if is_terminal(destination):
            await self._workflows.mark_terminal(run, state=destination, now=now)
        else:
            await self._workflows.update_state(
                run, state=destination, step_index=new_index, current_step=step_name
            )

    async def _create_outbox_job(
        self,
        *,
        approval: ApprovalRequest,
        decision_id: uuid.UUID,
        run: WorkflowRun,
        execution_action: ExecutionActionType,
        now: datetime,
    ) -> OutboxJob:
        """Build and persist exactly one durable outbox job for an approved action."""
        snapshot = ApprovalSnapshot.model_validate(approval.evidence_snapshot_json)
        job_id = uuid.uuid4()
        payload = OutboxJobData(
            outbox_job_id=job_id,
            approval_request_id=approval.id,
            approval_decision_id=decision_id,
            proposed_action_id=approval.proposed_action_id,
            workflow_run_id=run.id,
            workflow_name=run.workflow_name,
            workflow_version=run.workflow_version,
            ticket_id=approval.ticket_id,
            customer_id=snapshot.customer_id,
            order_id=approval.order_id,
            action_type=execution_action,
            approved_amount_pence=approval.approved_amount_pence,
            business_idempotency_key=approval.idempotency_key,
            approval_snapshot_hash=approval.evidence_snapshot_hash,
            rule_result_hash=approval.rule_result_hash,
            evidence_snapshot_hash=approval.evidence_snapshot_hash,
            policy_version_ids=list(approval.policy_version_ids),
            policy_content_hashes=list(snapshot.policy_content_hashes),
            created_at=now,
        )
        job = OutboxJob(
            id=job_id,
            approval_request_id=approval.id,
            workflow_run_id=run.id,
            proposed_action_id=approval.proposed_action_id,
            action_type=execution_action.value,
            payload_version=payload.payload_version,
            payload_json=payload.model_dump(mode="json"),
            payload_hash=payload.compute_hash(),
            idempotency_key=approval.idempotency_key,
            status=OutboxStatus.PENDING,
            priority=self._priority_for(approval.risk_level),
            attempt_count=0,
            maximum_attempts=self._settings.outbox_max_attempts,
            next_attempt_at=now,
        )
        return await OutboxRepository(self._session).create(job)

    @staticmethod
    def _priority_for(risk_level: str) -> int:
        # Higher-risk actions surface first for the worker.
        return {"high": 300, "medium": 200}.get(risk_level, 100)

    async def _order_id_for(self, proposal: ProposedAction) -> uuid.UUID | None:
        run = await self._workflows.get(proposal.workflow_run_id)
        if run is None:
            return None
        checkpoint = await self._workflows.get_latest_checkpoint(run.id)
        if checkpoint is None:
            return None
        raw = checkpoint.snapshot_json.get("resolved_order_id")
        return uuid.UUID(str(raw)) if raw else None

    async def _resolve_amounts(
        self, proposal: ProposedAction, request: CreateApprovalRequest
    ) -> tuple[int | None, int | None]:
        """Derive the requested amount and the deterministic maximum for the action.

        Limits come from the S2 refund rule against live order data, so an approval can
        never be created above what the deterministic layer permits.
        """
        order_id = await self._order_id_for(proposal)
        if order_id is None or "refund" not in proposal.action_type:
            return request.requested_amount_pence or proposal.amount_pence, None
        order = await OrderRepository(self._session).get_with_items(order_id)
        if order is None:
            return request.requested_amount_pence or proposal.amount_pence, None
        item_total = max((item.line_total_pence for item in order.items), default=0)
        # Prior refunds come from the ledger in the next increment; none exist yet.
        maximum = min(item_total, order.total_paid_pence)
        requested = request.requested_amount_pence or proposal.amount_pence or maximum
        return requested, maximum

    async def _build_snapshot(
        self,
        *,
        proposal: ProposedAction,
        run: WorkflowRun,
        requester_id: uuid.UUID,
        requested: int | None,
        maximum: int | None,
        idempotency_key: str,
        now: datetime,
    ) -> ApprovalSnapshot:
        rule_result = proposal.rule_result_json
        routing = rule_result.get("routing") if isinstance(rule_result, dict) else {}
        routing = routing if isinstance(routing, dict) else {}
        ticket = await self._session.get(Ticket, proposal.ticket_id)
        return ApprovalSnapshot(
            proposed_action_id=proposal.id,
            workflow_run_id=run.id,
            workflow_name=run.workflow_name,
            workflow_version=run.workflow_version,
            ticket_id=proposal.ticket_id,
            customer_id=ticket.customer_id if ticket else None,
            order_id=await self._order_id_for(proposal),
            action_type=proposal.action_type,
            requested_amount_pence=requested,
            maximum_allowed_amount_pence=maximum,
            risk_level=proposal.risk_level,
            required_role=proposal.required_role,
            approval_required=proposal.approval_required,
            idempotency_key=idempotency_key,
            eligibility_outcome=str(routing.get("outcome", "unknown")),
            reason_codes=[str(c) for c in routing.get("reason_codes", [])],
            rule_versions={"routing": str(routing.get("rule_version", "unknown"))},
            policy_citation_ids=list(proposal.citation_ids),
            policy_version_ids=[],
            policy_content_hashes=[],
            draft_response_hash=hash_text(proposal.draft_response_body),
            proposed_action_created_at=proposal.created_at,
            requester_user_id=requester_id,
            snapshot_created_at=now,
        )

    @staticmethod
    def _result(
        approval: ApprovalRequest,
        *,
        created: bool,
        state: WorkflowState = WorkflowState.AWAITING_APPROVAL,
        outbox_job_created: bool = False,
        outbox_job_id: uuid.UUID | None = None,
        manual_action_required: bool = False,
    ) -> ApprovalResult:
        return ApprovalResult(
            approval_id=approval.id,
            status=approval.status,
            workflow_run_id=approval.workflow_run_id,
            workflow_state=state,
            snapshot_hash=approval.evidence_snapshot_hash,
            requested_amount_pence=approval.requested_amount_pence,
            maximum_allowed_amount_pence=approval.maximum_allowed_amount_pence,
            approved_amount_pence=approval.approved_amount_pence,
            created=created,
            outbox_job_created=outbox_job_created,
            outbox_job_id=outbox_job_id,
            manual_action_required=manual_action_required,
        )


# Roles that may ever decide. Kept explicit so a future role cannot silently gain it.
DECIDING_ROLES: frozenset[UserRole] = frozenset({UserRole.supervisor})
