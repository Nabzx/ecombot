"""Simulated action handlers: refund and order cancellation (S6).

A handler *validates and plans* one action against the locked order — it performs no
writes. The processor applies the returned :class:`ActionExecutionResult` at once. All
effects are simulated: no payment processor, carrier or store is ever contacted; every
result carries a clearly-synthetic ``SIM-…`` reference.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.actions.context import ActionExecutionContext, ActionExecutionResult
from app.actions.enums import ExecutionActionType
from app.actions.errors import (
    ExecutionErrorCode,
    business,
    precondition_changed,
)
from app.actions.references import next_reference
from app.models.enums import OrderStatus, ShipmentStatus
from app.models.order import Order
from app.models.shipment import Shipment
from app.outbox.payload import OutboxJobData
from app.rules.constants import REFUND_HIGH_MAX_PENCE

# The absolute ceiling on any single simulated refund (GBP 250.00).
REFUND_ABSOLUTE_MAX_PENCE = REFUND_HIGH_MAX_PENCE

_CANCELLABLE_ORDER_STATUSES = frozenset(
    {OrderStatus.placed, OrderStatus.paid, OrderStatus.processing}
)


class ActionHandler(Protocol):
    action_type: ExecutionActionType
    version: str

    async def execute(
        self,
        session: AsyncSession,
        ctx: ActionExecutionContext,
        payload: OutboxJobData,
        order: Order,
        shipment: Shipment | None,
    ) -> ActionExecutionResult: ...


class SimulatedRefundHandler:
    action_type = ExecutionActionType.SIMULATED_REFUND
    version = "refund-handler-v1"

    async def execute(
        self,
        session: AsyncSession,
        ctx: ActionExecutionContext,
        payload: OutboxJobData,
        order: Order,
        shipment: Shipment | None,
    ) -> ActionExecutionResult:
        amount = payload.approved_amount_pence
        if amount is None or amount <= 0:
            raise business(
                ExecutionErrorCode.AMOUNT_OVER_LIMIT,
                "a refund requires a positive approved amount",
            )
        if amount > REFUND_ABSOLUTE_MAX_PENCE:
            raise business(
                ExecutionErrorCode.REFUND_OVER_MAX,
                f"refund {amount}p exceeds the {REFUND_ABSOLUTE_MAX_PENCE}p ceiling",
            )
        # The refundable item total (largest line) and the remaining order balance,
        # recomputed now against the ledger — prior refunds reduce what remains.
        item_total = max((item.line_total_pence for item in order.items), default=0)
        prior = await ctx.refund_history.refunded_total_pence(order.id)
        remaining = max(0, order.total_paid_pence - prior)
        if amount > item_total:
            raise business(
                ExecutionErrorCode.AMOUNT_OVER_LIMIT,
                f"refund {amount}p exceeds the item total {item_total}p",
            )
        if amount > remaining:
            raise business(
                ExecutionErrorCode.AMOUNT_OVER_BALANCE,
                f"refund {amount}p exceeds the remaining balance {remaining}p",
            )

        cumulative = prior + amount
        new_status = (
            OrderStatus.refunded
            if cumulative >= order.total_paid_pence
            else OrderStatus.partially_refunded
        )
        reference = await next_reference(
            session, action_type=self.action_type, year=ctx.now().year
        )
        pounds = f"£{amount / 100:,.2f}"
        return ActionExecutionResult(
            business_effect_reference=reference,
            amount_pence=amount,
            ledger_amount_pence=amount,
            new_order_status=new_status,
            summary=(
                f"A simulated refund of {pounds} was recorded in the AgentOps "
                "demonstration ledger."
            ),
            result_json={
                "simulated": True,
                "action": "simulated_refund",
                "reference": reference,
                "amount_pence": amount,
                "currency": "GBP",
                "order_id": str(order.id),
                "cumulative_refunded_pence": cumulative,
                "order_status": new_status.value,
            },
            precondition_snapshot={
                "order_status": order.status.value,
                "prior_refunded_pence": prior,
                "item_total_pence": item_total,
                "total_paid_pence": order.total_paid_pence,
            },
        )


class SimulatedCancellationHandler:
    action_type = ExecutionActionType.SIMULATED_ORDER_CANCELLATION
    version = "cancellation-handler-v1"

    async def execute(
        self,
        session: AsyncSession,
        ctx: ActionExecutionContext,
        payload: OutboxJobData,
        order: Order,
        shipment: Shipment | None,
    ) -> ActionExecutionResult:
        # Preconditions may have changed safely between approval and execution.
        if order.status == OrderStatus.cancelled:
            raise precondition_changed(
                ExecutionErrorCode.ORDER_ALREADY_CANCELLED,
                "the order is already cancelled",
            )
        if order.status == OrderStatus.delivered:
            raise precondition_changed(
                ExecutionErrorCode.ORDER_DELIVERED_BEFORE_EXECUTION,
                "the order was delivered before execution",
            )
        if order.status == OrderStatus.shipped or (
            shipment is not None and shipment.status != ShipmentStatus.label_created
        ):
            raise precondition_changed(
                ExecutionErrorCode.ORDER_SHIPPED_BEFORE_EXECUTION,
                "the order shipped before execution; a return flow is required",
            )
        if order.status not in _CANCELLABLE_ORDER_STATUSES:
            raise precondition_changed(
                ExecutionErrorCode.APPROVED_BUT_PRECONDITIONS_CHANGED,
                f"order status {order.status.value} is no longer cancellable",
            )

        reference = await next_reference(
            session, action_type=self.action_type, year=ctx.now().year
        )
        return ActionExecutionResult(
            business_effect_reference=reference,
            new_order_status=OrderStatus.cancelled,
            summary=(
                "The order was cancelled in the AgentOps demonstration environment."
            ),
            result_json={
                "simulated": True,
                "action": "simulated_order_cancellation",
                "reference": reference,
                "order_id": str(order.id),
                "previous_status": order.status.value,
                "order_status": OrderStatus.cancelled.value,
            },
            precondition_snapshot={
                "order_status": order.status.value,
                "shipment_status": (
                    shipment.status.value if shipment is not None else None
                ),
            },
        )
