"""Clock abstraction (CORE-006). All platform code takes time from a Clock, never datetime.now()."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time as a timezone-aware UTC datetime."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Deterministic clock for tests and simulations."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("ManualClock requires a timezone-aware datetime")
        self._now = start.astimezone(UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("ManualClock cannot go backwards")
        self._now += delta
        return self._now


def ensure_utc(value: datetime) -> datetime:
    """Return value as aware UTC. Naive datetimes are rejected rather than guessed."""
    if value.tzinfo is None:
        raise ValueError("naive datetime: timezone required")
    return value.astimezone(UTC)
