"""Cancellation-eligibility rules.

An order is cancellable only while it has not yet been dispatched: status in
{placed, paid, processing} and the shipment absent or only label-created. Cancellation
is High risk and always requires Supervisor approval. An already-cancelled order yields
an idempotent ineligible result, not an error.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.models.enums import OrderStatus, ShipmentStatus
from app.rules.enums import (
    ActionType,
    DecisionOutcome,
    ReasonCode,
    RiskLevel,
    Route,
)
from app.rules.idempotency import generate_idempotency_key
from app.rules.models import RuleResult

RULE_VERSION = "cancellation-v1"

_CANCELLABLE_ORDER_STATUSES = {
    OrderStatus.placed,
    OrderStatus.paid,
    OrderStatus.processing,
}
_PRE_DISPATCH_SHIPMENT = {ShipmentStatus.label_created}


class CancellationInput(BaseModel):
    ownership_confirmed: bool
    ticket_id: uuid.UUID
    order_id: uuid.UUID
    order_status: OrderStatus
    shipment_status: ShipmentStatus | None = None
    shipment_present: bool = True


def check_cancellation_eligibility(inp: CancellationInput) -> RuleResult:
    if not inp.ownership_confirmed:
        return _blocked()

    if inp.order_status == OrderStatus.cancelled:
        return _ineligible(
            ReasonCode.ORDER_ALREADY_CANCELLED,
            "The order is already cancelled.",
        )
    if inp.order_status == OrderStatus.delivered:
        return _ineligible(
            ReasonCode.ORDER_ALREADY_DELIVERED,
            "The order has been delivered; use the return flow.",
            extra=[ReasonCode.RETURN_FLOW_RECOMMENDED],
        )
    if inp.order_status in {OrderStatus.refunded, OrderStatus.partially_refunded}:
        return _ineligible(
            ReasonCode.ORDER_STATUS_NOT_CANCELLABLE,
            "A refunded order cannot be cancelled.",
        )
    if inp.order_status not in _CANCELLABLE_ORDER_STATUSES:
        # e.g. shipped
        return _ineligible(
            ReasonCode.ORDER_ALREADY_SHIPPED,
            "The order has shipped; use the return flow.",
            extra=[ReasonCode.RETURN_FLOW_RECOMMENDED],
        )

    # Order status is cancellable; the shipment must not have left the warehouse.
    shipment_ok = (not inp.shipment_present) or (
        inp.shipment_status in _PRE_DISPATCH_SHIPMENT
    )
    if not shipment_ok:
        return _ineligible(
            ReasonCode.ORDER_ALREADY_SHIPPED,
            "The shipment has left the warehouse; use the return flow.",
            extra=[ReasonCode.RETURN_FLOW_RECOMMENDED],
        )

    reason_codes = [
        ReasonCode.CANCELLATION_ELIGIBLE,
        ReasonCode.CANCELLATION_SUPERVISOR_APPROVAL_REQUIRED,
        ReasonCode.DUPLICATE_CHECK_REQUIRED,
    ]
    explanations = [
        "The order has not shipped and can be cancelled.",
        "Cancellations require Supervisor approval.",
    ]
    if not inp.shipment_present:
        reason_codes.append(ReasonCode.SHIPMENT_ASSUMED_ABSENT)
        explanations.append("No shipment record exists; assumed not yet dispatched.")

    return RuleResult(
        outcome=DecisionOutcome.requires_approval,
        eligible=True,
        risk_level=RiskLevel.high,
        route=Route.await_supervisor,
        reason_codes=reason_codes,
        explanations=explanations,
        approval_required=True,
        execution_permitted=False,
        rule_version=RULE_VERSION,
        idempotency_key=generate_idempotency_key(
            ticket_id=inp.ticket_id,
            action_type=ActionType.cancellation,
            order_id=inp.order_id,
        ),
    )


def _ineligible(
    code: ReasonCode, explanation: str, *, extra: list[ReasonCode] | None = None
) -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.ineligible,
        eligible=False,
        risk_level=RiskLevel.read_only,
        route=Route.continue_processing,
        reason_codes=[code, *(extra or [])],
        explanations=[explanation],
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )


def _blocked() -> RuleResult:
    return RuleResult(
        outcome=DecisionOutcome.blocked,
        eligible=False,
        risk_level=RiskLevel.blocked,
        route=Route.blocked,
        reason_codes=[ReasonCode.ORDER_OWNERSHIP_MISMATCH],
        explanations=["Ownership is not confirmed; the cancellation is blocked."],
        execution_permitted=False,
        rule_version=RULE_VERSION,
    )
