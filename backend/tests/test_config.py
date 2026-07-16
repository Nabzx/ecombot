"""Tests for typed settings loading and validation."""

from __future__ import annotations

import pytest
from app.core.config import DEV_ONLY_JWT_SECRET, Settings


def test_defaults_are_development_safe() -> None:
    settings = Settings()
    assert settings.app_name == "AgentOps API"
    assert settings.environment == "development"
    assert settings.jwt_algorithm == "HS256"
    assert settings.database_url_str.startswith("postgresql+asyncpg://")


def test_cors_origins_accept_comma_separated_string() -> None:
    settings = Settings(
        backend_cors_origins="http://localhost:3000, http://localhost:8000"
    )
    assert settings.backend_cors_origins == [
        "http://localhost:3000",
        "http://localhost:8000",
    ]


def test_cors_origins_parsed_from_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    # Exercises the real environment source (a comma-separated value must not be
    # JSON-decoded); this is the path Docker Compose uses.
    monkeypatch.setenv("BACKEND_CORS_ORIGINS", "http://a.example, http://b.example")
    settings = Settings()
    assert settings.backend_cors_origins == ["http://a.example", "http://b.example"]


def test_log_level_is_normalised() -> None:
    settings = Settings(log_level="debug")
    assert settings.log_level == "DEBUG"


def test_production_rejects_dev_secret() -> None:
    with pytest.raises(ValueError, match="JWT_SECRET"):
        Settings(environment="production", jwt_secret=DEV_ONLY_JWT_SECRET)


def test_production_accepts_real_secret() -> None:
    settings = Settings(environment="production", jwt_secret="a-real-secret-value")
    assert settings.environment == "production"
