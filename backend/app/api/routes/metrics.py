"""Prometheus metrics endpoint (S7).

``GET /metrics`` renders the in-process registry in Prometheus text format. It is
unauthenticated by convention (standard for scraping on an internal network) and safe
because every metric label carries only low-cardinality, non-PII values.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.observability.metrics import registry

router = APIRouter(tags=["observability"])

_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(content=registry().render(), media_type=_CONTENT_TYPE)
