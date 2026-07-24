"""Authenticated execution-API tests (S6).

Verifies RBAC on the read-only action/outbox endpoints, that no production endpoint
executes an action, and that the dev process-one endpoint drives a real refund to
success and is PII-safe.
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
from tests.test_approval_service import _truncate_all
from tests.test_outbox_execution import REFUND_TICKET

pytestmark = pytest.mark.usefixtures("_prepare_test_database")


@pytest.fixture
async def api() -> AsyncIterator[tuple[AsyncClient, async_sessionmaker[AsyncSession]]]:
    from app.seeds.runner import seed

    engine = create_async_engine(TEST_DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_here = False
    async with factory() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE refund_ledger_entries, executed_actions, "
                "outbox_attempts, outbox_jobs, approval_requests, workflow_runs, "
                "idempotency_records RESTART IDENTITY CASCADE"
            )
        )
        if (
            await session.scalar(select(Ticket).where(Ticket.seed_tag == REFUND_TICKET))
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
                await session.commit()
            await engine.dispose()


async def _token(client: AsyncClient, email: str) -> str:
    r = await client.post(
        "/api/auth/login", json={"email": email, "password": DEV_PASSWORD}
    )
    assert r.status_code == 200, r.text
    token: str = r.json()["access_token"]
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _emails(factory: async_sessionmaker[AsyncSession]) -> tuple[str, str]:
    async with factory() as session:
        agent = await session.scalar(
            select(User).where(User.role == UserRole.support_agent).limit(1)
        )
        supervisor = await session.scalar(
            select(User).where(User.role == UserRole.supervisor).order_by(User.email)
        )
        assert agent is not None and supervisor is not None
        return agent.email, supervisor.email


async def test_agent_cannot_inspect_outbox_internals(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, _ = await _emails(factory)
    token = await _token(client, agent_email)
    # Agents may read action status but not worker/outbox diagnostics.
    assert (await client.get("/api/actions", headers=_auth(token))).status_code == 200
    assert (await client.get("/api/outbox", headers=_auth(token))).status_code == 403
    assert (
        await client.get("/api/outbox/stats", headers=_auth(token))
    ).status_code == 403


async def test_supervisor_can_inspect_outbox(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    _, supervisor_email = await _emails(factory)
    token = await _token(client, supervisor_email)
    for url in ("/api/outbox", "/api/outbox/stats", "/api/actions"):
        assert (await client.get(url, headers=_auth(token))).status_code == 200


async def test_audit_api_rbac_and_chain(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, supervisor_email = await _emails(factory)
    agent = await _token(client, agent_email)
    supervisor = await _token(client, supervisor_email)

    # A login already produced audit events; the supervisor can list and verify them.
    listed = await client.get("/api/audit", headers=_auth(supervisor))
    assert listed.status_code == 200
    verify = await client.get("/api/audit/verify", headers=_auth(supervisor))
    assert verify.status_code == 200
    assert verify.json()["ok"] is True
    # An agent may not read the full audit trail (worker/security diagnostics).
    assert (await client.get("/api/audit", headers=_auth(agent))).status_code == 403
    # But the audit rows are PII-safe: no emails or passwords in any summary/metadata.
    for row in listed.json():
        assert "@" not in row["summary"]
        assert "password" not in str(row["metadata"]).lower()


async def test_execution_endpoints_require_authentication(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, _ = api
    for url in ("/api/actions", "/api/outbox", "/api/outbox/stats"):
        assert (await client.get(url)).status_code == 401
    # There is no production endpoint that executes an action directly.
    assert (
        await client.post(f"/api/actions/{uuid.uuid4()}/execute")
    ).status_code == 404


async def test_dev_process_one_drives_refund_and_is_pii_safe(
    api: tuple[AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = api
    agent_email, supervisor_email = await _emails(factory)

    # Drive a refund workflow to an approved outbox job over HTTP.
    async with factory() as session:
        ticket = await session.scalar(
            select(Ticket).where(Ticket.seed_tag == REFUND_TICKET)
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

    agent_token = await _token(client, agent_email)
    created = await client.post(
        f"/api/proposed-actions/{action_id}/approval",
        json={"proposed_action_id": str(action_id)},
        headers=_auth(agent_token),
    )
    assert created.status_code == 201
    approval_id = created.json()["id"]

    supervisor_token = await _token(client, supervisor_email)
    approved = await client.post(
        f"/api/approvals/{approval_id}/approve",
        json={},
        headers=_auth(supervisor_token),
    )
    assert approved.status_code == 200
    assert approved.json()["outbox_job_created"] is True

    # Process one job through the exact worker via the dev endpoint.
    processed = await client.post(
        "/api/dev/outbox/process-one", headers=_auth(supervisor_token)
    )
    assert processed.status_code == 200, processed.text
    body = processed.json()
    assert body["processed"] == 1
    assert body["by_outcome"].get("succeeded") == 1

    # The action is visible and PII-safe (no customer contact fields, no full payload).
    actions = await client.get("/api/actions", headers=_auth(supervisor_token))
    assert actions.status_code == 200
    rows = actions.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["business_effect_reference"].startswith("SIM-REF-")
    assert "customer_email" not in row
    assert "payload_json" not in row
    assert "customer_message" not in row
