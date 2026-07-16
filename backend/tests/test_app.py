"""Tests for application assembly and the database readiness helper."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.db import session as session_module
from app.db.session import check_database_connection
from app.main import create_app
from starlette.routing import Route


def test_create_app_metadata() -> None:
    app = create_app(Settings(environment="test", jwt_secret="test-secret"))
    assert app.title == "AgentOps API"
    assert app.version == "0.1.0"


def test_health_routes_registered() -> None:
    app = create_app(Settings(environment="test", jwt_secret="test-secret"))
    paths = {route.path for route in app.routes if isinstance(route, Route)}
    assert {"/health", "/health/live", "/health/ready"} <= paths


async def test_check_database_connection_returns_false_without_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no reachable PostgreSQL, readiness reports False rather than raising."""
    settings = Settings(
        environment="test",
        jwt_secret="test-secret",
        # Unroutable port so the connection fails fast rather than hanging.
        database_url="postgresql+asyncpg://agentops:agentops@127.0.0.1:1/agentops",
    )
    await session_module.dispose_engine()
    monkeypatch.setattr(session_module, "get_settings", lambda: settings)
    try:
        assert await check_database_connection() is False
    finally:
        await session_module.dispose_engine()
