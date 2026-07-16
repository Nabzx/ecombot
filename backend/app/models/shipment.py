"""Shipment model (one per order in the MVP)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import ShipmentStatus, pg_enum

if TYPE_CHECKING:
    from app.models.order import Order


class Shipment(UUIDPKMixin, TimestampMixin, Base):
    """Delivery record for an order. Carrier names are fictional/synthetic.

    Database checks keep ``delivered_at`` consistent with status: a delivered shipment
    must have a delivery time, and a non-delivered shipment must not.
    """

    __tablename__ = "shipments"
    __table_args__ = (
        CheckConstraint(
            "status <> 'delivered' OR delivered_at IS NOT NULL",
            name="delivered_requires_date",
        ),
        CheckConstraint(
            "status = 'delivered' OR delivered_at IS NULL",
            name="undelivered_has_no_date",
        ),
        CheckConstraint(
            "delivered_at IS NULL OR shipped_at IS NULL OR delivered_at >= shipped_at",
            name="delivered_after_shipped",
        ),
        Index("ix_shipments_status", "status"),
    )

    order_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    carrier: Mapped[str] = mapped_column(String(60), nullable=False)
    tracking_number: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    status: Mapped[ShipmentStatus] = mapped_column(
        pg_enum(ShipmentStatus, "shipment_status"), nullable=False
    )
    promised_delivery_date: Mapped[date] = mapped_column(Date, nullable=False)
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[Order] = relationship(back_populates="shipment")

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Shipment {self.tracking_number} status={self.status}>"
