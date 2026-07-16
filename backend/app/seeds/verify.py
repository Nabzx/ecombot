"""Programmatic data-integrity verification.

Returns a list of human-readable issues; an empty list means the dataset is sound. The
CLI turns a non-empty list into a non-zero exit code.
"""

from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.customer import Customer
from app.models.enums import MessageSender, PolicyStatus, ShipmentStatus
from app.models.order import Order
from app.models.policy import Policy
from app.models.ticket import Ticket

MAX_MONEY_PENCE = 1_000_000  # £10,000 sanity ceiling for any single money field
_DIGIT_RUN = re.compile(r"\d{13,19}")  # card-number-like runs

# Order statuses whose shipment (if present) must be delivered.
_DELIVERED_ORDER_STATUSES = {"delivered", "partially_refunded", "refunded"}


async def verify_data(session: AsyncSession) -> list[str]:
    issues: list[str] = []

    customers = list((await session.scalars(select(Customer))).all())
    orders = list(
        (
            await session.scalars(
                select(Order).options(
                    selectinload(Order.items), selectinload(Order.shipment)
                )
            )
        ).all()
    )
    tickets = list(
        (
            await session.scalars(select(Ticket).options(selectinload(Ticket.messages)))
        ).all()
    )
    policies = list(
        (
            await session.scalars(select(Policy).options(selectinload(Policy.versions)))
        ).all()
    )

    customer_ids = {c.id for c in customers}
    order_owner = {o.id: o.customer_id for o in orders}

    _check_unique(issues, "customer email", [c.email for c in customers])
    _check_unique(
        issues, "customer external_reference", [c.external_reference for c in customers]
    )
    _check_unique(issues, "order_number", [o.order_number for o in orders])
    _check_unique(issues, "ticket_reference", [t.ticket_reference for t in tickets])

    for order in orders:
        _check_order(issues, order, customer_ids)

    for ticket in tickets:
        _check_ticket(issues, ticket, customer_ids, order_owner)

    for policy in policies:
        _check_policy(issues, policy)

    _check_no_secrets(issues, customers, tickets)
    return issues


def _check_unique(issues: list[str], label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        issues.append(f"Duplicate {label} values found")


def _check_order(issues: list[str], order: Order, customer_ids: set[uuid.UUID]) -> None:
    ref = order.order_number
    if order.customer_id not in customer_ids:
        issues.append(f"Order {ref} references a missing customer")
    if not order.items:
        issues.append(f"Order {ref} has no items")

    subtotal = 0
    for item in order.items:
        expected = item.quantity * item.unit_price_pence
        if item.line_total_pence != expected:
            issues.append(f"Order {ref} item line total mismatch")
        subtotal += item.line_total_pence
        _check_money(issues, ref, item.line_total_pence)

    if subtotal != order.subtotal_pence:
        issues.append(f"Order {ref} subtotal does not equal sum of line totals")
    if order.total_paid_pence != (
        order.subtotal_pence + order.delivery_fee_pence - order.discount_pence
    ):
        issues.append(f"Order {ref} total_paid is inconsistent")
    for value in (
        order.subtotal_pence,
        order.delivery_fee_pence,
        order.discount_pence,
        order.total_paid_pence,
    ):
        _check_money(issues, ref, value)

    _check_shipment(issues, order)


def _check_shipment(issues: list[str], order: Order) -> None:
    shipment = order.shipment
    if shipment is None:
        return
    ref = order.order_number
    delivered = shipment.status == ShipmentStatus.delivered
    if delivered and shipment.delivered_at is None:
        issues.append(f"Order {ref} delivered shipment has no delivered_at")
    if not delivered and shipment.delivered_at is not None:
        issues.append(f"Order {ref} non-delivered shipment has delivered_at")
    if (
        order.status.value in _DELIVERED_ORDER_STATUSES
        and shipment.status != ShipmentStatus.delivered
    ):
        issues.append(
            f"Order {ref} status {order.status.value} but shipment not delivered"
        )


def _check_ticket(
    issues: list[str],
    ticket: Ticket,
    customer_ids: set[uuid.UUID],
    order_owner: dict[uuid.UUID, uuid.UUID],
) -> None:
    ref = ticket.ticket_reference
    if ticket.customer_id is not None and ticket.customer_id not in customer_ids:
        issues.append(f"Ticket {ref} references a missing customer")
    if (
        ticket.customer_id is not None
        and ticket.order_id is not None
        and order_owner.get(ticket.order_id) != ticket.customer_id
    ):
        issues.append(f"Ticket {ref} links an order owned by another customer")

    # Message sequence must be continuous from 1, and customer messages untrusted.
    sequences = sorted(m.sequence_number for m in ticket.messages)
    if sequences != list(range(1, len(sequences) + 1)):
        issues.append(f"Ticket {ref} message sequence is not continuous")
    for message in ticket.messages:
        if message.sender == MessageSender.customer and message.is_trusted:
            issues.append(f"Ticket {ref} has a trusted customer message")


def _check_policy(issues: list[str], policy: Policy) -> None:
    is_fixture = "fixture" in policy.topic
    active = 0
    for version in policy.versions:
        if (
            version.effective_to is not None
            and version.effective_to <= version.effective_from
        ):
            issues.append(
                f"Policy {policy.topic} v{version.version} has an invalid range"
            )
        if version.status == PolicyStatus.active:
            active += 1
    if active > 1 and not is_fixture:
        issues.append(
            f"Policy {policy.topic} has multiple active versions (unexpected conflict)"
        )


def _check_money(issues: list[str], ref: str, value: int) -> None:
    if value < 0 or value > MAX_MONEY_PENCE:
        issues.append(f"Order {ref} has a money value out of range: {value}")


def _check_no_secrets(
    issues: list[str], customers: list[Customer], tickets: list[Ticket]
) -> None:
    for ticket in tickets:
        for message in ticket.messages:
            if _DIGIT_RUN.search(message.body):
                issues.append(
                    f"Ticket {ticket.ticket_reference} message contains a card-like digit run"
                )
    for customer in customers:
        if _DIGIT_RUN.search(customer.phone):
            issues.append(
                f"Customer {customer.external_reference} phone looks card-like"
            )
