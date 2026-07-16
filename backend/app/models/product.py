"""Product catalogue model."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, CheckConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.db.mixins import TimestampMixin, UUIDPKMixin
from app.models.enums import ProductCategory, pg_enum

if TYPE_CHECKING:
    from app.models.order import OrderItem


class Product(UUIDPKMixin, TimestampMixin, Base):
    """A physical homeware product. Price is stored in integer pennies (GBP)."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("unit_price_pence > 0", name="unit_price_positive"),
        Index("ix_products_category", "category"),
    )

    sku: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[ProductCategory] = mapped_column(
        pg_enum(ProductCategory, "product_category"), nullable=False
    )
    unit_price_pence: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    order_items: Mapped[list[OrderItem]] = relationship(
        back_populates="product",
        passive_deletes=True,
    )

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<Product {self.sku} {self.name!r}>"
