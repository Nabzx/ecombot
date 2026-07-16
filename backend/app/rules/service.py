"""Orchestration that runs the deterministic layer against a seeded ticket.

Used by the demo CLI to show, for a fixture: the resolved customer/order, the ownership
result, the category-specific rule result, the routing decision and any idempotency key.
Order numbers are extracted from message text with a plain regex (not an LLM), which is
how the cross-customer fixture surfaces an order that belongs to someone else.
"""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TicketCategory
from app.models.order import Order
from app.models.ticket import Ticket
from app.repositories.order import OrderRepository
from app.repositories.ticket import TicketRepository
from app.rules.cancellations import CancellationInput, check_cancellation_eligibility
from app.rules.clock import Clock
from app.rules.deliveries import (
    DeliveryDelayInput,
    MissingDeliveryInput,
    check_missing_delivery,
    classify_delivery_delay,
)
from app.rules.enums import (
    ActionType,
    ItemCondition,
    ReasonCode,
    RefundBasis,
    ReturnReason,
    RiskLevel,
)
from app.rules.models import RuleResult
from app.rules.ownership import OwnershipInput, check_ownership, is_confirmed
from app.rules.refunds import RefundInput, check_refund_eligibility
from app.rules.remedies import (
    DamagedItemInput,
    IncorrectItemInput,
    check_damaged_item_remedy,
    check_incorrect_item_remedy,
)
from app.rules.returns import ReturnInput, check_return_eligibility
from app.rules.routing import RoutingInput, calculate_risk_and_route

ORDER_NUMBER_RE = re.compile(r"MER-\d{4}-\d{6}")

_SUPERVISOR_ACTIONS = {
    TicketCategory.refund_request: ActionType.refund,
    TicketCategory.return_request: ActionType.return_rma,
    TicketCategory.cancellation_request: ActionType.cancellation,
    TicketCategory.damaged_item: ActionType.replacement,
    TicketCategory.incorrect_item: ActionType.replacement,
}


class TicketInspection(BaseModel):
    ticket_reference: str
    seed_tag: str | None
    category: TicketCategory
    injection_flag: bool
    resolved_customer_id: uuid.UUID | None
    resolved_order_number: str | None
    order_belongs_to_customer: bool | None
    ownership: RuleResult
    category_result: RuleResult | None
    routing: RuleResult
    idempotency_key: str | None


async def _resolve_order(
    session: AsyncSession, ticket: Ticket
) -> tuple[Order | None, bool]:
    """Return (order, linked) where linked means the ticket itself references it."""
    repo = OrderRepository(session)
    if ticket.order_id is not None:
        order = await repo.get_with_items(ticket.order_id)
        return order, True
    # Not linked: try to find an order number mentioned in the messages.
    for message in ticket.messages:
        match = ORDER_NUMBER_RE.search(message.body)
        if match:
            return await repo.get_by_number(match.group()), False
    return None, False


