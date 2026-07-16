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
DEV_ONLY_JWT_SECRET = "dev-only-insecure-change-me"  # noqa: S105 - labelled dev value


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

    # --- Auth (used from a later stage; placeholders only for now) -----------
    jwt_secret: str = DEV_ONLY_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # --- Model provider (future stages) --------------------------------------
    llm_provider: Literal["mock", "ollama", "hosted"] = "mock"
    ollama_base_url: str = "http://localhost:11434"
    hosted_provider_api_key: str | None = None

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

    @field_validator("log_level")
    @classmethod
    def _normalise_log_level(cls, value: str) -> str:
        return value.upper()

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
