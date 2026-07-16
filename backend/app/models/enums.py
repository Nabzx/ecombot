"""Domain enumerations.

Python ``StrEnum`` members whose values are the exact strings stored in PostgreSQL
native enums. ``pg_enum`` builds a SQLAlchemy ``Enum`` that persists ``.value`` (not
the member name) and carries a stable type name for migrations.
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum as SAEnum


class UserRole(StrEnum):
    support_agent = "support_agent"
    supervisor = "supervisor"


class CustomerTier(StrEnum):
    standard = "standard"
    vip = "vip"


class ProductCategory(StrEnum):
    kitchenware = "kitchenware"
    home_decor = "home_decor"
    bedding_and_towels = "bedding_and_towels"
    small_furniture = "small_furniture"
    consumer_accessories = "consumer_accessories"


class OrderStatus(StrEnum):
    placed = "placed"
    paid = "paid"
    processing = "processing"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"
    partially_refunded = "partially_refunded"
    refunded = "refunded"


class ShipmentStatus(StrEnum):
    label_created = "label_created"
    in_transit = "in_transit"
    out_for_delivery = "out_for_delivery"
    delivered = "delivered"
    exception = "exception"
    lost = "lost"


class TicketStatus(StrEnum):
    received = "received"
    processing = "processing"
    awaiting_agent = "awaiting_agent"
    awaiting_approval = "awaiting_approval"
    escalated = "escalated"
    needs_information = "needs_information"
    resolved = "resolved"
    failed = "failed"
    closed = "closed"


class TicketCategory(StrEnum):
    order_tracking = "order_tracking"
    delayed_delivery = "delayed_delivery"
    missing_delivery = "missing_delivery"
    damaged_item = "damaged_item"
    incorrect_item = "incorrect_item"
    return_request = "return_request"
    refund_request = "refund_request"
    cancellation_request = "cancellation_request"
    product_policy_question = "product_policy_question"
    unknown = "unknown"


class TicketPriority(StrEnum):
    low = "low"
    normal = "normal"
    high = "high"
    urgent = "urgent"


class MessageSender(StrEnum):
    customer = "customer"
    agent = "agent"
    supervisor = "supervisor"
    system = "system"


class PolicyStatus(StrEnum):
    draft = "draft"
    active = "active"
    superseded = "superseded"
    expired = "expired"


def pg_enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    """Build a PostgreSQL native enum column type that stores member values."""
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        values_callable=lambda cls: [member.value for member in cls],
        validate_strings=True,
    )
