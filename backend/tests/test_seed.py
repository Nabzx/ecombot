"""Seed generator and seeding tests.

Determinism is checked without a database; population, integrity and reseed behaviour
are checked against a real PostgreSQL database.
"""

from __future__ import annotations

import hashlib

from app.models.enums import ShipmentStatus, TicketCategory
from app.seeds.generator import Dataset, build_dataset
from app.seeds.runner import gather_stats, reseed, seed
from app.seeds.verify import verify_data
from sqlalchemy.ext.asyncio import AsyncSession


def _fingerprint(dataset: Dataset) -> str:
    parts: list[str] = []
    parts += [o.order_number for o in dataset.orders]
    parts += [str(o.total_paid_pence) for o in dataset.orders]
    parts += [t.ticket_reference for t in dataset.tickets]
    parts += [c.email for c in dataset.customers]
    parts += [p.sku for p in dataset.products]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def test_generator_is_deterministic() -> None:
    assert _fingerprint(build_dataset()) == _fingerprint(build_dataset())


def test_generator_no_duplicate_identifiers() -> None:
    dataset = build_dataset()
    order_numbers = [o.order_number for o in dataset.orders]
    ticket_refs = [t.ticket_reference for t in dataset.tickets]
    emails = [c.email for c in dataset.customers]
    assert len(order_numbers) == len(set(order_numbers))
    assert len(ticket_refs) == len(set(ticket_refs))
    assert len(emails) == len(set(emails))


def test_generator_financial_consistency() -> None:
    for order in build_dataset().orders:
        subtotal = sum(i.line_total_pence for i in order.items)
        assert subtotal == order.subtotal_pence
        for item in order.items:
            assert item.line_total_pence == item.quantity * item.unit_price_pence
        assert order.total_paid_pence == (
            order.subtotal_pence + order.delivery_fee_pence - order.discount_pence
        )


def test_generator_has_fixtures_and_boundaries() -> None:
    dataset = build_dataset()
    tags = {t.seed_tag for t in dataset.tickets if t.seed_tag}
    assert "DEMO-RETURN-DAY-30" in tags
    assert "DEMO-RETURN-DAY-31" in tags
    assert "DEMO-PROMPT-INJECTION-001" in tags
    assert "DEMO-CROSS-CUSTOMER-001" in tags
    assert sum(1 for t in dataset.tickets if t.injection_flag) >= 10

    ship_statuses = {o.shipment.status for o in dataset.orders if o.shipment}
    assert ship_statuses == set(ShipmentStatus)  # every shipment status is represented

    categories = {t.category for t in dataset.tickets}
    assert categories == set(TicketCategory)  # all ten categories present


async def test_seed_populates_and_verifies(db_session: AsyncSession) -> None:
    stats = await seed(db_session)
    assert stats.counts["customers"] >= 50
    assert stats.counts["products"] >= 35
    assert stats.counts["orders"] >= 150
    assert stats.counts["tickets"] >= 80
    assert stats.adversarial_tickets >= 10
    assert stats.demo_fixtures >= 5
    assert stats.policy_versions_by_status["active"] >= 1
    assert stats.policy_versions_by_status["superseded"] >= 1
    assert stats.policy_versions_by_status["expired"] >= 1
    # All ten categories represented.
    assert all(count >= 0 for count in stats.tickets_by_category.values())
    assert len([c for c in stats.tickets_by_category.values() if c > 0]) == 10

    issues = await verify_data(db_session)
    assert issues == []


async def test_reseed_is_consistent(db_session: AsyncSession) -> None:
    first = await seed(db_session)
    second = await reseed(db_session)
    assert first.counts == second.counts
    assert await verify_data(db_session) == []
    # gather_stats after reseed matches too.
    again = await gather_stats(db_session)
    assert again.counts == second.counts
