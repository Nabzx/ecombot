"""Shared pytest fixtures.

Tests run fully offline (no external network, no paid APIs). Unit tests use no
database. DB-backed tests use a **disposable PostgreSQL test database** (never SQLite,
because the schema relies on PostgreSQL-specific behaviour). Point them at a database
with ``TEST_DATABASE_URL``; the default targets the Dockerised dev database published
on host port 5433. Each ``db_session`` runs inside a transaction that is rolled back
after the test (commits become savepoints), so tests are isolated and order-independent.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator

import app.models  # noqa: F401  (register all models on Base.metadata)
import pytest
from app.api.routes.health import database_ready
from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://agentops:agentops@localhost:5433/agentops_test",
)


@pytest.fixture
def test_settings() -> Settings:
    """Settings for a test environment with deterministic values."""
    return Settings(
        environment="test",
        debug=True,
        jwt_secret="test-secret",
        backend_cors_origins=["http://localhost:3000"],
    )


@pytest.fixture
async def client_db_ok(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client against an app whose database is reachable."""
    app = create_app(test_settings)

    async def _ready() -> bool:
        return True

    app.dependency_overrides[database_ready] = _ready
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


@pytest.fixture
async def client_db_down(test_settings: Settings) -> AsyncIterator[AsyncClient]:
    """HTTP client against an app whose database is unreachable."""
    app = create_app(test_settings)

    async def _not_ready() -> bool:
        return False

    app.dependency_overrides[database_ready] = _not_ready
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


# --- Database fixtures -------------------------------------------------------


async def _ensure_database() -> None:
    """Create the test database if it does not already exist."""
    target = make_url(TEST_DATABASE_URL)
    admin_url = target.set(database="agentops")
    # Pass the URL object (str(url) masks the password as '***').
    engine = create_async_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            exists = await conn.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            )
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        await engine.dispose()


async def _create_schema() -> None:
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _prepare_test_database() -> Iterator[None]:
    """Once per session: ensure the test database exists and (re)create the schema.

    Synchronous fixture running async setup in its own loop, which sidesteps
    pytest-asyncio event-loop scoping between session and function scopes.
    """
    asyncio.run(_ensure_database())
    asyncio.run(_create_schema())
    yield


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """A transactional session; everything is rolled back after the test."""
    engine = create_async_engine(TEST_DATABASE_URL)
    connection = await engine.connect()
    transaction = await connection.begin()
    maker = async_sessionmaker(
        bind=connection,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
