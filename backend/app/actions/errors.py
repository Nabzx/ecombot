"""Execution error taxonomy and failure classification (S6).

Failures are classified so the worker knows what to do next:

* ``RETRYABLE_TECHNICAL`` — a transient fault (DB blip, lost lease). Retry, then
  dead-letter once attempts are exhausted.
* ``PRECONDITION_CHANGED`` — the world changed safely after approval (e.g. the order
  shipped before a cancellation executed). Never retried; routed to manual handling.
* ``NON_RETRYABLE_BUSINESS`` — a business/security violation that must never execute
  (expired approval, tampering, over-limit, cross-customer). Never retried; the job
  fails and the workflow pauses for a human.

A duplicate successful effect is **not** an error — it is idempotent success.
"""

from __future__ import annotations

from enum import StrEnum


class ExecutionErrorKind(StrEnum):
    RETRYABLE_TECHNICAL = "retryable_technical"
    PRECONDITION_CHANGED = "precondition_changed"
    NON_RETRYABLE_BUSINESS = "non_retryable_business"


class ExecutionErrorCode(StrEnum):
    # retryable technical
    TRANSIENT_DEPENDENCY = "transient_dependency"
    LEASE_LOST = "lease_lost"
    INJECTED_FAILURE = "injected_failure"
    # precondition changed (safe)
    ORDER_SHIPPED_BEFORE_EXECUTION = "order_shipped_before_execution"
    ORDER_DELIVERED_BEFORE_EXECUTION = "order_delivered_before_execution"
    ORDER_ALREADY_CANCELLED = "order_already_cancelled"
    APPROVED_BUT_PRECONDITIONS_CHANGED = "approved_but_preconditions_changed"
    # non-retryable business / security
    APPROVAL_NOT_EXECUTABLE = "approval_not_executable"
    APPROVAL_EXPIRED = "approval_expired"
    SNAPSHOT_TAMPERED = "snapshot_tampered"
    PAYLOAD_TAMPERED = "payload_tampered"
    OWNERSHIP_MISMATCH = "ownership_mismatch"
    AMOUNT_OVER_LIMIT = "amount_over_limit"
    AMOUNT_OVER_APPROVED = "amount_over_approved"
    AMOUNT_OVER_BALANCE = "amount_over_balance"
    REFUND_OVER_MAX = "refund_over_max"
    UNSUPPORTED_ACTION = "unsupported_action"
    WORKFLOW_NOT_EXECUTABLE = "workflow_not_executable"
    WORKFLOW_CANCELLED = "workflow_cancelled"
    PROPOSAL_NOT_EXECUTABLE = "proposal_not_executable"
    INVALID_POLICY_VERSION = "invalid_policy_version"
    ORDER_NOT_FOUND = "order_not_found"


class ExecutionError(Exception):
    """A classified execution failure with a stable, PII-safe code."""

    def __init__(
        self,
        kind: ExecutionErrorKind,
        code: ExecutionErrorCode,
        message: str,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.code = code
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind is ExecutionErrorKind.RETRYABLE_TECHNICAL


def technical(code: ExecutionErrorCode, message: str) -> ExecutionError:
    return ExecutionError(ExecutionErrorKind.RETRYABLE_TECHNICAL, code, message)


def precondition_changed(code: ExecutionErrorCode, message: str) -> ExecutionError:
    return ExecutionError(ExecutionErrorKind.PRECONDITION_CHANGED, code, message)


def business(code: ExecutionErrorCode, message: str) -> ExecutionError:
    return ExecutionError(ExecutionErrorKind.NON_RETRYABLE_BUSINESS, code, message)
