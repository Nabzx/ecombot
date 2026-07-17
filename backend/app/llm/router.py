"""Provider routing, bounded retries, timeout and recorded fallback.

Tries a provider order (requested → configured fallback → mock), retrying a single
provider only for transient failures and falling back to the next provider only for
retryable errors when fallback is allowed. Configuration, authentication, input and
safety failures never trigger fallback. Every fallback and attempt is recorded.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass

from app.llm.enums import ModelErrorCode, ProviderCapability
from app.llm.models import (
    ModelProviderError,
    ModelRequest,
    ModelResponse,
)
from app.llm.providers.base import ModelProvider


@dataclass(frozen=True)
class RoutedResult:
    """A provider response plus the routing decisions that produced it."""

    response: ModelResponse
    requested_provider: str
    actual_provider: str
    attempts: int
    fallback_from: str | None
    fallback_reason: str | None


class ProviderRouter:
    """Selects and invokes providers with retry, timeout and fallback."""

    def __init__(
        self,
        providers: dict[str, ModelProvider],
        *,
        fallback_order: list[str],
        max_attempts: int = 2,
        request_timeout: float = 30.0,
        total_deadline: float = 90.0,
        backoff_base: float = 0.05,
        fallback_enabled: bool = True,
    ) -> None:
        self._providers = providers
        self._fallback_order = fallback_order
        self._max_attempts = max_attempts
        self._request_timeout = request_timeout
        self._total_deadline = total_deadline
        self._backoff_base = backoff_base
        self._fallback_enabled = fallback_enabled

    def _candidate_order(
        self, requested_provider: str | None, fallback_allowed: bool
    ) -> list[str]:
        first = requested_provider or (
            self._fallback_order[0] if self._fallback_order else "mock"
        )
        order = [first]
        if fallback_allowed and self._fallback_enabled:
            for name in self._fallback_order:
                if name not in order:
                    order.append(name)
        # Mock is the guaranteed final safety net when fallback is permitted.
        if fallback_allowed and self._fallback_enabled and "mock" not in order:
            order.append("mock")
        return order

    async def route(
        self,
        request: ModelRequest,
        *,
        requested_provider: str | None = None,
        required_capabilities: frozenset[ProviderCapability] = frozenset(),
        fallback_allowed: bool = True,
    ) -> RoutedResult:
        loop = asyncio.get_event_loop()
        deadline = loop.time() + self._total_deadline
        requested = requested_provider or (
            self._fallback_order[0] if self._fallback_order else "mock"
        )
        order = self._candidate_order(requested_provider, fallback_allowed)

        total_attempts = 0
        fallback_from: str | None = None
        fallback_reason: str | None = None
        last_error: ModelProviderError | None = None

        for index, name in enumerate(order):
            provider = self._providers.get(name)
            if provider is None or not provider.is_available():
                last_error = ModelProviderError(
                    ModelErrorCode.PROVIDER_UNAVAILABLE,
                    f"provider {name!r} is not configured/available",
                    provider=name,
                )
                continue
            if required_capabilities and not required_capabilities.issubset(
                provider.capabilities.capabilities
            ):
                last_error = ModelProviderError(
                    ModelErrorCode.UNSUPPORTED_CAPABILITY,
                    f"provider {name!r} lacks required capabilities",
                    provider=name,
                    retryable=False,
                )
                continue

            for attempt in range(1, self._max_attempts + 1):
                total_attempts += 1
                if loop.time() > deadline:
                    raise ModelProviderError(
                        ModelErrorCode.PROVIDER_TIMEOUT,
                        "total task deadline exceeded",
                        provider=name,
                    )
                try:
                    response = await asyncio.wait_for(
                        provider.generate(request), timeout=self._request_timeout
                    )
                except TimeoutError:
                    last_error = ModelProviderError(
                        ModelErrorCode.PROVIDER_TIMEOUT,
                        f"provider {name!r} timed out",
                        provider=name,
                    )
                except ModelProviderError as exc:
                    last_error = exc
                    if not exc.retryable:
                        # Non-retryable: neither retry this provider nor fall back.
                        raise
                else:
                    if index > 0:
                        fallback_from = requested
                        fallback_reason = (
                            last_error.code.value if last_error else "fallback"
                        )
                    return RoutedResult(
                        response=response.model_copy(
                            update={"retry_count": attempt - 1}
                        ),
                        requested_provider=requested,
                        actual_provider=name,
                        attempts=total_attempts,
                        fallback_from=fallback_from,
                        fallback_reason=fallback_reason,
                    )
                # Retryable failure: back off before the next attempt on this provider.
                if attempt < self._max_attempts:
                    await self._backoff(attempt)

        # Every candidate failed.
        if last_error is not None:
            raise last_error
        raise ModelProviderError(
            ModelErrorCode.INTERNAL_ERROR, "no providers were tried"
        )

    async def _backoff(self, attempt: int) -> None:
        if self._backoff_base <= 0:
            return
        delay = self._backoff_base * (2 ** (attempt - 1))
        delay += random.uniform(0, self._backoff_base)  # noqa: S311 - jitter, not crypto
        await asyncio.sleep(delay)
