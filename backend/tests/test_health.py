"""Tests for the health, liveness and readiness endpoints."""

from __future__ import annotations

from app import __version__
from httpx import AsyncClient


async def test_health_ok(client_db_ok: AsyncClient) -> None:
    response = await client_db_ok.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "status": "ok",
        "service": "agentops-api",
        "version": __version__,
    }


async def test_liveness(client_db_ok: AsyncClient) -> None:
    response = await client_db_ok.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


async def test_readiness_ok_when_database_reachable(client_db_ok: AsyncClient) -> None:
    response = await client_db_ok.get("/health/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["checks"] == {"database": "ok"}
    assert body["version"] == __version__


async def test_readiness_503_when_database_unreachable(
    client_db_down: AsyncClient,
) -> None:
    response = await client_db_down.get("/health/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["checks"] == {"database": "error"}
