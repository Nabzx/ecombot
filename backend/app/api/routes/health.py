"""Health, liveness and readiness endpoints.

- ``/health``      cheap combined status for humans and simple uptime pings.
- ``/health/live`` process liveness only; never touches dependencies.
- ``/health/ready`` verifies dependencies (PostgreSQL) and returns 503 if any fail.

The database check is injected as a dependency so tests can override it to simulate an
unavailable database without a real outage.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from app import __version__
from app.db.session import check_database_connection, check_migrations_applied
from app.schemas.health import HealthResponse, LivenessResponse, ReadinessResponse

router = APIRouter(tags=["health"])

SERVICE_NAME = "agentops-api"


async def database_ready() -> bool:
    """Dependency wrapper around the database readiness check (overridable in tests)."""
    return await check_database_connection()


async def migrations_ready() -> bool:
    """Wrapper around the migration readiness check (overridable in tests)."""
    return await check_migrations_applied()


@router.get("/health", response_model=HealthResponse, summary="Combined health status")
async def health() -> HealthResponse:
    return HealthResponse(service=SERVICE_NAME, version=__version__)


@router.get(
    "/health/live",
    response_model=LivenessResponse,
    summary="Liveness probe",
)
async def health_live() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/health/ready",
    response_model=ReadinessResponse,
    summary="Readiness probe",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ReadinessResponse,
            "description": "A required dependency is unavailable.",
        }
    },
)
async def health_ready(
    response: Response,
    db_ok: Annotated[bool, Depends(database_ready)],
    migrations_ok: Annotated[bool, Depends(migrations_ready)],
) -> ReadinessResponse:
    checks: dict[str, str] = {
        "database": "ok" if db_ok else "error",
        "migrations": "ok" if migrations_ok else "error",
    }
    all_ok = all(value == "ok" for value in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadinessResponse(
        status="ready" if all_ok else "not_ready",
        service=SERVICE_NAME,
        version=__version__,
        checks={k: ("ok" if v == "ok" else "error") for k, v in checks.items()},
    )
