"""Customer repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, or_, select

from app.models.customer import Customer
from app.repositories.base import DEFAULT_LIMIT, BaseRepository, Page, clamp_limit


class CustomerRepository(BaseRepository):
    async def get(self, customer_id: uuid.UUID) -> Customer | None:
        return await self.session.get(Customer, customer_id)

    async def get_by_email(self, email: str) -> Customer | None:
        stmt = select(Customer).where(Customer.email == email.strip().lower())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_external_reference(self, reference: str) -> Customer | None:
        stmt = select(Customer).where(Customer.external_reference == reference.strip())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def search(
        self, query: str, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[Customer]:
        """Case-insensitive search over email, external reference and full name."""
        limit = clamp_limit(limit)
        pattern = f"%{query.strip()}%"
        predicate = or_(
            Customer.email.ilike(pattern),
            Customer.external_reference.ilike(pattern),
            func.concat(Customer.first_name, " ", Customer.last_name).ilike(pattern),
        )
        total = (
            await self.session.execute(
                select(func.count()).select_from(Customer).where(predicate)
            )
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(Customer)
                .where(predicate)
                .order_by(Customer.last_name, Customer.first_name, Customer.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return Page(items=list(rows), total=total, limit=limit, offset=offset)

    async def list(
        self, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> Page[Customer]:
        limit = clamp_limit(limit)
        total = (
            await self.session.execute(select(func.count()).select_from(Customer))
        ).scalar_one()
        rows = (
            await self.session.execute(
                select(Customer)
                .order_by(Customer.created_at, Customer.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return Page(items=list(rows), total=total, limit=limit, offset=offset)
