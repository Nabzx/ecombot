# AgentOps backend image.
# Development-oriented: dependencies are baked into an image-level virtualenv that
# lives OUTSIDE the app directory, so a bind-mounted ./backend does not shadow it and
# hot reload works. Runtime deps + dev tooling are both installed so tests and linters
# can run inside the container.

FROM python:3.12-slim AS base

# Pinned uv binary from the official image (reproducible, no network installer).
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/opt/venv/bin:${PATH}"

WORKDIR /app

# Install dependencies first for better layer caching. Only the lock + manifest are
# needed; project code is not installed (package = false).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project

# Application source (overlaid by a bind mount in development).
COPY . .

EXPOSE 8000

# Standalone default. Docker Compose overrides this to run migrations first and to
# enable --reload for development.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
