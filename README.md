# AgentOps — AI Customer Support Operations Platform

AgentOps is an internal **support-operations platform** (not a customer-facing
chatbot) for a fictional e-commerce retailer, built on fully synthetic data. Each
support ticket flows through an explicit, auditable AI workflow that classifies the
request, resolves the customer and order, retrieves the relevant policy, applies
**deterministic business rules**, drafts a grounded response, and — for any
consequential action — stops at a **human approval gate** before a durable worker
executes it exactly once. Every run is traced, costed, audited and scored against a
golden evaluation set.

> **Current stage: S0 — Foundations.** This repository currently contains only the
> runnable skeleton: a FastAPI backend with health endpoints, a Next.js status page, a
> PostgreSQL + pgvector database, migrations, quality tooling and CI. **No domain
> models, AI workflows, tools, retrieval, approvals, evaluations or business rules are
> implemented yet.** They arrive in later stages (see the roadmap below).

## Why this project exists

AgentOps is a portfolio project targeting **Applied AI Engineer**, **Forward Deployed
AI Engineer**, **AI Automation Engineer**, **AI Product Engineer** and **AI Solutions
Engineer** roles. The goal is to demonstrate the ability to turn an ambiguous business
process into a secure, evaluated and reliable production AI workflow — with equal
attention to the AI components and the software system around them.

## Architecture (S0)

```
Next.js frontend  (http://localhost:3000)
        |
        v   HTTP (typed API client, CORS)
FastAPI backend   (http://localhost:8000, docs at /docs)
        |
        v   async SQLAlchemy + Alembic
PostgreSQL + pgvector
```

## Technology stack

| Area       | Choice                                                              |
| ---------- | ------------------------------------------------------------------- |
| Backend    | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 (async), Alembic    |
| Database   | PostgreSQL 16 + pgvector                                            |
| Frontend   | Next.js 15 (App Router), React 19, TypeScript (strict), Tailwind    |
| Tooling    | uv, Ruff, MyPy, Pytest; ESLint, Vitest                             |
| Local run  | Docker Compose                                                     |
| CI         | GitHub Actions (backend, frontend, integration)                    |

## Prerequisites

- Docker + Docker Compose (the only requirement to run the stack)
- For non-Docker development: Python 3.12 with [`uv`](https://docs.astral.sh/uv/) and
  Node.js 20+

## Quick start (Docker)

```bash
cp .env.example .env
docker compose up --build
```

Then open:

- Frontend: <http://localhost:3000>
- Backend API: <http://localhost:8000>
- API docs (Swagger): <http://localhost:8000/docs>

The backend applies database migrations automatically on startup. Stop with
`docker compose down` (the database volume is preserved).

## Non-Docker development

Backend:

```bash
cd backend
uv sync
uv run uvicorn app.main:app --reload   # needs a reachable PostgreSQL
```

Frontend:

```bash
cd frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > .env.local
npm run dev
```

You can point the local backend at the Dockerised database by running only
`docker compose up db` and setting `DATABASE_URL=...@localhost:5432/agentops`.

## Environment configuration

All configuration lives in a root `.env` (copied from `.env.example`). It is
git-ignored and contains only safe local defaults — no real secrets. Key variables:
`DATABASE_URL`, `BACKEND_CORS_ORIGINS`, `JWT_SECRET` (dev placeholder),
`NEXT_PUBLIC_API_BASE_URL`. See `.env.example` for the full list.

## Available commands

Via `make` (see `make help`) or the underlying commands directly:

| Task                | `make`             | Underlying command                                             |
| ------------------- | ------------------ | -------------------------------------------------------------- |
| Start stack         | `make up`          | `docker compose up --build`                                    |
| Stop stack          | `make down`        | `docker compose down`                                          |
| Build images        | `make build`       | `docker compose build`                                         |
| Follow logs         | `make logs`        | `docker compose logs -f`                                       |
| Backend shell       | `make backend-shell`  | `docker compose exec backend sh`                            |
| Frontend shell      | `make frontend-shell` | `docker compose exec frontend sh`                          |
| Apply migrations    | `make migrate`     | `docker compose exec backend alembic upgrade head`             |
| New migration       | `make migration m="msg"` | `... alembic revision -m "msg"`                          |
| Backend tests       | `make test-backend`  | `cd backend && uv run pytest`                                |
| Frontend tests      | `make test-frontend` | `cd frontend && npm run test`                                |
| Lint                | `make lint`        | `ruff format --check . && ruff check .` / `npm run lint`       |
| Type-check          | `make typecheck`   | `uv run mypy .` / `npm run typecheck`                          |
| Format (backend)    | `make format`      | `uv run ruff format . && uv run ruff check --fix .`            |
| Everything (CI set) | `make check`       | lint + typecheck + test                                        |

## Health endpoints

| Endpoint            | Purpose                                                    |
| ------------------- | ---------------------------------------------------------- |
| `GET /health`       | Combined status: `{status, service, version}`             |
| `GET /health/live`  | Liveness only (process is up)                              |
| `GET /health/ready` | Dependency readiness; returns **503** when PostgreSQL is down |

## Testing & quality

```bash
make check          # runs everything CI runs
# or individually:
cd backend  && uv run ruff check . && uv run mypy . && uv run pytest
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

CI (`.github/workflows/ci.yml`) runs the same checks on every push and pull request,
plus an integration job that applies migrations against a real PostgreSQL service and
verifies readiness and the pgvector extension. Nothing in CI requires paid APIs.

## Current limitations (S0)

- Only health endpoints exist; there is no domain data, no AI and no dashboard.
- The stage packages under `backend/app/` (models, tools, rules, workflows, …) are
  intentionally empty placeholders with per-package READMEs.
- `JWT_SECRET` in `.env.example` is a labelled development placeholder; authentication
  is not wired up yet.
- Two moderate `npm audit` advisories remain in a `postcss` copy bundled **inside**
  Next.js; they cannot be resolved without downgrading Next and do not affect this
  build.

## Roadmap

**S0 Foundations (this stage)** → S1 Domain & Synthetic Data → S2 Deterministic core &
rules → S3 RAG → S4 Provider abstraction → S5 Workflow state machine → S6 Human-in-the-
loop & outbox → S7 Observability & audit → S8 Evaluation → S9 Dashboard → S10 Hardening.

**Next up: S1 — Domain & Synthetic Data.**
