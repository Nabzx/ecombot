"""Reliability middleware and circuit-breaker tests (S7)."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.main import create_app
from app.observability.circuit_breaker import BreakerState, CircuitBreaker
from httpx import ASGITransport, AsyncClient


def _settings(**kw: object) -> Settings:
    return Settings(
        environment="test",
        jwt_secret="test-secret-0123456789abcdef",
        **kw,  # type: ignore[arg-type]
    )


# --- circuit breaker ----------------------------------------------------------------
def test_breaker_opens_after_threshold_and_recovers() -> None:
    breaker = CircuitBreaker("hosted", threshold=3, cooldown_seconds=10.0)
    assert breaker.state is BreakerState.CLOSED
    for _ in range(3):
        assert breaker.allow(now=0.0)
        breaker.record_failure(now=0.0)
    # Open now; requests are denied until the cooldown passes.
    assert breaker.state is BreakerState.OPEN
    assert not breaker.allow(now=5.0)
    # After cooldown, one probe is allowed (half-open).
    assert breaker.allow(now=11.0)
    assert breaker.state is BreakerState.HALF_OPEN
    # A successful probe closes the breaker.
    breaker.record_success()
    assert breaker.state is BreakerState.CLOSED


def test_half_open_failure_reopens() -> None:
    breaker = CircuitBreaker("x", threshold=1, cooldown_seconds=5.0)
    breaker.record_failure(now=0.0)
    assert breaker.state is BreakerState.OPEN
    assert breaker.allow(now=6.0)  # half-open probe
    breaker.record_failure(now=6.0)
    assert breaker.state is BreakerState.OPEN


# --- middleware ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_request_id_is_assigned_and_echoed() -> None:
    app = create_app(_settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health/live")
        assert response.status_code == 200
        assert response.headers["X-Request-ID"]
        # An inbound, well-formed correlation id is adopted and echoed back.
        r2 = await client.get(
            "/health/live", headers={"X-Correlation-ID": "cor-abc123"}
        )
        assert r2.headers["X-Correlation-ID"] == "cor-abc123"


@pytest.mark.asyncio
async def test_rate_limit_returns_429_envelope() -> None:
    app = create_app(_settings(rate_limit_per_minute=2))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        assert (await client.get("/health/live")).status_code == 200
        assert (await client.get("/health/live")).status_code == 200
        limited = await client.get("/health/live")
        assert limited.status_code == 429
        body = limited.json()
        assert body["code"] == "rate_limited"
        assert "request_id" in body


@pytest.mark.asyncio
async def test_request_too_large_is_rejected() -> None:
    app = create_app(_settings(max_request_bytes=10))
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/login",
            content=b"x" * 100,
            headers={"content-type": "application/json"},
        )
    assert response.status_code == 413
    assert response.json()["code"] == "request_too_large"
