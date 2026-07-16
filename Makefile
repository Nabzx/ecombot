# AgentOps developer task interface.
# `make` is a convenience only — every target maps to a documented plain command
# (see README "Available commands"). Lifecycle targets use Docker Compose; quality
# targets run locally via `uv` (backend) and `npm` (frontend).

COMPOSE ?= docker compose
BACKEND_DIR := backend
FRONTEND_DIR := frontend

.DEFAULT_GOAL := help
.PHONY: help up down build logs backend-shell frontend-shell migrate migration \
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
