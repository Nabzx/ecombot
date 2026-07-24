"""FastAPI application factory and entry point.

Run in development with::

    uvicorn app.main:app --reload

The application is assembled by ``create_app`` so tests can build isolated instances.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.router import api_router
from app.api.routes import health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown handling.

    Startup keeps side effects minimal (no eager DB connection) so the API can start
    even while PostgreSQL is still coming up; readiness reports the real state.
    """
    settings: Settings = app.state.settings
    logger.info(
        "Starting %s v%s (environment=%s, debug=%s)",
        settings.app_name,
        __version__,
        settings.environment,
        settings.debug,
    )
    yield
    # Import here to avoid binding the engine at import time.
    from app.db.session import dispose_engine

    await dispose_engine()
    logger.info("Shutdown complete")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build and configure a FastAPI application instance."""
    settings = settings or get_settings()
    configure_logging(settings.log_level, json_logs=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.state.settings = settings

    if settings.backend_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.backend_cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Health + metrics endpoints live at the root; business API is under the prefix.
    from app.api.routes import metrics as metrics_routes

    app.include_router(health.router)
    app.include_router(metrics_routes.router)
    app.include_router(api_router, prefix=settings.api_prefix)

    # Authenticated business endpoints (own /api/... prefixes).
    from app.api.routes import approvals as approval_routes
    from app.api.routes import auth as auth_routes
    from app.api.routes import execution as execution_routes

    app.include_router(auth_routes.router)
    app.include_router(approval_routes.router)
    app.include_router(execution_routes.router)

    # Development-only inspection endpoints.
    if settings.environment in ("development", "test"):
        from app.api.routes import (
            dev_models,
            dev_outbox,
            dev_retrieval,
            dev_workflows,
        )

        app.include_router(dev_retrieval.router)
        app.include_router(dev_models.router)
        app.include_router(dev_workflows.router)
        app.include_router(dev_outbox.router)

    return app


app = create_app()
