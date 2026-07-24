"""Audit event types (S7).

A closed vocabulary of the security- and consequence-relevant events AgentOps records in
its immutable, hash-chained audit log. Business/PII detail never appears here — only the
event kind, its subject and safe metadata.
"""

from __future__ import annotations

from enum import StrEnum


class AuditEventType(StrEnum):
    # authentication
    AUTH_LOGIN_SUCCEEDED = "auth_login_succeeded"
    AUTH_LOGIN_FAILED = "auth_login_failed"
    AUTH_TOKEN_REFRESHED = "auth_token_refreshed"
    # approvals
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_EXPIRED = "approval_expired"
    APPROVAL_RETRY_AUTHORISED = "approval_retry_authorised"
    # outbox / execution
    OUTBOX_JOB_CREATED = "outbox_job_created"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    ACTION_DEAD_LETTERED = "action_dead_lettered"
    ACTION_MANUAL_REQUIRED = "action_manual_required"
    # workflow
    WORKFLOW_TERMINAL = "workflow_terminal"


# Decision type → audit event type, so the approval service maps decisions consistently.
DECISION_AUDIT_EVENT: dict[str, AuditEventType] = {
    "approve": AuditEventType.APPROVAL_APPROVED,
    "reject": AuditEventType.APPROVAL_REJECTED,
    "cancel": AuditEventType.APPROVAL_CANCELLED,
    "expire": AuditEventType.APPROVAL_EXPIRED,
    "retry_authorised": AuditEventType.APPROVAL_RETRY_AUTHORISED,
}
