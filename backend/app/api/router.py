"""Central API router.

Aggregates all route modules. Health endpoints are registered at the application root
(see ``main.py``); this router carries the versioned business API mounted under
``settings.api_prefix`` and is intentionally empty until S1 introduces domain routes.
"""

from __future__ import annotations

from fastapi import APIRouter

api_router = APIRouter()

# Business routers (tickets, approvals, evaluations, ...) are added in later stages.
