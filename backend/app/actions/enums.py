"""Executed-action and refund-ledger enums (S6)."""

from __future__ import annotations

from enum import StrEnum


class ExecutionActionType(StrEnum):
    """The internal, executable action types (mapped from proposed actions)."""

    SIMULATED_REFUND = "simulated_refund"
    SIMULATED_ORDER_CANCELLATION = "simulated_order_cancellation"


class ExecutedActionStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    # Reserved only; S6 does not implement reversals.
    REVERSED = "reversed"


class RefundEntryType(StrEnum):
    REFUND = "refund"


class ExecutionOutcome(StrEnum):
    """Distinguishes a technical failure from a safe precondition change."""

    SUCCEEDED = "succeeded"
    APPROVED_BUT_PRECONDITIONS_CHANGED = "approved_but_preconditions_changed"
    TECHNICAL_FAILURE = "technical_failure"


# Proposed-action value → internal executable action (only these auto-execute).
PROPOSED_TO_EXECUTION: dict[str, ExecutionActionType] = {
    "request_supervisor_refund_approval": ExecutionActionType.SIMULATED_REFUND,
    "request_supervisor_cancellation_approval": (
        ExecutionActionType.SIMULATED_ORDER_CANCELLATION
    ),
}
