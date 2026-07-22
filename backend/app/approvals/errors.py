"""Stable approval error taxonomy (S6).

Services and APIs raise :class:`ApprovalError` with a stable code; raw SQLAlchemy
exceptions never reach callers. Each code maps to an HTTP status.
"""

from __future__ import annotations

from enum import StrEnum


class ApprovalErrorCode(StrEnum):
    APPROVAL_NOT_FOUND = "approval_not_found"
    PROPOSAL_NOT_FOUND = "proposal_not_found"
    WORKFLOW_NOT_AWAITING_APPROVAL = "workflow_not_awaiting_approval"
    PROPOSAL_NOT_AWAITING_APPROVAL = "proposal_not_awaiting_approval"
    BLOCKED_PROPOSAL = "blocked_proposal"
    APPROVAL_ALREADY_EXISTS = "approval_already_exists"
    APPROVAL_NOT_PENDING = "approval_not_pending"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_SUPERSEDED = "approval_superseded"
    APPROVAL_SNAPSHOT_INVALID = "approval_snapshot_invalid"
    APPROVAL_SNAPSHOT_TAMPERED = "approval_snapshot_tampered"
    APPROVAL_AMOUNT_INVALID = "approval_amount_invalid"
    APPROVAL_AMOUNT_ABOVE_REQUESTED = "approval_amount_above_requested"
    APPROVAL_AMOUNT_ABOVE_MAXIMUM = "approval_amount_above_maximum"
    APPROVAL_SELF_DECISION_FORBIDDEN = "approval_self_decision_forbidden"
    APPROVAL_ROLE_FORBIDDEN = "approval_role_forbidden"
    APPROVAL_CONCURRENT_DECISION = "approval_concurrent_decision"
    APPROVAL_IDEMPOTENCY_CONFLICT = "approval_idempotency_conflict"
    WORKFLOW_STATE_CONFLICT = "workflow_state_conflict"
    OWNERSHIP_REVALIDATION_FAILED = "ownership_revalidation_failed"
    POLICY_EVIDENCE_INVALID = "policy_evidence_invalid"
    EDIT_NOT_ALLOWED = "edit_not_allowed"
    INTERNAL_ERROR = "internal_error"


class ApprovalError(Exception):
    """A typed approval failure with a stable code and safe message."""

    def __init__(self, code: ApprovalErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# Business conflicts → 409; permission/role/self-decision → 403; not-found → 404;
# invalid input/amount → 422; tamper/security → 409; technical → 500.
ERROR_HTTP_STATUS: dict[ApprovalErrorCode, int] = {
    ApprovalErrorCode.APPROVAL_NOT_FOUND: 404,
    ApprovalErrorCode.PROPOSAL_NOT_FOUND: 404,
    ApprovalErrorCode.WORKFLOW_NOT_AWAITING_APPROVAL: 409,
    ApprovalErrorCode.PROPOSAL_NOT_AWAITING_APPROVAL: 409,
    ApprovalErrorCode.BLOCKED_PROPOSAL: 409,
    ApprovalErrorCode.APPROVAL_ALREADY_EXISTS: 409,
    ApprovalErrorCode.APPROVAL_NOT_PENDING: 409,
    ApprovalErrorCode.APPROVAL_EXPIRED: 409,
    ApprovalErrorCode.APPROVAL_CANCELLED: 409,
    ApprovalErrorCode.APPROVAL_REJECTED: 409,
    ApprovalErrorCode.APPROVAL_SUPERSEDED: 409,
    ApprovalErrorCode.APPROVAL_SNAPSHOT_INVALID: 409,
    ApprovalErrorCode.APPROVAL_SNAPSHOT_TAMPERED: 409,
    ApprovalErrorCode.APPROVAL_AMOUNT_INVALID: 422,
    ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_REQUESTED: 422,
    ApprovalErrorCode.APPROVAL_AMOUNT_ABOVE_MAXIMUM: 422,
    ApprovalErrorCode.APPROVAL_SELF_DECISION_FORBIDDEN: 403,
    ApprovalErrorCode.APPROVAL_ROLE_FORBIDDEN: 403,
    ApprovalErrorCode.APPROVAL_CONCURRENT_DECISION: 409,
    ApprovalErrorCode.APPROVAL_IDEMPOTENCY_CONFLICT: 409,
    ApprovalErrorCode.WORKFLOW_STATE_CONFLICT: 409,
    ApprovalErrorCode.OWNERSHIP_REVALIDATION_FAILED: 409,
    ApprovalErrorCode.POLICY_EVIDENCE_INVALID: 409,
    ApprovalErrorCode.EDIT_NOT_ALLOWED: 409,
    ApprovalErrorCode.INTERNAL_ERROR: 500,
}


def http_status_for(code: ApprovalErrorCode) -> int:
    return ERROR_HTTP_STATUS.get(code, 500)
