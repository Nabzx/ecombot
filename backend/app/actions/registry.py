"""Explicit action-handler registry (S6).

Handlers are resolved only through this closed registry keyed by the internal
``ExecutionActionType`` — never by a handler name taken from a job payload. Each entry
carries the metadata the worker needs (timeout, max attempts, retryable technical codes)
and honest flags for whether the action moves money or changes order state.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.actions.enums import ExecutionActionType
from app.actions.errors import ExecutionErrorCode
from app.actions.handlers import (
    ActionHandler,
    SimulatedCancellationHandler,
    SimulatedRefundHandler,
)

# Technical error codes that are safe to retry (transient faults only).
_RETRYABLE_TECHNICAL: frozenset[ExecutionErrorCode] = frozenset(
    {
        ExecutionErrorCode.TRANSIENT_DEPENDENCY,
        ExecutionErrorCode.LEASE_LOST,
        ExecutionErrorCode.INJECTED_FAILURE,
    }
)


@dataclass(frozen=True)
class ActionHandlerSpec:
    action_type: ExecutionActionType
    handler: ActionHandler
    handler_version: str
    timeout_seconds: float
    maximum_attempts: int
    moves_money: bool
    changes_order_state: bool
    retryable_error_codes: frozenset[ExecutionErrorCode] = field(
        default_factory=lambda: _RETRYABLE_TECHNICAL
    )


_REGISTRY: dict[ExecutionActionType, ActionHandlerSpec] = {
    ExecutionActionType.SIMULATED_REFUND: ActionHandlerSpec(
        action_type=ExecutionActionType.SIMULATED_REFUND,
        handler=SimulatedRefundHandler(),
        handler_version=SimulatedRefundHandler.version,
        timeout_seconds=15.0,
        maximum_attempts=5,
        moves_money=True,
        changes_order_state=True,
    ),
    ExecutionActionType.SIMULATED_ORDER_CANCELLATION: ActionHandlerSpec(
        action_type=ExecutionActionType.SIMULATED_ORDER_CANCELLATION,
        handler=SimulatedCancellationHandler(),
        handler_version=SimulatedCancellationHandler.version,
        timeout_seconds=15.0,
        maximum_attempts=5,
        moves_money=False,
        changes_order_state=True,
    ),
}


def get_handler_spec(action_type: ExecutionActionType) -> ActionHandlerSpec | None:
    return _REGISTRY.get(action_type)


def registered_action_types() -> list[ExecutionActionType]:
    return list(_REGISTRY)
