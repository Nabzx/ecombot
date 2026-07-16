"""Order and OrderItem models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import OrderStatus, pg_enum

if TYPE_CHECKING:
    from app.models.customer import Customer
    from app.models.product import Product
    from app.models.shipment import Shipment
    from app.models.ticket import Ticket


class Order(UUIDPKMixin, TimestampMixin, Base):
    """A customer order. Money is stored in integer pennies (GBP).

    ``total_paid_pence`` is enforced by a database check to equal
    ``subtotal_pence + delivery_fee_pence - discount_pence``.
    """

    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("subtotal_pence >= 0", name="subtotal_non_negative"),
        CheckConstraint("delivery_fee_pence >= 0", name="delivery_fee_non_negative"),
        CheckConstraint("discount_pence >= 0", name="discount_non_negative"),
        CheckConstraint("total_paid_pence >= 0", name="total_paid_non_negative"),
        CheckConstraint(
            "total_paid_pence = subtotal_pence + delivery_fee_pence - discount_pence",
            name="total_paid_consistent",
        ),
        Index("ix_orders_customer_id", "customer_id"),
        Index("ix_orders_status", "status"),
        Index("ix_orders_placed_at", "placed_at"),
    )

    order_number: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[OrderStatus] = mapped_column(
        pg_enum(OrderStatus, "order_status"), nullable=False
    )
    subtotal_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    delivery_fee_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    discount_pence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_paid_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="OrderItem.created_at",
    )
    shipment: Mapped[Shipment | None] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="order",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Order {self.order_number} status={self.status}>"


class OrderItem(UUIDPKMixin, TimestampMixin, Base):
    """An order line; ``line_total_pence`` must equal quantity * unit price."""

    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("unit_price_pence >= 0", name="item_unit_price_non_negative"),
        CheckConstraint("line_total_pence >= 0", name="line_total_non_negative"),
        CheckConstraint(
            "line_total_pence = quantity * unit_price_pence",
            name="line_total_consistent",
        ),
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        Index("ix_order_items_order_id", "order_id"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("products.id", ondelete="RESTRICT"),
        nullable=False,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    line_total_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_returned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    returned_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="items")
    product: Mapped[Product] = relationship(back_populates="order_items")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<OrderItem order={self.order_id} product={self.product_id}>"
