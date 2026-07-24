"""In-process metrics registry + /metrics endpoint tests (S7)."""

from __future__ import annotations

import pytest
from app.observability.metrics import MetricsRegistry
from httpx import ASGITransport, AsyncClient


def test_counter_gauge_histogram_render() -> None:
    reg = MetricsRegistry()
    reg.inc("reqs_total", outcome="ok")
    reg.inc("reqs_total", outcome="ok")
    reg.inc("reqs_total", outcome="error")
    reg.set_gauge("queue_depth", 3.0)
    reg.observe("latency_seconds", 0.02)
    reg.observe("latency_seconds", 0.4)

    assert reg.get_counter("reqs_total", outcome="ok") == 2.0
    text = reg.render()
    assert "# TYPE reqs_total counter" in text
    assert 'reqs_total{outcome="ok"} 2.0' in text
    assert 'reqs_total{outcome="error"} 1.0' in text
    assert "queue_depth 3.0" in text
    # Histogram bucket / sum / count lines are present and cumulative.
    assert "latency_seconds_bucket" in text
    assert "latency_seconds_count 2" in text
    assert 'le="+Inf"' in text


def test_labels_are_escaped() -> None:
    reg = MetricsRegistry()
    reg.inc("m", label='a"b\\c')
    assert 'a\\"b\\\\c' in reg.render()


@pytest.mark.asyncio
async def test_metrics_endpoint_serves_prometheus_text() -> None:
    from app.core.config import Settings
    from app.main import create_app

    settings = Settings(environment="test", jwt_secret="test-secret-0123456789abcdef")
    app = create_app(settings)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    # No authentication required (scrape convention); body is Prometheus text.
    assert isinstance(response.text, str)
