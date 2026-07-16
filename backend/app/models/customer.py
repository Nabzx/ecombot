"""Customer model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.pii import mask_email, mask_phone
from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import CustomerTier, pg_enum

if TYPE_CHECKING:
    from app.models.order import Order
    from app.models.ticket import Ticket


class Customer(UUIDPKMixin, TimestampMixin, Base):
    """A synthetic Meridian & Co. customer. Contains no real personal data."""

    __tablename__ = "customers"
    __table_args__ = (Index("ix_customers_last_first", "last_name", "first_name"),)

    external_reference: Mapped[str] = mapped_column(
        String(40), nullable=False, unique=True
    )
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    tier: Mapped[CustomerTier] = mapped_column(
        pg_enum(CustomerTier, "customer_tier"),
        nullable=False,
        default=CustomerTier.standard,
    )

    orders: Mapped[list[Order]] = relationship(
        back_populates="customer",
        cascade="save-update, merge",
        passive_deletes=True,
    )
    tickets: Mapped[list[Ticket]] = relationship(
        back_populates="customer",
        passive_deletes=True,
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def masked_email(self) -> str:
        return mask_email(self.email)

    @property
    def masked_phone(self) -> str:
        return mask_phone(self.phone)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Customer {self.external_reference} {self.email}>"
