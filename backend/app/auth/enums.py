"""Permissions, token types and the role → permission map (S6)."""

from __future__ import annotations

from enum import StrEnum

from app.models.enums import UserRole


class Permission(StrEnum):
    """Explicit permissions checked by the approval/action APIs and services."""

    TICKET_REVIEW = "ticket_review"
    PROPOSAL_EDIT = "proposal_edit"
    APPROVAL_REQUEST_CREATE = "approval_request_create"
    APPROVAL_QUEUE_READ = "approval_queue_read"
    APPROVAL_DECIDE = "approval_decide"
    APPROVAL_HIGH_VALUE = "approval_high_value"
    ACTION_STATUS_READ = "action_status_read"
    OUTBOX_INSPECT = "outbox_inspect"
    MANUAL_RETRY_REQUEST = "manual_retry_request"


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


# The synthetic system actor used by the outbox worker. It is never a human user, cannot
# authenticate and cannot approve — it only claims jobs and applies simulated effects.
SYSTEM_EXECUTOR_ID = "system-executor"
SYSTEM_EXECUTOR_ROLE = "system_executor"

_AGENT_PERMISSIONS: frozenset[Permission] = frozenset(
    {
        Permission.TICKET_REVIEW,
        Permission.PROPOSAL_EDIT,
        Permission.APPROVAL_REQUEST_CREATE,
        Permission.APPROVAL_QUEUE_READ,
        Permission.ACTION_STATUS_READ,
    }
)

# Supervisors do everything an agent can, plus decide, high-value approvals, inspect the
# outbox and authorise a retry after a safe technical failure.
_SUPERVISOR_PERMISSIONS: frozenset[Permission] = _AGENT_PERMISSIONS | frozenset(
    {
        Permission.APPROVAL_DECIDE,
        Permission.APPROVAL_HIGH_VALUE,
        Permission.OUTBOX_INSPECT,
        Permission.MANUAL_RETRY_REQUEST,
    }
)

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.support_agent: _AGENT_PERMISSIONS,
    UserRole.supervisor: _SUPERVISOR_PERMISSIONS,
}


def permissions_for(role: UserRole) -> frozenset[Permission]:
    return ROLE_PERMISSIONS.get(role, frozenset())
