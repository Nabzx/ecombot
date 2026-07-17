# AgentOps — AI Customer Support Operations Platform

AgentOps is an internal **support-operations platform** (not a customer-facing
chatbot) for a fictional e-commerce retailer, built on fully synthetic data. Each
support ticket flows through an explicit, auditable AI workflow that classifies the
request, resolves the customer and order, retrieves the relevant policy, applies
**deterministic business rules**, drafts a grounded response, and — for any
consequential action — stops at a **human approval gate** before a durable worker
executes it exactly once. Every run is traced, costed, audited and scored against a
golden evaluation set.

> **Current stage: S4 — Provider Abstraction, Structured Outputs & Prompt System.** On top
> of S0–S3, this stage adds a **provider-independent, measurable, safely constrained model
> layer**: a deterministic mock provider (default; optional Ollama and hosted adapters),
> versioned prompts with deterministic hashes, strictly-validated structured outputs with one
> bounded repair, and five independently callable model tasks (classify, extract identifiers,
> plan read-only tools, summarise evidence, draft a grounded response) plus a decision
> summary. **Every model output is a proposal** — deterministic rules, permissions and later
> human approval remain authoritative. No workflow engine, approvals, outbox or execution
> exist yet (that is S5+).

## Model layer (S4)

A provider-neutral model layer that can classify tickets, extract candidate identifiers,
propose read-only tool calls, summarise evidence and draft grounded responses — all as
proposals validated against strict schemas and safety rules.

- **Providers** (`app/llm/providers/`): a typed async `ModelProvider` Protocol with a
  deterministic **mock** default (no network, no secrets, CI/tests/eval), plus optional
  **Ollama** and generic **hosted** OpenAI-compatible adapters that fail clearly when
  unavailable. Capability-driven, with recorded routing/fallback and bounded retries. See
  [docs/model-providers.md](docs/model-providers.md).
- **Prompts** (`app/prompts/`): versioned YAML templates with deterministic hashes,
  immutability, safe `{{ var }}` rendering (no code execution) and trusted/untrusted data
  boundaries. See [docs/prompt-system.md](docs/prompt-system.md).
- **Tasks** (`app/llm/`): five tasks + decision summary + repair, each with strict Pydantic
  I/O and semantic safety validation (tool allowlist, citations ⊆ supplied, action ∈ allowed
  list, no false execution claim). Proposed actions contain **no** execute/approve values.
  See [docs/model-tasks.md](docs/model-tasks.md).
- **Persistence**: `prompt_versions` and `model_calls` tables record what was used with
  redaction applied (email/phone/card/JWT/keys/DB-URLs), integer GBP-microunit cost, token
  provenance and fallback/repair metadata. No secrets, PII or hidden reasoning are stored.
- **Evaluation** (80 cases, 6 hard gates): all safety gates pass at 0 unsafe outcomes with
  the deterministic mock. See [docs/model-evaluation.md](docs/model-evaluation.md).

```bash
make list-providers
make list-model-tasks
make list-prompts
make model-demo FIXTURE=DEMO-REFUND-APPROVAL-001
make model-demo FIXTURE=DEMO-PROMPT-INJECTION-001
make eval-model-layer
```

## Policy retrieval (S3)

Runs fully offline (no LLM, no network). Policy documents become searchable evidence; a
later AI may use the citations but never decides which policy version is active, whether
conflicting policies combine, or whether an unsupported answer is invented.

- **Ingestion** (`app/retrieval/ingestion.py`): deterministic, idempotent chunking +
  embedding into `policy_chunks` (tsvector + `vector(256)`); unchanged sources are
  skipped, never silently rebuilt. See [docs/policy-indexing.md](docs/policy-indexing.md).
- **Hybrid retrieval** (`app/retrieval/service.py`): lexical (`ts_rank_cd`) + semantic
  (pgvector cosine) fused by RRF, with active-version/date/source filtering, conflict
  detection (via the S2 policy-validity rule), support checks and stable citations like
  `POL-RETURNS:v2:returns-policy:chunk-00`. See
  [docs/policy-retrieval.md](docs/policy-retrieval.md).
- **Embeddings**: default `deterministic_hash` (dim 256, reproducible, CI-friendly);
  optional local Sentence Transformers / Ollama (never required).
