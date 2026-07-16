# AgentOps developer task interface.
# `make` is a convenience only — every target maps to a documented plain command
# (see README "Available commands"). Lifecycle targets use Docker Compose; quality
# targets run locally via `uv` (backend) and `npm` (frontend).

COMPOSE ?= docker compose
BACKEND_DIR := backend
FRONTEND_DIR := frontend

.DEFAULT_GOAL := help
.PHONY: help up down build logs backend-shell frontend-shell migrate migration \
        seed reseed seed-stats verify-data \
        list-rules list-tools demo-tool demo-rules \
        index-policies reindex-policies policy-index-stats verify-policy-index \
        search-policies eval-retrieval \
        test test-backend test-frontend lint lint-backend lint-frontend \
        typecheck typecheck-backend typecheck-frontend format check

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

# --- Stack lifecycle (Docker Compose) ----------------------------------------
up: ## Start the full stack (build if needed)
	$(COMPOSE) up --build

down: ## Stop the stack and remove containers (keeps the db volume)
	$(COMPOSE) down

build: ## Build all images
	$(COMPOSE) build

logs: ## Follow logs for all services
	$(COMPOSE) logs -f

backend-shell: ## Open a shell in the running backend container
	$(COMPOSE) exec backend sh

frontend-shell: ## Open a shell in the running frontend container
	$(COMPOSE) exec frontend sh

# --- Database migrations -----------------------------------------------------
migrate: ## Apply migrations inside the backend container
	$(COMPOSE) exec backend alembic upgrade head

migration: ## Create a revision: make migration m="message"
	$(COMPOSE) exec backend alembic revision -m "$(m)"

# --- Synthetic data ----------------------------------------------------------
seed: ## Seed an empty database with synthetic data
	$(COMPOSE) exec backend python -m app.seeds.cli seed

reseed: ## DEV ONLY: reset and reseed the database
	$(COMPOSE) exec backend python -m app.seeds.cli reseed --yes

seed-stats: ## Show seeded dataset statistics
	$(COMPOSE) exec backend python -m app.seeds.cli stats

verify-data: ## Verify data integrity (non-zero exit on failure)
	$(COMPOSE) exec backend python -m app.seeds.cli verify

# --- Deterministic rules & tools (S2) ----------------------------------------
list-rules: ## List deterministic business rules
	$(COMPOSE) exec backend python -m app.rules.cli list-rules

list-tools: ## List registered tools
	$(COMPOSE) exec backend python -m app.tools.cli list-tools

demo-tool: ## Print a tool's input schema: make demo-tool TOOL=get_order
	$(COMPOSE) exec backend python -m app.tools.cli schema $(TOOL)

index-policies: ## Index policy documents (idempotent)
	$(COMPOSE) exec backend python -m app.retrieval.cli index

reindex-policies: ## Force a full policy reindex
	$(COMPOSE) exec backend python -m app.retrieval.cli reindex --yes

policy-index-stats: ## Show policy index statistics
	$(COMPOSE) exec backend python -m app.retrieval.cli stats

verify-policy-index: ## Verify the policy index (non-zero on failure)
	$(COMPOSE) exec backend python -m app.retrieval.cli verify

search-policies: ## Search policies: make search-policies QUERY="..."
	$(COMPOSE) exec backend python -m app.retrieval.cli search "$(QUERY)"

eval-retrieval: ## Run the retrieval evaluation (enforces hard gates)
	$(COMPOSE) exec backend python -m app.retrieval.cli eval

demo-rules: ## Run the deterministic layer over the named demo fixtures
	@for fx in DEMO-TRACKING-001 DEMO-REFUND-APPROVAL-001 DEMO-RETURN-DAY-30 \
	           DEMO-RETURN-DAY-31 DEMO-PROMPT-INJECTION-001 DEMO-CROSS-CUSTOMER-001; do \
		echo "=== $$fx ==="; \
		$(COMPOSE) exec -T backend python -m app.tools.cli run-demo $$fx; \
		echo; \
	done

# --- Quality (run locally) ---------------------------------------------------
test-backend: ## Run backend tests
	cd $(BACKEND_DIR) && uv run pytest

test-frontend: ## Run frontend tests
	cd $(FRONTEND_DIR) && npm run test

test: test-backend test-frontend ## Run all tests

lint-backend: ## Ruff format check + lint (backend)
	cd $(BACKEND_DIR) && uv run ruff format --check . && uv run ruff check .

lint-frontend: ## ESLint (frontend)
	cd $(FRONTEND_DIR) && npm run lint

lint: lint-backend lint-frontend ## Lint everything

typecheck-backend: ## MyPy (backend)
	cd $(BACKEND_DIR) && uv run mypy .

typecheck-frontend: ## tsc --noEmit (frontend)
	cd $(FRONTEND_DIR) && npm run typecheck

typecheck: typecheck-backend typecheck-frontend ## Type-check everything

format: ## Auto-format backend (Ruff) and fix backend lint
	cd $(BACKEND_DIR) && uv run ruff format . && uv run ruff check --fix .

check: lint typecheck test ## Run lint + typecheck + tests (everything CI runs)
