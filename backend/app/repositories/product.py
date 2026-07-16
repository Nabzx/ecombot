"""Product repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select

from app.models.enums import ProductCategory
from app.models.product import Product
from app.repositories.base import DEFAULT_LIMIT, BaseRepository, Page, clamp_limit


class ProductRepository(BaseRepository):
    async def get(self, product_id: uuid.UUID) -> Product | None:
        return await self.session.get(Product, product_id)

    async def get_by_sku(self, sku: str) -> Product | None:
        stmt = select(Product).where(Product.sku == sku.strip())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list_by_category(self, category: ProductCategory) -> list[Product]:
        stmt = (
            select(Product)
            .where(Product.category == category)
            .order_by(Product.name, Product.id)
        )
        return list((await self.session.execute(stmt)).scalars())

    async def list(
        self,
        *,
        active_only: bool = False,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[Product]:
        limit = clamp_limit(limit)
        base = select(Product)
        count = select(func.count()).select_from(Product)
        if active_only:
            base = base.where(Product.is_active.is_(True))
            count = count.where(Product.is_active.is_(True))
        total = (await self.session.execute(count)).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(Product.name, Product.id).limit(limit).offset(offset)
            )
        ).scalars()
        return Page(items=list(rows), total=total, limit=limit, offset=offset)
