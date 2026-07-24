"""A minimal circuit breaker for the provider layer (S7).

Closed → (N consecutive failures) → Open → (cooldown) → Half-open → (probe result)
→ Closed or Open. When a provider's breaker is open the router skips it and falls
to the next candidate (finally the deterministic mock), so the system never hard-fails.
Breaker state is exposed as a metric; it is never recorded as a business audit event.
"""

from __future__ import annotations

from enum import StrEnum

from app.observability.metrics import M_BREAKER_STATE, registry


class BreakerState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


_STATE_VALUE = {
    BreakerState.CLOSED: 0.0,
    BreakerState.OPEN: 1.0,
    BreakerState.HALF_OPEN: 2.0,
}


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        threshold: int = 5,
        cooldown_seconds: float = 30.0,
    ) -> None:
        self._name = name
        self._threshold = max(1, threshold)
        self._cooldown = cooldown_seconds
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._emit()

    @property
    def state(self) -> BreakerState:
        return self._state

    def allow(self, now: float) -> bool:
        if self._state is BreakerState.OPEN:
            if now - self._opened_at >= self._cooldown:
                self._transition(BreakerState.HALF_OPEN)
                return True
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        if self._state is not BreakerState.CLOSED:
            self._transition(BreakerState.CLOSED)

    def record_failure(self, now: float) -> None:
        if self._state is BreakerState.HALF_OPEN:
            self._opened_at = now
            self._transition(BreakerState.OPEN)
            return
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = now
            self._transition(BreakerState.OPEN)

    def _transition(self, state: BreakerState) -> None:
        self._state = state
        self._emit()

    def _emit(self) -> None:
        registry().set_gauge(
            M_BREAKER_STATE, _STATE_VALUE[self._state], breaker=self._name
        )


class BreakerRegistry:
    """Per-provider breakers, created lazily with shared thresholds."""

    def __init__(self, *, threshold: int, cooldown_seconds: float) -> None:
        self._threshold = threshold
        self._cooldown = cooldown_seconds
        self._breakers: dict[str, CircuitBreaker] = {}

    def get(self, name: str) -> CircuitBreaker:
        breaker = self._breakers.get(name)
        if breaker is None:
            breaker = CircuitBreaker(
                name, threshold=self._threshold, cooldown_seconds=self._cooldown
            )
            self._breakers[name] = breaker
        return breaker
