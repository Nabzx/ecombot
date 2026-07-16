"""Repository tests against a real PostgreSQL database."""

from __future__ import annotations

import uuid
from datetime import timedelta

from app.repositories.customer import CustomerRepository
from app.repositories.order import OrderRepository
from app.repositories.policy import PolicyRepository
from app.repositories.ticket import TicketRepository
from sqlalchemy.ext.asyncio import AsyncSession

from tests.factories import TODAY, build_sample


async def test_customer_get_by_email(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = CustomerRepository(db_session)
    found = await repo.get_by_email("JANE.DOE@example.com")  # case-insensitive
    assert found is not None
    assert found.external_reference == "MER-C-00001"


async def test_customer_search_by_name_and_reference(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = CustomerRepository(db_session)
    by_name = await repo.search("Jane")
    assert by_name.total == 1
    assert by_name.items[0].email == "jane.doe@example.com"

    by_ref = await repo.search("MER-C-00002")
    assert by_ref.total == 1
    assert by_ref.items[0].email == "john.smith@example.com"


async def test_customer_not_found(db_session: AsyncSession) -> None:
    repo = CustomerRepository(db_session)
    assert await repo.get(uuid.uuid4()) is None
    assert await repo.get_by_email("nobody@example.com") is None


async def test_order_get_by_number_and_items(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = OrderRepository(db_session)
    order = await repo.get_by_number("MER-2026-000001")
    assert order is not None

    with_items = await repo.get_with_items(order.id)
    assert with_items is not None
    assert len(with_items.items) == 1
    assert with_items.shipment is not None
    assert with_items.items[0].product.sku == "MER-KIT-T01"


async def test_order_search_is_customer_scoped(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    repo = OrderRepository(db_session)
    # Customer B's order number must not be findable under customer A (ownership).
    a_results = await repo.search_for_customer(sample.customer_a.id, "MER-2026-000003")
    assert a_results == []
    b_results = await repo.search_for_customer(sample.customer_b.id, "MER-2026-000003")
    assert len(b_results) == 1


async def test_order_list_for_customer_pagination(db_session: AsyncSession) -> None:
    sample = await build_sample(db_session)
    repo = OrderRepository(db_session)
    page = await repo.list_for_customer(sample.customer_a.id, limit=1, offset=0)
    assert page.total == 2  # customer A has two orders
    assert len(page.items) == 1
    page2 = await repo.list_for_customer(sample.customer_a.id, limit=1, offset=1)
    assert len(page2.items) == 1
    assert page.items[0].id != page2.items[0].id


async def test_ticket_with_ordered_messages(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = TicketRepository(db_session)
    ticket = await repo.get_by_reference("TKT-2026-000001")
    assert ticket is not None

    loaded = await repo.get_with_messages(ticket.id)
    assert loaded is not None
    sequences = [m.sequence_number for m in loaded.messages]
    assert sequences == [1, 2]


async def test_policy_active_version_for_date(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = PolicyRepository(db_session)
    active = await repo.get_active_version_for_date("returns", TODAY)
    assert active is not None
    assert active.version == 2  # the active one, not the superseded v1
    assert active.status.value == "active"

    versions = await repo.find_versions_by_topic("returns")
    assert [v.version for v in versions] == [1, 2]

    # Before any version was effective, there is no active version.
    long_ago = TODAY - timedelta(days=5000)
    assert await repo.get_active_version_for_date("returns", long_ago) is None


async def test_policy_list(db_session: AsyncSession) -> None:
    await build_sample(db_session)
    repo = PolicyRepository(db_session)
    policies = await repo.list_policies()
    assert any(p.topic == "returns" for p in policies)
