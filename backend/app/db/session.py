"""Async SQLAlchemy engine, session factory and FastAPI dependencies.

The engine is created lazily from settings and cached, so importing this module never
opens a connection (important for tests and Alembic). Call ``dispose_engine`` on
shutdown to release the pool cleanly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url_str,
            echo=settings.db_echo,
            pool_pre_ping=True,
            future=True,
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a database session with commit/rollback handling."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def check_database_connection() -> bool:
    """Return ``True`` if a trivial query against PostgreSQL succeeds.

    Used by the readiness probe. Never raises: a failure is reported as ``False`` so
    the caller can translate it into a 503 response.
    """
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # readiness must never raise; report unhealthy instead
        logger.warning("Database readiness check failed: %s", exc)
        return False


async def check_migrations_applied() -> bool:
    """Return ``True`` if Alembic has stamped a schema version (migrations ran).

    Readiness distinguishes "database reachable" from "schema migrated"; a reachable but
    un-migrated database is not ready to serve. Never raises.
    """
    try:
        engine = get_engine()
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT version_num FROM alembic_version LIMIT 1")
            )
            return result.scalar_one_or_none() is not None
    except Exception as exc:  # missing table / unreachable → not ready
        logger.warning("Migration readiness check failed: %s", exc)
        return False


async def dispose_engine() -> None:
    """Dispose the engine and reset module state (called on app shutdown)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        logger.info("Database engine disposed")
    _engine = None
    _sessionmaker = None
