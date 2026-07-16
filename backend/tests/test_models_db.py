"""Model tests against a real PostgreSQL database.

Covers relationships, enum/unique/check constraints, cascade and RESTRICT delete
behaviour, timestamps and money handling.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.models.enums import ShipmentStatus
from app.models.order import Order, OrderItem
from app.models.shipment import Shipment
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import (
    NOW,
    TODAY,
    build_sample,
    make_customer,
    make_order,
    make_product,
)


async def test_create_and_relationships(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    loaded = await db_session.get(Order, sample.order_a1.id)
    assert loaded is not None
    assert loaded.customer.email == "jane.doe@example.com"
    assert len(loaded.items) == 1
    assert loaded.shipment is not None
    assert loaded.created_at is not None
    assert loaded.updated_at is not None
    # Money is stored as integers (pennies).
    assert isinstance(loaded.total_paid_pence, int)


async def test_unique_email_constraint(db_session: AsyncSession) -> None:
    db_session.add(make_customer("dup@example.com", "A", "B", "MER-C-10001"))
    await db_session.flush()
    db_session.add(make_customer("dup@example.com", "C", "D", "MER-C-10002"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_line_total_check_constraint(db_session: AsyncSession) -> None:
    customer = make_customer("c1@example.com", "A", "B", "MER-C-20001")
    product = make_product("MER-KIT-C01", 1000)
    order = make_order(customer, product, "MER-2026-100001", with_shipment=False)
    order.items[0].line_total_pence = 999  # inconsistent with quantity * unit price
    db_session.add(order)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_total_paid_consistency_constraint(db_session: AsyncSession) -> None:
    customer = make_customer("c2@example.com", "A", "B", "MER-C-20002")
    product = make_product("MER-KIT-C02", 1000)
    order = make_order(customer, product, "MER-2026-100002", with_shipment=False)
    order.total_paid_pence = 12345  # not subtotal + delivery - discount
    db_session.add(order)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_confidence_range_constraint(db_session: AsyncSession) -> None:
    from app.models.enums import TicketCategory, TicketStatus
    from app.models.ticket import Ticket

    ticket = Ticket(
        ticket_reference="TKT-2026-900001",
        category=TicketCategory.unknown,
        status=TicketStatus.received,
        subject="x",
        classification_confidence=1.5,  # out of [0, 1]
        received_at=NOW,
    )
    db_session.add(ticket)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_delivered_shipment_requires_date(db_session: AsyncSession) -> None:
    customer = make_customer("c3@example.com", "A", "B", "MER-C-20003")
    product = make_product("MER-KIT-C03", 1000)
    order = make_order(customer, product, "MER-2026-100003", with_shipment=False)
    order.shipment = Shipment(
        carrier="X",
        tracking_number="MER-TRK-C03",
        status=ShipmentStatus.delivered,
        promised_delivery_date=TODAY,
        delivered_at=None,  # violates: delivered requires a date
    )
    db_session.add(order)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_undelivered_shipment_has_no_date(db_session: AsyncSession) -> None:
    customer = make_customer("c4@example.com", "A", "B", "MER-C-20004")
    product = make_product("MER-KIT-C04", 1000)
    order = make_order(customer, product, "MER-2026-100004", with_shipment=False)
    order.shipment = Shipment(
        carrier="X",
        tracking_number="MER-TRK-C04",
        status=ShipmentStatus.in_transit,
        promised_delivery_date=TODAY,
        delivered_at=NOW,  # violates: non-delivered must not have a date
    )
    db_session.add(order)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_empty_message_body_rejected(db_session: AsyncSession) -> None:
    from app.models.enums import MessageSender, TicketCategory, TicketStatus
    from app.models.ticket import Ticket, TicketMessage

    ticket = Ticket(
        ticket_reference="TKT-2026-900002",
        category=TicketCategory.unknown,
        status=TicketStatus.received,
        subject="x",
        received_at=NOW,
    )
    ticket.messages = [
        TicketMessage(
            sender=MessageSender.customer,
            body="   ",
            is_trusted=False,
            sequence_number=1,
        )
    ]
    db_session.add(ticket)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_cascade_delete_children(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    order_id = sample.order_a1.id
    await db_session.delete(sample.order_a1)
    await db_session.flush()
    items = await db_session.scalar(
        select(func.count())
        .select_from(OrderItem)
        .where(OrderItem.order_id == order_id)
    )
    shipments = await db_session.scalar(
        select(func.count()).select_from(Shipment).where(Shipment.order_id == order_id)
    )
    assert items == 0
    assert shipments == 0


async def test_customer_delete_restricted_with_orders(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    await db_session.delete(sample.customer_a)
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


async def test_invalid_enum_value_rejected(db_session: AsyncSession) -> None:
    customer = make_customer("c5@example.com", "A", "B", "MER-C-20005")
    product = make_product("MER-KIT-C05", 1000)
    order = make_order(customer, product, "MER-2026-100005", with_shipment=False)
    db_session.add(order)
    await db_session.flush()
    # Raw SQL bypasses SQLAlchemy's client-side enum validation so the invalid value
    # is rejected by the PostgreSQL enum type itself.
    with pytest.raises((IntegrityError, DBAPIError)):
        await db_session.execute(
            text("UPDATE orders SET status = 'not_a_real_status' WHERE id = :id"),
            {"id": order.id},
        )
        await db_session.flush()
    await db_session.rollback()


async def test_timestamps_are_timezone_aware(db_session: AsyncSession) -> None:
    customer = make_customer("c6@example.com", "A", "B", "MER-C-20006")
    db_session.add(customer)
    await db_session.flush()
    created = customer.created_at
    assert isinstance(created, datetime)
    assert created.tzinfo is not None
    assert abs((datetime.now(UTC) - created).total_seconds()) < 60 * 60
