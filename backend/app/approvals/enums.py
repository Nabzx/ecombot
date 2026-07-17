"""Approval status and decision enums with validated transitions (S6)."""

from __future__ import annotations

from enum import StrEnum


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    EXECUTION_PENDING = "execution_pending"
    EXECUTED = "executed"
    EXECUTION_FAILED = "execution_failed"


# Legal status transitions. Anything not listed is rejected (e.g. rejected→approved,
# expired→approved, executed→pending, cancelled→approved are all impossible).
APPROVAL_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset(
        {
            ApprovalStatus.APPROVED,
            ApprovalStatus.REJECTED,
            ApprovalStatus.EXPIRED,
            ApprovalStatus.CANCELLED,
            ApprovalStatus.SUPERSEDED,
        }
    ),
    ApprovalStatus.APPROVED: frozenset({ApprovalStatus.EXECUTION_PENDING}),
    ApprovalStatus.EXECUTION_PENDING: frozenset(
        {ApprovalStatus.EXECUTED, ApprovalStatus.EXECUTION_FAILED}
    ),
    ApprovalStatus.EXECUTION_FAILED: frozenset({ApprovalStatus.EXECUTION_PENDING}),
}

# Terminal approval statuses (no further transitions in S6).
TERMINAL_APPROVAL_STATUSES: frozenset[ApprovalStatus] = frozenset(
    {
        ApprovalStatus.REJECTED,
        ApprovalStatus.EXPIRED,
        ApprovalStatus.CANCELLED,
        ApprovalStatus.SUPERSEDED,
        ApprovalStatus.EXECUTED,
    }
)


def is_valid_approval_transition(
    source: ApprovalStatus, destination: ApprovalStatus
) -> bool:
    return destination in APPROVAL_TRANSITIONS.get(source, frozenset())


class ApprovalDecisionType(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL = "cancel"
    EXPIRE = "expire"
    RETRY_AUTHORISED = "retry_authorised"
