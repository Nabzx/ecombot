"""Seed, reset and statistics operations against the database.

- ``seed`` refuses to run against a non-empty database (fail clearly rather than
  corrupt or duplicate data).
- ``reset`` truncates every domain table and is development-only.
- ``reseed`` resets then seeds.

Objects are flushed in FK dependency order because foreign keys are set as raw column
values rather than via relationships.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import OrderStatus, PolicyStatus, TicketCategory
from app.models.order import Order
from app.models.policy import PolicyVersion
from app.models.ticket import Ticket
from app.models.user import User
from app.seeds.generator import build_dataset

# Child-to-parent order for TRUNCATE (CASCADE covers FKs, listed for clarity).
_TABLES = (
    "ticket_messages",
    "tickets",
    "shipments",
    "order_items",
    "orders",
    "policy_versions",
    "policies",
    "products",
    "customers",
    "users",
)


class SeedError(RuntimeError):
    """Raised when seeding cannot proceed (e.g. the database is not empty)."""


@dataclass(slots=True)
class SeedStats:
    counts: dict[str, int] = field(default_factory=dict)
    orders_by_status: dict[str, int] = field(default_factory=dict)
    tickets_by_category: dict[str, int] = field(default_factory=dict)
    policy_versions_by_status: dict[str, int] = field(default_factory=dict)
    adversarial_tickets: int = 0
    demo_fixtures: int = 0


async def _is_empty(session: AsyncSession) -> bool:
    count = await session.scalar(select(func.count()).select_from(User))
    return (count or 0) == 0


async def seed(session: AsyncSession) -> SeedStats:
    """Populate an empty database with the deterministic dataset."""
    if not await _is_empty(session):
        raise SeedError(
            "Database already contains data. Use 'reseed' to reset and reseed."
        )
    dataset = build_dataset()
    session.add_all(dataset.users)
    session.add_all(dataset.products)
    session.add_all(dataset.customers)
    await session.flush()
    session.add_all(dataset.policies)
    await session.flush()
    session.add_all(dataset.orders)
    await session.flush()
    session.add_all(dataset.tickets)
    await session.flush()
    await session.commit()
    return await gather_stats(session)


async def reset(session: AsyncSession) -> None:
    """Development-only: remove all domain data."""
    await session.execute(
        text(f"TRUNCATE TABLE {', '.join(_TABLES)} RESTART IDENTITY CASCADE")
    )
    await session.commit()


async def reseed(session: AsyncSession) -> SeedStats:
    await reset(session)
    return await seed(session)


async def _count(session: AsyncSession, table: str) -> int:
    result = await session.scalar(text(f"SELECT count(*) FROM {table}"))  # noqa: S608
    return int(result or 0)


async def gather_stats(session: AsyncSession) -> SeedStats:
    stats = SeedStats()
    for table in _TABLES:
        stats.counts[table] = await _count(session, table)

    for status in OrderStatus:
        stats.orders_by_status[status.value] = (
            await session.scalar(
                select(func.count()).select_from(Order).where(Order.status == status)
            )
            or 0
        )
    for category in TicketCategory:
        stats.tickets_by_category[category.value] = (
            await session.scalar(
                select(func.count())
                .select_from(Ticket)
                .where(Ticket.category == category)
            )
            or 0
        )
    for pstatus in PolicyStatus:
        stats.policy_versions_by_status[pstatus.value] = (
            await session.scalar(
                select(func.count())
                .select_from(PolicyVersion)
                .where(PolicyVersion.status == pstatus)
            )
            or 0
        )
    stats.adversarial_tickets = (
        await session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.injection_flag.is_(True))
        )
        or 0
    )
    stats.demo_fixtures = (
        await session.scalar(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.seed_tag.like("DEMO-%"))
        )
        or 0
    )
    return stats
