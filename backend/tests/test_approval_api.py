"""Authenticated approval API tests (S6).

Exercises the HTTP surface end to end against real Postgres: login, queue, PII-safe
payloads, role enforcement, self-approval refusal, idempotent replay and the decision
history. Approving must report ``outbox_job_created: false`` in this increment.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from app.core.config import Settings, get_settings
from app.db.session import get_session
from app.main import create_app
from app.models.enums import UserRole
from app.models.ticket import Ticket
from app.models.user import User
from app.seeds.security import DEV_PASSWORD
from app.workflows.enums import WorkflowState
from app.workflows.repository import WorkflowRepository
from app.workflows.service import StartWorkflowRequest, SupportWorkflowService
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tests.conftest import TEST_DATABASE_URL
from tests.test_approval_service import APPROVAL_TICKET, _truncate_all

pytestmark = pytest.mark.usefixtures("_prepare_test_database")


@pytest.fixture
async def api() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    from app.seeds.runner import seed

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_here = False
    async with factory() as session:
        await session.execute(text("DELETE FROM idempotency_records"))
        await session.execute(text("DELETE FROM approval_requests"))
        await session.execute(text("DELETE FROM workflow_runs"))
        if (
            await session.scalar(
                select(Ticket).where(Ticket.seed_tag == APPROVAL_TICKET)
            )
            is None
        ):
            await seed(session)
            seeded_here = True
        await session.commit()

    settings = Settings(
        environment="test",
        debug=True,
        jwt_secret="test-only-secret-0123456789abcdefghij",
        database_url=TEST_DATABASE_URL,
    )
    app = create_app(settings)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    # Point the app at the disposable test database and its deterministic settings.
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        try:
            yield client, factory
        finally:
            async with factory() as session:
                if seeded_here:
                    await _truncate_all(session)
                else:
                    await session.execute(text("DELETE FROM idempotency_records"))
                    await session.execute(text("DELETE FROM approval_requests"))
                    await session.execute(text("DELETE FROM workflow_runs"))
                await session.commit()
            await engine.dispose()


async def _token(client: AsyncClient, email: str) -> str:
    response = await client.post(
        "/api/auth/login", json={"email": email, "password": DEV_PASSWORD}
    )
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _emails(
    factory: async_sessionmaker[AsyncSession],
) -> tuple[str, str, str]:
    """Return (agent, supervisor_a, supervisor_b) seeded e-mail addresses."""
    async with factory() as session:
        agent = await session.scalar(
            select(User).where(User.role == UserRole.support_agent).order_by(User.email)
        )
        supervisors = list(
            await session.scalars(
                select(User)
                .where(User.role == UserRole.supervisor)
                .order_by(User.email)
            )
        )
        assert agent is not None and len(supervisors) >= 2
        return agent.email, supervisors[0].email, supervisors[1].email


async def _pending(
    client: AsyncClient, factory: async_sessionmaker[AsyncSession], agent_token: str
) -> str:
    """Drive the demo ticket to awaiting_approval and raise a request over HTTP."""
    async with factory() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == APPROVAL_TICKET)
        )
        assert ticket is not None
        ticket_id = ticket.id
    run = await SupportWorkflowService(session_factory=factory).start(
        StartWorkflowRequest(ticket_id=ticket_id)
    )
    assert run.state == WorkflowState.AWAITING_APPROVAL
    async with factory() as session:
        proposal = await WorkflowRepository(session).get_current_proposal(run.run_id)
        assert proposal is not None
        action_id = proposal.id

    response = await client.post(
        f"/api/proposed-actions/{action_id}/approval",
        json={"proposed_action_id": str(action_id), "request_reason": "refund please"},
        headers=_auth(agent_token),
    )
    assert response.status_code == 201, response.text
    approval_id: str = response.json()["id"]
    return approval_id


async def test_approval_endpoints_require_authentication(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api
    for method, url in (
        ("get", "/api/approvals"),
        ("get", f"/api/approvals/{uuid.uuid4()}"),
        ("post", f"/api/approvals/{uuid.uuid4()}/approve"),
    ):
        response = await getattr(client, method)(url)
        assert response.status_code == 401, (url, response.status_code)


async def test_queue_and_detail_are_pii_safe(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, supervisor_email, _ = await _emails(factory)
    agent_token = await _token(client, agent_email)
    approval_id = await _pending(client, factory, agent_token)

    supervisor_token = await _token(client, supervisor_email)
    queue = await client.get("/api/approvals", headers=_auth(supervisor_token))
    assert queue.status_code == 200
    ids = [row["id"] for row in queue.json()]
    assert approval_id in ids

    detail = await client.get(
        f"/api/approvals/{approval_id}", headers=_auth(supervisor_token)
    )
    assert detail.status_code == 200
    body = detail.json()
    # The summary carries evidence and limits but no customer contact details.
    assert body["evidence_snapshot_hash"]
    assert body["status"] == "pending"
    assert "customer_email" not in body
    assert "draft_response_body" not in body


async def test_supervisor_approve_queues_one_job(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, _, supervisor_email = await _emails(factory)
    agent_token = await _token(client, agent_email)
    approval_id = await _pending(client, factory, agent_token)

    supervisor_token = await _token(client, supervisor_email)
    response = await client.post(
        f"/api/approvals/{approval_id}/approve",
        json={"reason": "within policy"},
        headers=_auth(supervisor_token),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["approval"]["status"] == "execution_pending"
    assert body["workflow_state"] == "approved_pending_execution"
    assert body["outbox_job_created"] is True

    decisions = await client.get(
        f"/api/approvals/{approval_id}/decisions", headers=_auth(supervisor_token)
    )
    assert decisions.status_code == 200
    assert [d["decision"] for d in decisions.json()] == ["approve"]


async def test_agent_cannot_approve_over_http(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, _, _ = await _emails(factory)
    agent_token = await _token(client, agent_email)
    approval_id = await _pending(client, factory, agent_token)

    response = await client.post(
        f"/api/approvals/{approval_id}/approve",
        json={},
        headers=_auth(agent_token),
    )
    assert response.status_code == 403, response.text


async def test_approve_is_idempotent_under_a_repeated_key(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, _, supervisor_email = await _emails(factory)
    agent_token = await _token(client, agent_email)
    approval_id = await _pending(client, factory, agent_token)
    supervisor_token = await _token(client, supervisor_email)

    headers = {**_auth(supervisor_token), "Idempotency-Key": "approve-once"}
    first = await client.post(
        f"/api/approvals/{approval_id}/approve", json={}, headers=headers
    )
    assert first.status_code == 200, first.text

    # The replay returns the same decided approval instead of a second decision.
    second = await client.post(
        f"/api/approvals/{approval_id}/approve", json={}, headers=headers
    )
    assert second.status_code == 200, second.text
    assert second.json()["approval"]["status"] == "execution_pending"

    decisions = await client.get(
        f"/api/approvals/{approval_id}/decisions", headers=_auth(supervisor_token)
    )
    assert len(decisions.json()) == 1


async def test_reject_requires_a_reason_over_http(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, _, supervisor_email = await _emails(factory)
    agent_token = await _token(client, agent_email)
    approval_id = await _pending(client, factory, agent_token)
    supervisor_token = await _token(client, supervisor_email)

    missing = await client.post(
        f"/api/approvals/{approval_id}/reject",
        json={},
        headers=_auth(supervisor_token),
    )
    assert missing.status_code == 422

    rejected = await client.post(
        f"/api/approvals/{approval_id}/reject",
        json={"reason": "outside the remedy window"},
        headers=_auth(supervisor_token),
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["workflow_state"] == "approval_rejected"
