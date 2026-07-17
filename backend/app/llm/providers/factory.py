"""Construct the configured set of providers from typed settings.

The mock provider is always built. Ollama and hosted are built only when enabled; they
are constructed lazily and fail clearly at call time if their dependency/credentials
are missing (they are never probed here so importing this module stays offline).
"""

from __future__ import annotations

from app.core.config import Settings
from app.llm.providers.base import ModelProvider
from app.llm.providers.hosted import HostedProvider
from app.llm.providers.mock import MockProvider
from app.llm.providers.ollama import OllamaProvider


def build_providers(settings: Settings) -> dict[str, ModelProvider]:
    """Return provider-name → provider for every configured provider."""
    providers: dict[str, ModelProvider] = {
        "mock": MockProvider(model=settings.llm_default_model)
        if settings.llm_default_provider == "mock"
        else MockProvider(),
    }
    if settings.llm_ollama_enabled:
        providers["ollama"] = OllamaProvider(
            base_url=settings.ollama_base_url,
            model=settings.ollama_model,
            enabled=True,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    if settings.llm_hosted_enabled:
        providers["hosted"] = HostedProvider(
            base_url=settings.hosted_provider_base_url,
            model=settings.hosted_provider_model,
            api_key=settings.hosted_provider_api_key,
            enabled=True,
            price_table_version=settings.llm_cost_table_version,
            timeout_seconds=settings.llm_request_timeout_seconds,
        )
    return providers
