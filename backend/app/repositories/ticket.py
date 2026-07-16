"""Ticket repository."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.models.enums import TicketCategory, TicketStatus
from app.models.ticket import Ticket
from app.repositories.base import DEFAULT_LIMIT, BaseRepository, Page, clamp_limit


class TicketRepository(BaseRepository):
    async def get(self, ticket_id: uuid.UUID) -> Ticket | None:
        return await self.session.get(Ticket, ticket_id)

    async def get_by_reference(self, reference: str) -> Ticket | None:
        stmt = select(Ticket).where(Ticket.ticket_reference == reference.strip())
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_by_seed_tag(self, seed_tag: str) -> Ticket | None:
        stmt = select(Ticket).where(Ticket.seed_tag == seed_tag)
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def get_with_messages(self, ticket_id: uuid.UUID) -> Ticket | None:
        """Load a ticket with its messages ordered by sequence number."""
        stmt = (
            select(Ticket)
            .where(Ticket.id == ticket_id)
            .options(selectinload(Ticket.messages))
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def list(
        self,
        *,
        status: TicketStatus | None = None,
        category: TicketCategory | None = None,
        customer_id: uuid.UUID | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Page[Ticket]:
        limit = clamp_limit(limit)
        base = select(Ticket)
        count = select(func.count()).select_from(Ticket)
        if status is not None:
            base = base.where(Ticket.status == status)
            count = count.where(Ticket.status == status)
        if category is not None:
            base = base.where(Ticket.category == category)
            count = count.where(Ticket.category == category)
        if customer_id is not None:
            base = base.where(Ticket.customer_id == customer_id)
            count = count.where(Ticket.customer_id == customer_id)
        total = (await self.session.execute(count)).scalar_one()
        rows = (
            await self.session.execute(
                base.order_by(Ticket.received_at.desc(), Ticket.id)
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return Page(items=list(rows), total=total, limit=limit, offset=offset)
