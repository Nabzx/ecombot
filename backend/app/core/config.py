"""Typed application settings loaded from the environment and an optional .env file.

Settings are intentionally centralised here so no module reaches for ``os.environ``
directly. Later stages (auth, providers, telemetry) extend this object rather than
introducing their own configuration surface.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, PostgresDsn, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["development", "test", "production"]

# Explicitly labelled development-only secret. It is safe to commit because it is
# obviously not a real secret; the production validator below refuses to boot with it.
# >= 32 bytes so HS256 does not warn; still obviously a non-production value.
DEV_ONLY_JWT_SECRET = "dev-only-insecure-change-me-0123456789ab"  # noqa: S105


class Settings(BaseSettings):
    """Application configuration.

    Values come from environment variables (see ``.env.example``). Names are
    case-insensitive. Unknown variables in the environment are ignored so the same
    ``.env`` can be shared with the frontend and Docker Compose.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application ---------------------------------------------------------
    app_name: str = "AgentOps API"
    environment: Environment = "development"
    debug: bool = False
    log_level: str = "INFO"

    # --- HTTP server ---------------------------------------------------------
    api_host: str = "0.0.0.0"  # noqa: S104 - bind all interfaces inside the container
    api_port: int = 8000
    # Prefix reserved for versioned business routers added in later stages.
    # Health endpoints are deliberately mounted at the root.
    api_prefix: str = "/api"

    # --- CORS ----------------------------------------------------------------
    # NoDecode disables pydantic-settings' JSON decoding of this list field so the
    # validator below can accept a plain comma-separated environment value.
    backend_cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # --- Database ------------------------------------------------------------
    # Async driver DSN. Defaults to the local Docker Compose service so the app
    # boots out of the box for development; production must supply its own.
    database_url: PostgresDsn = Field(
        default=PostgresDsn(
            "postgresql+asyncpg://agentops:agentops@localhost:5432/agentops"
        )
    )
    # SQL statement echo is off by default even in debug; it is very noisy for CLI
    # tooling. Enable explicitly when diagnosing queries.
    db_echo: bool = False

    # --- Auth / JWT (S6) -----------------------------------------------------
    jwt_secret: str = DEV_ONLY_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_minutes: int = 720

    # --- Approvals / outbox worker (S6) --------------------------------------
    approval_expiry_hours: int = 24
    manual_retry_max: int = 3

    worker_enabled: bool = True
    worker_id: str = "outbox-worker-1"
    worker_poll_interval_seconds: float = 1.0
    worker_batch_size: int = 5
    outbox_lease_seconds: int = 60
    outbox_max_attempts: int = 5
    outbox_retry_base_seconds: float = 2.0
    worker_shutdown_timeout_seconds: float = 10.0
    # Deterministic failure injection for tests/eval only (empty = disabled).
    outbox_failure_injection: str = ""

    # --- Model layer / providers (S4) ----------------------------------------
    # The deterministic mock provider is the default everywhere: it needs no network,
    # no model download and no secrets, and it is the only provider CI relies on.
    llm_default_provider: Literal["mock", "ollama", "hosted"] = "mock"
    llm_default_model: str = "mock-deterministic-v1"
    # Fallback order tried on retryable provider failures. Comma-separated in the env.
    # Mock is always appended as the final safety net so the system never hard-fails.
    llm_fallback_order: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["mock"]
    )
    llm_fallback_enabled: bool = True

    # Mock provider is always available; the others are opt-in and fail clearly if
    # selected without their dependency/credentials.
    llm_mock_enabled: bool = True
    llm_ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1"
    llm_hosted_enabled: bool = False
    hosted_provider_base_url: str | None = None
    hosted_provider_model: str = "gpt-4o-mini"
    hosted_provider_api_key: str | None = None

    # Timeouts / retries / bounds (all enforced across retries and fallback).
    llm_request_timeout_seconds: float = 30.0
    llm_total_deadline_seconds: float = 90.0
    llm_max_retries: int = 2
    llm_default_temperature: float = 0.0
    llm_max_input_chars: int = 24_000
    llm_max_output_tokens: int = 1_024

    # Persistence / safety toggles. Redaction cannot be disabled outside a non-prod env.
    llm_prompt_persistence_enabled: bool = True
    llm_raw_output_persistence_enabled: bool = False
    llm_pii_redaction_enabled: bool = True
    llm_cost_table_version: str = "price-table-2026-07"

    # --- Policy retrieval / embeddings (S3) -----------------------------------
    # deterministic_hash requires no model/network (default; used in CI). The optional
    # local providers must be explicitly selected and available.
    embedding_provider: Literal[
        "deterministic_hash", "sentence_transformers", "ollama"
    ] = "deterministic_hash"
    sentence_transformer_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    ollama_embedding_model: str = "nomic-embed-text"

    # --- Telemetry (future stage) --------------------------------------------
    otel_enabled: bool = False
    otel_exporter_endpoint: str | None = None

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def _split_cors_origins(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list for CORS origins."""
        if isinstance(value, str) and not value.startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @field_validator("llm_fallback_order", mode="before")
    @classmethod
    def _split_fallback_order(cls, value: object) -> object:
        """Accept a comma-separated string as well as a JSON list for fallback order."""
        if isinstance(value, str) and not value.startswith("["):
            return [name.strip() for name in value.split(",") if name.strip()]
        return value

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _validate_provider_config(self) -> Settings:
        """Fail clearly on invalid provider names or an unusable configuration."""
        valid = {"mock", "ollama", "hosted"}
        unknown = [name for name in self.llm_fallback_order if name not in valid]
        if unknown:
            raise ValueError(
                f"LLM_FALLBACK_ORDER contains unknown providers: {unknown}. "
                f"Valid providers: {sorted(valid)}"
            )
        if self.llm_default_provider not in valid:
            raise ValueError(
                f"LLM_DEFAULT_PROVIDER {self.llm_default_provider!r} is not valid"
            )
        if not self.llm_mock_enabled:
            raise ValueError(
                "LLM_MOCK_ENABLED must be true: the deterministic mock provider is the "
                "required safety net for CI, tests and fallback."
            )
        if self.llm_max_retries < 0:
            raise ValueError("LLM_MAX_RETRIES must be >= 0")
        if not 0.0 <= self.llm_default_temperature <= 2.0:
            raise ValueError("LLM_DEFAULT_TEMPERATURE must be within [0, 2]")
        return self

    @model_validator(mode="after")
    def _guard_production_secrets(self) -> Settings:
        """Refuse to boot in production with development-only secrets."""
        if self.environment == "production" and self.jwt_secret == DEV_ONLY_JWT_SECRET:
            raise ValueError(
                "JWT_SECRET must be set to a real value when ENVIRONMENT=production"
            )
        return self

    @property
    def database_url_str(self) -> str:
        """The database URL as a plain string for SQLAlchemy/Alembic."""
        return str(self.database_url)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance.

    Cached so the environment is parsed once. Tests that need a different
    configuration clear the cache via ``get_settings.cache_clear()``.
    """
    return Settings()