- **Trust boundary**: normal retrieval searches only active official policy; hostile and
  test fixtures are isolated and can never become authoritative evidence.
- **`search_policies` tool**: model-facing, `policy_read`, read-only, with restricted
  output (no source override, no raw vectors, no full documents).
- **Evaluation** (65 cases, 3 hard gates): see
  [docs/retrieval-evaluation.md](docs/retrieval-evaluation.md). With the deterministic
  embedding, lexical is the strongest channel (R@1 0.90); hard gates (active-version,
  conflict detection, hostile-source exclusion) all pass at 1.00.

```bash
make index-policies
make verify-policy-index
make search-policies QUERY="Can I return an opened item after 30 days?"
make eval-retrieval
```

## Deterministic rules & tools (S2)

The rules engine runs with no model, network or external API. A later AI stage may
understand language and *propose* an action, but the deterministic layer is the final
authority for ownership, eligibility, limits, risk and escalation, and nothing is
executed in S2 (`execution_permitted` is always `false`).

- **Rules** (`backend/app/rules/`): clock abstraction, typed `RuleResult` with stable
  reason codes, ownership, returns, refunds, cancellations, deliveries, remedies, policy
  validity, routing and idempotency. See [docs/business-rules.md](docs/business-rules.md).
- **Tools** (`backend/app/tools/`): a typed registry + executor with permissions, PII-safe
  results and JSON schemas — read-only lookups (`get_order`, `search_customer`, …) and
  deterministic rule tools (`check_refund_eligibility`, `calculate_risk_and_route`, …).
  Write/execute tools are **reserved names without handlers**. See
  [docs/tool-system.md](docs/tool-system.md).

Key thresholds: 30-day inclusive return window; refunds `<=£50` Medium, `£50.01–£250`
High, `>£250` Blocked; delivery delay tiers 1–3 / 4–9 / `>=10` days; confidence `>=0.75`
continue, `0.50–0.74` agent, `<0.50` escalate. All refunds and cancellations require
Supervisor approval.

Inspect it against the seeded fixtures:

```bash
make list-rules
make list-tools
make demo-tool TOOL=get_order          # print a tool's JSON schema
make demo-rules                        # run the deterministic layer over demo fixtures
```

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

## Domain & synthetic data (S1)

The platform models the support operations of **Meridian & Co.**, a fictional UK
homeware retailer. Core entities: **Customer → Order → OrderItem** (with **Product**),
**Order → Shipment**, **Customer/Order → Ticket → TicketMessage**, and **Policy →
PolicyVersion**. Money is stored as integer pennies; primary keys are UUIDs; enums are
native PostgreSQL types; important invariants are enforced by database constraints. See
[docs/domain-model.md](docs/domain-model.md) for the full model and ER diagram.

The deterministic seed (fixed seed, UK Faker, no external calls) produces roughly:

| Users | Products | Customers | Orders | Tickets (adversarial) | Policies/versions |
| ----- | -------- | --------- | ------ | --------------------- | ----------------- |
| 4     | 42       | 55        | 161    | 85 (13)               | 10 / 12           |

All ten ticket categories, every shipment status, and return-window boundary cases are
represented. Named demo fixtures (e.g. `DEMO-REFUND-APPROVAL-001`, `DEMO-RETURN-DAY-30`,
`DEMO-PROMPT-INJECTION-001`, `DEMO-CROSS-CUSTOMER-001`) are tagged on tickets and listed
in [data/synthetic/demo_cases.json](data/synthetic/demo_cases.json). See
[docs/synthetic-data.md](docs/synthetic-data.md).

**Data privacy:** every record is synthetic. There are no real customers, no real
payment details, and no real company systems; names/emails/phone numbers are fabricated
(`@example.com`, `07…`). Adversarial ticket content is stored verbatim for later
security evaluation and is never executed.

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

The backend applies database migrations automatically on startup. Populate the
synthetic dataset with:

```bash
make seed          # or: docker compose exec backend python -m app.seeds.cli seed
make seed-stats    # show dataset statistics
make verify-data   # run integrity checks (non-zero exit on failure)
```

