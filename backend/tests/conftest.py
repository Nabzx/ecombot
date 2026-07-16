"""Shared pytest fixtures.

Tests run fully offline: no PostgreSQL, no external network and no paid APIs. The
database readiness dependency is overridden so health/readiness behaviour can be
exercised deterministically in both the reachable and unreachable states.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from app.api.routes.health import database_ready
from app.core.config import Settings
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def test_settings() -> Settings:
    """Settings for a test environment with deterministic values."""
    return Settings(
        environment="test",
        debug=True,
        jwt_secret="test-secret",
        backend_cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
async def client_db_ok(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client against an app whose database is reachable."""
    app = create_app(test_settings)

    async def _ready() -> bool:
        return True

    app.dependency_overrides[database_ready] = _ready
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def client_db_down(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client against an app whose database is unreachable."""
    app = create_app(test_settings)

    async def _not_ready() -> bool:
        return False

    app.dependency_overrides[database_ready] = _not_ready
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()
