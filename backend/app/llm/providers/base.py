"""The provider-neutral async model interface.

Application code depends on this Protocol, never on a vendor SDK. A provider is a thin
adapter that turns a :class:`ModelRequest` into a :class:`ModelResponse`, declares its
capabilities, and raises :class:`ModelProviderError` (mapped to the typed taxonomy) on
failure rather than leaking raw exceptions.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.llm.models import ModelRequest, ModelResponse, ProviderCapabilities


@runtime_checkable
class ModelProvider(Protocol):
    """A provider-neutral, asynchronous model provider."""

    @property
    def provider_name(self) -> str:
        """Stable provider identifier, e.g. ``"mock"``, ``"ollama"``, ``"hosted"``."""
        ...

    @property
    def default_model(self) -> str:
        """The model used when a request does not name one."""
        ...

    @property
    def capabilities(self) -> ProviderCapabilities:
        """The capabilities this provider declares (drives service branching)."""
        ...

    def is_available(self) -> bool:
        """Whether the provider is configured and usable without a network probe."""
        ...

    async def generate(self, request: ModelRequest) -> ModelResponse:
        """Perform a single model call. Raises ``ModelProviderError`` on failure."""
        ...