Stop with `docker compose down` (the database volume is preserved). The database is
published on host port **5433** (to avoid clashing with any local Postgres on 5432).

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
`docker compose up db` and setting `DATABASE_URL=...@localhost:5433/agentops`
(the dev database is published on host port 5433).

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
| Seed data           | `make seed`        | `docker compose exec backend python -m app.seeds.cli seed`     |
| Reset + reseed      | `make reseed`      | `... python -m app.seeds.cli reseed --yes` (DEV ONLY)          |
| Seed statistics     | `make seed-stats`  | `... python -m app.seeds.cli stats`                            |
| Verify data         | `make verify-data` | `... python -m app.seeds.cli verify`                           |
| List rules          | `make list-rules`  | `... python -m app.rules.cli list-rules`                       |
| List tools          | `make list-tools`  | `... python -m app.tools.cli list-tools`                       |
| Tool schema         | `make demo-tool TOOL=get_order` | `... python -m app.tools.cli schema get_order`    |
| Run demo fixtures   | `make demo-rules`  | `... python -m app.tools.cli run-demo DEMO-RETURN-DAY-30`      |
| Index policies      | `make index-policies` | `... python -m app.retrieval.cli index`                    |
| Verify policy index | `make verify-policy-index` | `... python -m app.retrieval.cli verify`              |
| Search policies     | `make search-policies QUERY="..."` | `... python -m app.retrieval.cli search "..."`|
| Retrieval eval      | `make eval-retrieval` | `... python -m app.retrieval.cli eval` (hard gates)        |
| List providers      | `make list-providers` | `... python -m app.llm.cli list-providers`                 |
| List model tasks    | `make list-model-tasks` | `... python -m app.llm.cli list-tasks`                   |
| List prompts        | `make list-prompts` | `... python -m app.llm.cli list-prompts`                     |
| Classify a ticket   | `make classify-ticket TICKET=TKT-2026-000001` | `... python -m app.llm.cli classify-ticket ...` |
| Model demo          | `make model-demo FIXTURE=DEMO-REFUND-APPROVAL-001` | `... python -m app.llm.cli run-demo ...`   |
| Model-call stats    | `make model-stats` | `... python -m app.llm.cli stats`                            |
| Model-layer eval    | `make eval-model-layer` | `... python -m app.llm.evaluation` (6 hard gates)        |
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

Backend DB-backed tests use a **disposable PostgreSQL test database** (never SQLite).
Start the stack first (`docker compose up -d db`) so they can reach Postgres on host
port 5433; override with `TEST_DATABASE_URL` if needed. Tests are isolated per-test via
transaction rollback.

CI (`.github/workflows/ci.yml`) runs, on every push and PR: backend lint + type-check,
the frontend checks, and a backend-tests job that spins up PostgreSQL + pgvector,
applies migrations, seeds the synthetic data, runs the integrity check, and runs the
full pytest suite. Nothing in CI requires paid APIs.

## Current limitations (S4)

- The model layer proposes; it does **not** decide eligibility, approve, execute, update
  tickets or orchestrate a workflow. There is **no workflow engine, approvals, outbox or
  execution** yet, and the frontend is still the S0 status page.
- The default provider is a deterministic **mock**, not a language model: it exercises the
  system (parsing, validation, safety, persistence) rather than demonstrating language
  quality. Ollama/hosted are optional and never required for tests or CI.
- Workflow / approval / outbox / audit **tables are deferred** to the stages that first use
  them; `model_calls.workflow_run_id` is a nullable, FK-less column reserved for S5.
- `JWT_SECRET` in `.env.example` is a labelled development placeholder; authentication
  is not wired up yet.
- The synthetic dataset is anchored to a fixed reference date (2026-07-16), so
  "days since delivery" are relative to that date rather than today.
- Two moderate `npm audit` advisories remain in a `postcss` copy bundled **inside**
  Next.js; they cannot be resolved without downgrading Next and do not affect this
  build.

## Roadmap

S0 Foundations → S1 Domain & Synthetic Data → S2 Deterministic Tools & Business Rules →
S3 Policy Retrieval & Evidence Grounding → **S4 Provider Abstraction & Prompt System (this
stage)** → S5 Workflow state machine & checkpointing → S6 Human-in-the-loop & outbox → S7
Observability & audit → S8 Evaluation → S9 Dashboard → S10 Hardening.

**Next up: S5 — Workflow State Machine & Checkpointing.**
