"""Provider-agnostic market data interface (DATA-018).

Two capabilities, so providers can be swapped by configuration without touching strategies (DEC-0021):

* **historical** — ``fetch_bars(BarRequest) -> RawBars`` (the existing ``DataProvider`` protocol).
  Results only ever enter the system through the versioned catalog; backtests read catalog versions and
  never live feeds.
* **real-time** — ``snapshot`` / ``stream`` of Level I ``Quote`` objects, each carrying its ``DataState``.

Safety rules enforced here, not by callers' good intentions:

* Every quote states whether it is ``LIVE``, ``DELAYED``, ``FROZEN`` or ``SIMULATED``; there is no "unknown".
* ``QuoteGuard`` rejects any quote whose state is not allowed (e.g. a ``DELAYED`` quote when live data is
  required): delayed data is never silently substituted for real-time data.
* Timestamps are normalised to UTC; LIVE quotes older than ``stale_after_s`` (by exchange time) are flagged
  stale and rejected by the guard; exchange timestamps ahead of receipt by more than ``max_clock_skew_s``
  are flagged as clock skew.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol, runtime_checkable

from quantlab.data.providers.base import DataProvider, LicenseInfo


class DataState(StrEnum):
    LIVE = "LIVE"
    DELAYED = "DELAYED"
    FROZEN = "FROZEN"  # last known value outside market hours / no updates
    SIMULATED = "SIMULATED"  # generated or replayed — never real-time market data


class MarketDataError(RuntimeError):
    pass


class DataStateError(MarketDataError):
    """A quote's state is not acceptable for the caller (e.g. DELAYED when LIVE is required)."""


@dataclass(frozen=True)
class Quote:
    symbol: str
    state: DataState
    source: str
    received_ts: datetime  # local receipt time, UTC
    exchange_ts: datetime | None = None  # market timestamp from the feed, UTC
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    last_size: float | None = None
    volume: float | None = None  # cumulative session volume as reported by the feed
    flags: tuple[str, ...] = field(default_factory=tuple)  # e.g. "stale", "clock_skew", "crossed"

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2

    @property
    def latency_s(self) -> float | None:
        if self.exchange_ts is None:
            return None
        return (self.received_ts - self.exchange_ts).total_seconds()


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    connected: bool
    state: DataState | None  # state of the data currently delivered (None when nothing delivered yet)
    detail: str = ""
    reconnects: int = 0
    last_error: str | None = None


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise MarketDataError("naive timestamps are not allowed in quotes")
    return value.astimezone(UTC)


def _num(value: float | None) -> float | None:
    if value is None:
        return None
    v = float(value)
    return v if math.isfinite(v) and v >= 0 else None  # feeds use -1/NaN for "no value"


def normalize_quote(quote: Quote, *, stale_after_s: float = 15.0, max_clock_skew_s: float = 2.0) -> Quote:
    """UTC timestamps, numeric sanity and flags (stale, clock_skew, crossed, no_price)."""
    received, exchange = _utc(quote.received_ts), _utc(quote.exchange_ts)
    assert received is not None
    bid, ask, last = _num(quote.bid), _num(quote.ask), _num(quote.last)
    flags = set(quote.flags)
    if exchange is not None:
        age = (received - exchange).total_seconds()
        if age > stale_after_s and quote.state is DataState.LIVE:
            flags.add("stale")
        if age < -max_clock_skew_s:
            flags.add("clock_skew")
    if bid is not None and ask is not None and bid > ask:
        flags.add("crossed")
    if bid is None and ask is None and last is None:
        flags.add("no_price")
    return replace(
        quote,
        received_ts=received,
        exchange_ts=exchange,
        bid=bid,
        ask=ask,
        last=last,
        bid_size=_num(quote.bid_size),
        ask_size=_num(quote.ask_size),
        last_size=_num(quote.last_size),
        volume=_num(quote.volume),
        symbol=quote.symbol.upper(),
        flags=tuple(sorted(flags)),
    )


# Quotes with any of these flags are never used to fill (paper broker) or to approve an order (risk gate).
NO_TRADE_FLAGS = frozenset({"stale", "clock_skew", "crossed", "no_price", "halted", "frozen"})


@dataclass(frozen=True)
class QuoteGuard:
    """Accept only quotes in ``allowed`` states and without rejecting flags."""

    allowed: frozenset[DataState]
    reject_flags: frozenset[str] = frozenset({"stale", "clock_skew", "crossed", "no_price"})

    @classmethod
    def for_mode(cls, require_live: bool) -> QuoteGuard:
        """Live trading requires LIVE; research/paper simulation may use SIMULATED, never DELAYED silently."""
        return cls(frozenset({DataState.LIVE}) if require_live else frozenset(DataState))

    def check(self, quote: Quote) -> Quote:
        if quote.state not in self.allowed:
            allowed = ", ".join(sorted(s.value for s in self.allowed))
            raise DataStateError(f"{quote.symbol}: {quote.state.value} data rejected (allowed: {allowed})")
        bad = sorted(set(quote.flags) & self.reject_flags)
        if bad:
            raise DataStateError(f"{quote.symbol}: quote rejected ({', '.join(bad)})")
        return quote


@runtime_checkable
class RealtimeProvider(Protocol):
    name: str

    def snapshot(self, symbols: Sequence[str]) -> list[Quote]: ...

    def stream(self, symbols: Sequence[str]) -> Iterator[Quote]:
        """Yield normalised quotes until the iterator is closed."""
        ...

    def status(self) -> ProviderStatus: ...

    def close(self) -> None: ...


@runtime_checkable
class MarketDataProvider(DataProvider, RealtimeProvider, Protocol):
    """Both capabilities (e.g. IBKR). Tiingo is historical-only; mock streaming is real-time-only."""

    license: LicenseInfo
