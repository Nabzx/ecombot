"""Order repository. Cross-customer queries are always customer-scoped."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.order import Order, OrderItem
from app.repositories.base import DEFAULT_LIMIT, BaseRepository, Page, clamp_limit


class OrderRepository(BaseRepository):
    async def get(self, order_id: uuid.UUID) -> Order | None:
        return await self.session.get(Order, order_id)

    async def get_by_number(self, order_number: str) -> Order | None:
        stmt = select(Order).where(Order.order_number == order_number.strip())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_items(self, order_id: uuid.UUID) -> Order | None:
        """Load an order with its items (and each item's product) and its shipment."""
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.items).selectinload(OrderItem.product),
                selectinload(Order.shipment),
            )
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search_for_customer(
        self, customer_id: uuid.UUID, query: str
    ) -> list[Order]:
        """Find orders by order-number fragment, scoped to one customer (ownership)."""
        pattern = f"%{query.strip()}%"
        stmt = (
            select(Order)
            .where(
                Order.customer_id == customer_id,
                Order.order_number.ilike(pattern),
            )
            .order_by(Order.placed_at.desc(), Order.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list_for_customer(
        self, customer_id: uuid.UUID, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[Order]:
        limit = clamp_limit(limit)
        total = (
            await self.session.execute(
                select(func.count())
                .select_from(Order)
                .where(Order.customer_id == customer_id)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(Order)
                .where(Order.customer_id == customer_id)
                .order_by(Order.placed_at.desc(), Order.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return Page(items=list(rows), total=total, limit=limit, offset=offset)
