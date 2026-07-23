"""Action execution context and the handler result plan (S6).

The context is deliberately model-free: the worker never calls an LLM to decide whether
an action may execute. Handlers read the locked order and the refund ledger, revalidate
deterministically, and return an :class:`ActionExecutionResult` *plan* — the processor
applies every write in one atomic transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime

from app.actions.repository import LedgerRefundHistory
from app.models.enums import OrderStatus
from app.rules.clock import Clock


@dataclass
class ActionExecutionContext:
    """Everything a handler needs to validate and plan one action — no model access."""

    worker_id: str
    clock: Clock
    refund_history: LedgerRefundHistory
    correlation_id: str
    attempt_number: int
    total_deadline_seconds: float = 30.0

    def now(self) -> datetime:
        value = self.clock.now()
        return value


@dataclass
class ActionExecutionResult:
    """A handler's computed plan: the effect to apply, described but not yet written."""

    business_effect_reference: str
    result_json: dict[str, object]
    summary: str
    amount_pence: int | None = None
    ledger_amount_pence: int | None = None
    order_item_id: uuid.UUID | None = None
    new_order_status: OrderStatus | None = None
    precondition_snapshot: dict[str, object] = field(default_factory=dict)
