"""Pydantic response schemas for the health endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Combined health summary returned by ``GET /health``."""

    status: Literal["ok"] = "ok"
    service: str = Field(examples=["agentops-api"])
    version: str = Field(examples=["0.1.0"])


class LivenessResponse(BaseModel):
    """Liveness result returned by ``GET /health/live``."""

    status: Literal["alive"] = "alive"


class ReadinessResponse(BaseModel):
    """Readiness result returned by ``GET /health/ready``.

    ``status`` is ``ready`` only when every dependency check passes; otherwise the
    endpoint responds with HTTP 503 and ``status`` is ``not_ready``.
    """

    status: Literal["ready", "not_ready"]
    service: str = Field(examples=["agentops-api"])
    version: str = Field(examples=["0.1.0"])
    checks: dict[str, Literal["ok", "error"]] = Field(
        description="Per-dependency readiness results.",
        examples=[{"database": "ok"}],
    )
