"""Injectable clock abstraction.

Business rules never call ``datetime.now()`` or ``date.today()`` directly; they take a
``Clock`` so tests and demonstrations are deterministic. All times are timezone-aware
UTC. Rules that work in calendar days convert with ``today()`` explicitly.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...

    def today(self) -> date:
        """Return the current UTC calendar date."""
        ...


class SystemClock:
    """Real wall-clock time in UTC (used at runtime)."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    def today(self) -> date:
        return datetime.now(UTC).date()


class FixedClock:
    """A clock frozen at a fixed instant (used in tests and the demo CLI)."""

    def __init__(self, moment: datetime) -> None:
        if moment.tzinfo is None:
            raise ValueError("FixedClock requires a timezone-aware datetime")
        self._moment = moment.astimezone(UTC)

    def now(self) -> datetime:
        return self._moment

    def today(self) -> date:
        return self._moment.date()


# The synthetic dataset (S1) is anchored to this instant; the demo CLI and fixture
# tests use it so the day-30 / day-31 boundaries stay deterministic.
SEED_REFERENCE = datetime(2026, 7, 16, 12, 0, tzinfo=UTC)


def seed_reference_clock() -> FixedClock:
    return FixedClock(SEED_REFERENCE)
