# AgentOps Backend (`agentops-api`)

FastAPI + async SQLAlchemy backend for AgentOps. **Stage S0 — Foundations**: only the
runnable skeleton and health endpoints exist. Domain models, tools, workflows,
retrieval, approvals, evaluations and business rules arrive in later stages.

## Stack

- Python 3.12, FastAPI, Pydantic v2 + pydantic-settings
- SQLAlchemy 2.x (async) with asyncpg, Alembic migrations
- Tooling: `uv`, Ruff, MyPy (near-strict), Pytest + pytest-asyncio, HTTPX

## Layout

```
app/
  api/          HTTP layer: central router + route modules (health)
  core/         config (typed settings) and logging
  db/           declarative base, async engine/session, readiness check
  schemas/      Pydantic request/response models
  models/ services/ rules/ tools/ retrieval/ providers/
  workflows/ approvals/ outbox/ tracing/ audit/ evaluations/
                empty stage packages (see each README) — no logic yet
  main.py       application factory + lifespan
alembic/        async migration environment + versions
tests/          offline tests (no DB, no network, no paid APIs)
```

## Local development (without Docker)

```bash
uv sync                       # create .venv and install deps + dev tools
cp ../.env.example ../.env    # then edit DATABASE_URL if needed
uv run uvicorn app.main:app --reload
```

Requires a reachable PostgreSQL for `/health/ready` to report `ready` and for
migrations. With Docker, prefer the root `docker compose up`.

## Quality commands

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy .
uv run pytest
```

## Migrations

```bash
uv run alembic upgrade head          # apply migrations
uv run alembic revision -m "message" # create a new revision (autogenerate added in S1)
```

The database URL is read from application settings (`app.core.config`), never from
`alembic.ini`.

## Health endpoints

| Endpoint         | Purpose                                             |
| ---------------- | --------------------------------------------------- |
| `GET /health`      | Combined status (`{status, service, version}`)      |
| `GET /health/live` | Process liveness only                               |
| `GET /health/ready`| Dependency readiness; `503` when PostgreSQL is down |
