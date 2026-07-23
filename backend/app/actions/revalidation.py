"""Final deterministic pre-execution revalidation (S6).

Approval authorises an execution *attempt*; it never bypasses this gate. Before any
effect, the worker re-checks (against freshly-locked rows) that the approval, proposal,
workflow, ownership, evidence and payload all still permit exactly this action. Any
violation raises a classified :class:`ExecutionError` and no effect is applied.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.enums import PROPOSED_TO_EXECUTION
from app.actions.errors import (
    ExecutionErrorCode,
    business,
)
from app.approvals.enums import ApprovalDecisionType, ApprovalStatus
from app.approvals.repository import ApprovalDecisionRepository
from app.approvals.snapshot import SnapshotError, verify_snapshot
from app.models.approval import ApprovalRequest
from app.models.order import Order
from app.models.workflow import ProposedAction, WorkflowRun
from app.outbox.payload import OutboxJobData
from app.workflows.enums import ProposedActionStatus, WorkflowState

_EXECUTABLE_WORKFLOW_STATES = frozenset(
    {WorkflowState.APPROVED_PENDING_EXECUTION, WorkflowState.EXECUTING_ACTION}
)


async def revalidate_before_execution(
    session: AsyncSession,
    *,
    payload: OutboxJobData,
    approval: ApprovalRequest,
    proposal: ProposedAction,
    run: WorkflowRun,
    order: Order | None,
    now: datetime,
) -> None:
    """Raise a classified ExecutionError unless every precondition still holds."""
    # 1-2. Approval must still be pending execution.
    if approval.status != ApprovalStatus.EXECUTION_PENDING:
        raise business(
            ExecutionErrorCode.APPROVAL_NOT_EXECUTABLE,
            f"approval status is {approval.status.value}, not execution_pending",
        )

    # 3-4. A valid non-self Supervisor approve decision must exist.
    decisions = await ApprovalDecisionRepository(session).list_for_request(approval.id)
    approve = next(
        (d for d in decisions if d.decision == ApprovalDecisionType.APPROVE), None
    )
    if approve is None or approve.actor_role != "supervisor":
        raise business(
            ExecutionErrorCode.APPROVAL_NOT_EXECUTABLE,
            "no valid supervisor approval decision exists",
        )
    if approve.actor_user_id == approval.requester_user_id:
        raise business(
            ExecutionErrorCode.OWNERSHIP_MISMATCH,
            "approver may not be the requester",
        )

    # 5. Approval must not have expired.
    if approval.expires_at <= now:
        raise business(ExecutionErrorCode.APPROVAL_EXPIRED, "the approval has expired")

    # 6. Evidence snapshot must verify against its stored hash.
    try:
        verify_snapshot(
            approval.evidence_snapshot_json, approval.evidence_snapshot_hash
        )
    except SnapshotError as exc:
        raise business(ExecutionErrorCode.SNAPSHOT_TAMPERED, str(exc)) from exc

    # 7-8. Payload must reference the correct approval, proposal and workflow, and its
    # action-relevant hashes must match the approval it claims to authorise.
    if (
        payload.approval_request_id != approval.id
        or payload.proposed_action_id != approval.proposed_action_id
        or payload.workflow_run_id != approval.workflow_run_id
        or payload.approval_snapshot_hash != approval.evidence_snapshot_hash
        or payload.business_idempotency_key != approval.idempotency_key
    ):
        raise business(
            ExecutionErrorCode.PAYLOAD_TAMPERED,
            "payload does not match the approval it references",
        )

    # 9. Proposed action must still be approved-pending-execution.
    if proposal.status != ProposedActionStatus.APPROVED_PENDING_EXECUTION:
        raise business(
            ExecutionErrorCode.PROPOSAL_NOT_EXECUTABLE,
            f"proposed action status is {proposal.status.value}",
        )

    # 10-11. Workflow must be in an executable v2 state and not cancelled.
    if run.current_state == WorkflowState.CANCELLED:
        raise business(
            ExecutionErrorCode.WORKFLOW_CANCELLED, "the workflow was cancelled"
        )
    if run.current_state not in _EXECUTABLE_WORKFLOW_STATES:
        raise business(
            ExecutionErrorCode.WORKFLOW_NOT_EXECUTABLE,
            f"workflow state {run.current_state.value} is not executable",
        )

    # 12. Ownership: the order must belong to the customer named in the snapshot.
    if (
        order is not None
        and payload.customer_id is not None
        and order.customer_id != payload.customer_id
    ):
        raise business(
            ExecutionErrorCode.OWNERSHIP_MISMATCH,
            "order ownership no longer matches the approval",
        )

    # 13. The action must still be one that may be automatically executed.
    if PROPOSED_TO_EXECUTION.get(approval.action_type) != payload.action_type:
        raise business(
            ExecutionErrorCode.UNSUPPORTED_ACTION,
            "approved action does not map to the payload's execution action",
        )

    # 15. Policy versions must be unchanged since approval.
    if sorted(payload.policy_version_ids) != sorted(approval.policy_version_ids):
        raise business(
            ExecutionErrorCode.INVALID_POLICY_VERSION,
            "policy versions changed since approval",
        )