def _category_result(
    ticket: Ticket, order: Order | None, linked: bool, ownership_ok: bool, clock: Clock
) -> RuleResult | None:
    if order is None or not linked:
        return None
    shipment = order.shipment
    delivered_at = shipment.delivered_at if shipment else None
    first_item = order.items[0] if order.items else None

    match ticket.category:
        case TicketCategory.order_tracking | TicketCategory.delayed_delivery:
            return classify_delivery_delay(
                DeliveryDelayInput(
                    order_status=order.status,
                    shipment_present=shipment is not None,
                    shipment_status=shipment.status if shipment else None,
                    promised_delivery_date=shipment.promised_delivery_date
                    if shipment
                    else None,
                ),
                clock,
            )
        case TicketCategory.missing_delivery:
            return check_missing_delivery(
                MissingDeliveryInput(
                    ownership_confirmed=ownership_ok,
                    shipment_present=shipment is not None,
                    shipment_status=shipment.status if shipment else None,
                    promised_delivery_date=shipment.promised_delivery_date
                    if shipment
                    else None,
                ),
                clock,
            )
        case TicketCategory.return_request:
            return check_return_eligibility(
                ReturnInput(
                    ownership_confirmed=ownership_ok,
                    order_status=order.status,
                    delivered_at=delivered_at,
                    reason=ReturnReason.changed_mind,
                    condition=ItemCondition.unused,
                    already_returned=first_item.is_returned if first_item else False,
                ),
                clock,
            )
        case TicketCategory.refund_request:
            if first_item is None:
                return None
            return check_refund_eligibility(
                RefundInput(
                    ownership_confirmed=ownership_ok,
                    ticket_id=ticket.id,
                    order_id=order.id,
                    requested_amount_pence=first_item.line_total_pence,
                    item_line_total_pence=first_item.line_total_pence,
                    order_total_paid_pence=order.total_paid_pence,
                    basis=RefundBasis.damaged_item,
                )
            )
        case TicketCategory.cancellation_request:
            return check_cancellation_eligibility(
                CancellationInput(
                    ownership_confirmed=ownership_ok,
                    ticket_id=ticket.id,
                    order_id=order.id,
                    order_status=order.status,
                    shipment_status=shipment.status if shipment else None,
                    shipment_present=shipment is not None,
                )
            )
        case TicketCategory.damaged_item:
            return check_damaged_item_remedy(
                DamagedItemInput(
                    ownership_confirmed=ownership_ok, delivered_at=delivered_at
                ),
                clock,
            )
        case TicketCategory.incorrect_item:
            return check_incorrect_item_remedy(
                IncorrectItemInput(
                    ownership_confirmed=ownership_ok,
                    delivered_at=delivered_at,
                    ordered_sku=first_item.product.sku if first_item else "",
                    claimed_received_sku=None,
                ),
                clock,
            )
        case _:
            return None


async def inspect_ticket(
    session: AsyncSession, ticket_reference: str, clock: Clock
) -> TicketInspection:
    base = await TicketRepository(session).get_by_reference(ticket_reference)
    if base is None:
        raise LookupError(f"Ticket {ticket_reference} not found")
    ticket = await TicketRepository(session).get_with_messages(base.id)
    assert ticket is not None  # noqa: S101 - just loaded by id

    order, linked = await _resolve_order(session, ticket)

    ownership = check_ownership(
        OwnershipInput(
            resolved_customer_id=ticket.customer_id,
            customer_match_count=1 if ticket.customer_id else 0,
            resolved_order_id=order.id if order else None,
            order_customer_id=order.customer_id if order else None,
            order_match_count=1 if order else 0,
        )
    )
    ownership_ok = is_confirmed(ownership)
    order_belongs = None if order is None else order.customer_id == ticket.customer_id

    category_result = _category_result(ticket, order, linked, ownership_ok, clock)

    action = _SUPERVISOR_ACTIONS.get(ticket.category, ActionType.information)
    action_risk = category_result.risk_level if category_result else RiskLevel.read_only
    routing = calculate_risk_and_route(
        RoutingInput(
            classification_confidence=ticket.classification_confidence,
            ticket_category=ticket.category,
            ownership_blocked=ownership.outcome.value == "blocked",
            injection_flag=ticket.injection_flag,
            proposed_action=action,
            action_risk=action_risk,
            delivered_but_disputed=category_result is not None
            and category_result.has(ReasonCode.DELIVERED_BUT_DISPUTED),
        )
    )

    return TicketInspection(
        ticket_reference=ticket.ticket_reference,
        seed_tag=ticket.seed_tag,
        category=ticket.category,
        injection_flag=ticket.injection_flag,
        resolved_customer_id=ticket.customer_id,
        resolved_order_number=order.order_number if order else None,
        order_belongs_to_customer=order_belongs,
        ownership=ownership,
        category_result=category_result,
        routing=routing,
        idempotency_key=category_result.idempotency_key if category_result else None,
    )
