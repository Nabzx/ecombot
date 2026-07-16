"""Small, explicit object builders for repository and model tests.

Kept separate from the large synthetic generator so repo tests stay fast and readable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.models.customer import Customer
from app.models.enums import (
    CustomerTier,
    MessageSender,
    OrderStatus,
    PolicyStatus,
    ProductCategory,
    ShipmentStatus,
    TicketCategory,
    TicketStatus,
)
from app.models.order import Order, OrderItem
from app.models.policy import Policy, PolicyVersion
from app.models.product import Product
from app.models.shipment import Shipment
from app.models.ticket import Ticket, TicketMessage
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)
TODAY = date(2026, 7, 16)


@dataclass(slots=True)
class Sample:
    customer_a: Customer
    customer_b: Customer
    order_a1: Order
    order_a2: Order
    order_b1: Order
    ticket_a1: Ticket
    returns_policy: Policy


def make_customer(
    email: str,
    first: str,
    last: str,
    ref: str,
    tier: CustomerTier = CustomerTier.standard,
) -> Customer:
    return Customer(
        external_reference=ref,
        first_name=first,
        last_name=last,
        email=email,
        phone="07123456789",
        tier=tier,
    )


def make_product(sku: str, price: int = 2000) -> Product:
    return Product(
        sku=sku,
        name=f"Product {sku}",
        description="A test product.",
        category=ProductCategory.kitchenware,
        unit_price_pence=price,
    )


def make_order(
    customer: Customer,
    product: Product,
    number: str,
    *,
    qty: int = 1,
    status: OrderStatus = OrderStatus.delivered,
    with_shipment: bool = True,
    tracking: str = "MER-TRK-00000001",
) -> Order:
    line_total = qty * product.unit_price_pence
    order = Order(
        order_number=number,
        customer=customer,
        status=status,
        subtotal_pence=line_total,
        delivery_fee_pence=0,
        discount_pence=0,
        total_paid_pence=line_total,
        placed_at=NOW - timedelta(days=10),
    )
    order.items = [
        OrderItem(
            product=product,
            quantity=qty,
            unit_price_pence=product.unit_price_pence,
            line_total_pence=line_total,
        )
    ]
    if with_shipment:
        order.shipment = Shipment(
            carrier="Meridian Express",
            tracking_number=tracking,
            status=ShipmentStatus.delivered,
            promised_delivery_date=TODAY - timedelta(days=6),
            shipped_at=NOW - timedelta(days=9),
            delivered_at=NOW - timedelta(days=7),
        )
    return order


async def build_sample(session: AsyncSession) -> Sample:
    """Create two customers, three orders, a ticket and a versioned policy."""
    customer_a = make_customer(
        "jane.doe@example.com", "Jane", "Doe", "MER-C-00001", CustomerTier.vip
    )
    customer_b = make_customer("john.smith@example.com", "John", "Smith", "MER-C-00002")
    product1 = make_product("MER-KIT-T01", 2500)
    product2 = make_product("MER-KIT-T02", 4000)

    order_a1 = make_order(customer_a, product1, "MER-2026-000001", tracking="MER-TRK-1")
    order_a2 = make_order(
        customer_a,
        product2,
        "MER-2026-000002",
        with_shipment=False,
        status=OrderStatus.processing,
    )
    order_b1 = make_order(customer_b, product1, "MER-2026-000003", tracking="MER-TRK-3")

    ticket_a1 = Ticket(
        ticket_reference="TKT-2026-000001",
        customer=customer_a,
        order=order_a1,
        category=TicketCategory.order_tracking,
        status=TicketStatus.received,
        subject="Where is my order?",
        received_at=NOW - timedelta(days=1),
    )
    ticket_a1.messages = [
        TicketMessage(
            sender=MessageSender.customer,
            body="Where is order MER-2026-000001?",
            is_trusted=False,
            sequence_number=1,
        ),
        TicketMessage(
            sender=MessageSender.agent,
            body="Looking into it now.",
            is_trusted=True,
            sequence_number=2,
        ),
    ]

    returns_policy = Policy(
        topic="returns", title="Returns Policy", description="How returns work."
    )
    returns_policy.versions = [
        PolicyVersion(
            version=1,
            status=PolicyStatus.superseded,
            body="Old 14-day policy.",
            effective_from=TODAY - timedelta(days=800),
            effective_to=TODAY - timedelta(days=400),
        ),
        PolicyVersion(
            version=2,
            status=PolicyStatus.active,
            body="Current 30-day policy.",
            effective_from=TODAY - timedelta(days=400),
            effective_to=None,
        ),
    ]

    session.add_all(
        [
            customer_a,
            customer_b,
            product1,
            product2,
            order_a1,
            order_a2,
            order_b1,
            ticket_a1,
            returns_policy,
        ]
    )
    await session.flush()
    return Sample(
        customer_a=customer_a,
        customer_b=customer_b,
        order_a1=order_a1,
        order_a2=order_a2,
        order_b1=order_b1,
        ticket_a1=ticket_a1,
        returns_policy=returns_policy,
    )
