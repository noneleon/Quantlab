"""Interactive Brokers market data via TWS / IB Gateway (DATA-019).

``IBKRProvider`` implements both halves of ``MarketDataProvider`` (DEC-0021):

* historical bars (``fetch_bars``) — TRADES bars, regular trading hours, daily or 1-minute, fetched in
  windows and paced (``HistoricalPacer``); results enter QuantLab only through the versioned catalog;
* Level I quotes (``snapshot`` / ``stream``) — bid, ask, last, sizes, cumulative volume, market timestamps.

The provider talks to IB through the narrow ``IBClient`` protocol; ``ib_async_client.IbAsyncClient`` is the
real implementation (optional extra ``ibkr``), tests use a fake. This module never imports ``ib_async``.

Safety properties:

* The market-data connection is **read-only** (``readonly=True``) and has its own client id: it cannot
  place, modify or cancel orders. Orders belong to the broker adapter behind the OMS and risk gate (Phase 5).
* The state of every quote comes from what IB says it delivered (``marketDataType`` tick), not from what
  was requested: if IB falls back to delayed data the quote is ``DELAYED`` and a live-only guard rejects it.
* With ``require_live`` the provider only ever *requests* live data (type 1).
* Disconnections are retried with exponential backoff, subscriptions are restored, and every reconnect is
  counted in ``status()``; after ``max_reconnects`` failures the stream raises instead of going quiet.

Facts about IB behaviour used here come from the ``ib_async`` 2.1.0 source (``Ticker`` fields,
``marketDataType`` codes 1 live / 2 frozen / 3 delayed / 4 delayed-frozen, warning codes); IB's own API
documentation could not be read from this environment, so the following are **unverified assumptions**,
listed in KNOWN_ISSUES (KI-0044): intraday bar ``date`` is the bar start; TRADES daily bars are split- but
not dividend-adjusted; historical pacing of 60 requests / 10 minutes; ``durationStr`` limits per bar size.
"""

from __future__ import annotations

import contextlib
import math
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Protocol

import pandas as pd

from quantlab.data.calendars import is_session, session_bounds, session_close_utc, sessions
from quantlab.data.market_data import DataState, MarketDataError, ProviderStatus, Quote, normalize_quote
from quantlab.data.providers.base import BarRequest, LicenseInfo, ProviderError, RawBars
from quantlab.data.schema import Resolution

LICENSE = LicenseInfo(
    terms=(
        "Interactive Brokers market data: for the account holder's own use under the exchange "
        "subscriptions of that account; redistribution not allowed"
    ),
    redistribution_allowed=False,
    url="https://www.interactivebrokers.com/en/pricing/market-data-pricing.php",
)

# reqMarketDataType codes (ib_async docs) -> what QuantLab reports.
MARKET_DATA_TYPES = {"live": 1, "frozen": 2, "delayed": 3, "delayed_frozen": 4}
STATE_BY_TYPE = {1: DataState.LIVE, 2: DataState.FROZEN, 3: DataState.DELAYED, 4: DataState.DELAYED}

# IB message codes we act on (meanings as reported by IB's messages; see KI-0044 for verification status).
CONNECTIVITY_LOST = 1100
RESTORED_DATA_LOST = 1101  # connectivity restored, market data subscriptions lost -> resubscribe
RESTORED_DATA_KEPT = 1102
NO_SECURITY_DEFINITION = 200
HISTORICAL_ERROR = 162  # includes pacing violations and "query returned no data"
NOT_SUBSCRIBED = 354
DELAYED_DISPLAYED = 10167
COMPETING_SESSION = 10197


class IBConnectError(ProviderError):
    """TWS / IB Gateway not reachable (or refused the connection)."""


@dataclass(frozen=True)
class IBBar:
    """One historical bar as IB returns it (``date``: session date for daily bars, UTC start otherwise)."""

    date: date | datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class IBTick:
    """Current Level I state of one subscription (a snapshot of an ``ib_async.Ticker``)."""

    symbol: str
    market_data_type: int
    received: datetime | None  # local arrival time of the last update (UTC)
    last_trade_time: datetime | None  # exchange time of the last trade (tick 45), UTC
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    last_size: float | None = None
    volume: float | None = None
    halted: bool = False


@dataclass(frozen=True)
class IBMessage:
    req_id: int
    code: int
    text: str


class IBClient(Protocol):
    """The subset of the TWS API QuantLab uses for market data."""

    def connect(self, host: str, port: int, client_id: int, timeout: float) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def set_market_data_type(self, code: int) -> None: ...

    def historical_bars(
        self, symbol: str, end: datetime, duration: str, bar_size: str, what_to_show: str, use_rth: bool
    ) -> list[IBBar]: ...

    def subscribe(self, symbol: str) -> None: ...

    def unsubscribe(self, symbol: str) -> None: ...

    def ticks(self, symbols: Sequence[str]) -> list[IBTick]:
        """Current state of the given subscriptions."""
        ...

    def wait(self, timeout: float) -> set[str]:
        """Process network events for up to ``timeout`` s; return the symbols whose quotes changed."""
        ...

    def messages(self) -> list[IBMessage]:
        """IB errors/warnings received since the last call."""
        ...


@dataclass
class HistoricalPacer:
    """Sliding-window limit on historical requests (default 60 per 600 s, plus a minimum gap)."""

    max_requests: int = 60
    window_s: float = 600.0
    min_interval_s: float = 0.5
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    _sent: deque[float] = field(default_factory=deque)

    def acquire(self) -> None:
        now = self.clock()
        while self._sent and now - self._sent[0] >= self.window_s:
            self._sent.popleft()
        wait = 0.0
        if len(self._sent) >= self.max_requests:
            wait = self.window_s - (now - self._sent[0])
        if self._sent:
            wait = max(wait, self.min_interval_s - (now - self._sent[-1]))
        if wait > 0:
            self.sleep(wait)
            now = self.clock()
            while self._sent and now - self._sent[0] >= self.window_s:
                self._sent.popleft()
        self._sent.append(now)


def _value(v: float | None) -> float | None:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) and f >= 0 else None


class IBKRProvider:
    """Historical + real-time market data from TWS / IB Gateway (read-only connection)."""

    name = "ibkr"
    license = LICENSE
    adjustment = "split_adjusted"  # TRADES bars (assumption, KI-0044); checked against the dataset by fetch

    def __init__(
        self,
        client: IBClient,
        *,
        host: str,
        port: int,
        client_id: int,
        calendar: str = "XNYS",
        data_type: str = "live",
        require_live: bool = False,
        stale_after_s: float = 15.0,
        connect_timeout_s: float = 10.0,
        snapshot_timeout_s: float = 5.0,
        max_reconnects: int = 5,
        backoff_s: float = 1.0,
        pacer: HistoricalPacer | None = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if data_type not in MARKET_DATA_TYPES:
            raise MarketDataError(f"unknown IBKR market data type {data_type!r}")
        if require_live and data_type != "live":
            raise MarketDataError("require_live needs ibkr_data_type='live'")
        self._client = client
        self._host, self._port, self._client_id = host, port, client_id
        self.calendar = calendar
        self._data_type = MARKET_DATA_TYPES[data_type]
        self._stale_after = stale_after_s
        self._connect_timeout = connect_timeout_s
        self._snapshot_timeout = snapshot_timeout_s
        self._max_reconnects = max_reconnects
        self._backoff = backoff_s
        self._pacer = pacer or HistoricalPacer(sleep=sleep)
        self._sleep = sleep
        self._now = now
        self._subscribed: set[str] = set()
        self._kept: dict[str, float] = {}  # latest(): symbol -> last request (monotonic), kept subscribed
        self._reconnects = 0
        self._last_error: str | None = None
        self._last_state: DataState | None = None
        self._link_down = False  # IB 1100 seen and not yet restored (1101/1102): every quote is stale
        self._notes: list[str] = []

    # ---- connection ---------------------------------------------------------------------------------
    @property
    def endpoint(self) -> str:
        return f"{self._host}:{self._port} (client id {self._client_id})"

    def _connect(self) -> None:
        if self._client.is_connected():
            return
        try:
            self._client.connect(self._host, self._port, self._client_id, self._connect_timeout)
        except (OSError, TimeoutError, ConnectionError) as exc:
            self._last_error = f"cannot connect to TWS/IB Gateway at {self.endpoint}: {exc}"
            raise IBConnectError(
                f"{self._last_error}. Start IB Gateway or TWS, enable the API (socket clients) on that port "
                "and check brokers.ibkr.host / port_paper"
            ) from exc
        self._client.set_market_data_type(self._data_type)

    def _reconnect(self) -> None:
        """Exponential backoff; restores subscriptions. Raises MarketDataError when attempts are exhausted."""
        delay = self._backoff
        for attempt in range(1, self._max_reconnects + 1):
            self._sleep(delay)
            try:
                self._client.disconnect()
                self._connect()
            except IBConnectError:
                delay = min(delay * 2, 60.0)
                continue
            self._reconnects += 1
            for symbol in sorted(self._subscribed):
                self._client.subscribe(symbol)
            self._notes.append(f"reconnected after {attempt} attempt(s)")
            return
        raise MarketDataError(
            f"connection to {self.endpoint} lost and {self._max_reconnects} reconnect attempts failed"
        )

    def _handle_messages(self) -> bool:
        """Record IB messages; returns True when subscriptions must be restored."""
        resubscribe = False
        for msg in self._client.messages():
            if msg.code in (DELAYED_DISPLAYED, NOT_SUBSCRIBED, COMPETING_SESSION, CONNECTIVITY_LOST):
                self._last_error = f"IB {msg.code}: {msg.text}"
            if msg.code == CONNECTIVITY_LOST:
                self._link_down = True
            if msg.code in (RESTORED_DATA_LOST, RESTORED_DATA_KEPT):
                self._link_down = False
            if msg.code == RESTORED_DATA_LOST:
                resubscribe = True
                self._last_error = f"IB {msg.code}: {msg.text}"
        return resubscribe

    def close(self) -> None:
        for symbol in sorted(self._subscribed):
            with contextlib.suppress(Exception):  # best effort during shutdown
                self._client.unsubscribe(symbol)
        self._subscribed.clear()
        self._client.disconnect()

    def status(self) -> ProviderStatus:
        if not self._client.is_connected() and self._last_error is None:
            with contextlib.suppress(IBConnectError):  # reported below via last_error
                self._connect()
        connected = self._client.is_connected()
        self._handle_messages()
        requested = {v: k for k, v in MARKET_DATA_TYPES.items()}[self._data_type]
        return ProviderStatus(
            provider=self.name,
            connected=connected,
            state=self._last_state,
            detail=f"{self.endpoint}, read-only, requested market data type: {requested}",
            reconnects=self._reconnects,
            last_error=self._last_error,
        )

    # ---- real-time ----------------------------------------------------------------------------------
    def _quote(self, tick: IBTick) -> Quote:
        state = STATE_BY_TYPE.get(tick.market_data_type)
        if state is None:
            raise MarketDataError(f"{tick.symbol}: unknown IB market data type {tick.market_data_type}")
        flags = []
        if tick.market_data_type == 4:
            flags.append("frozen")
        if tick.halted:
            flags.append("halted")
        self._last_state = state
        return normalize_quote(
            Quote(
                symbol=tick.symbol,
                state=state,
                source=f"ibkr {self._host}:{self._port}",
                received_ts=tick.received or self._now(),
                exchange_ts=tick.last_trade_time,
                bid=_value(tick.bid),
                ask=_value(tick.ask),
                last=_value(tick.last),
                bid_size=_value(tick.bid_size),
                ask_size=_value(tick.ask_size),
                last_size=_value(tick.last_size),
                volume=_value(tick.volume),
                flags=tuple(flags),
            ),
            stale_after_s=self._stale_after,
        )

    def _subscribe(self, symbols: Sequence[str]) -> list[str]:
        wanted = [s.upper() for s in symbols]
        if not wanted:
            raise MarketDataError("no symbols given")
        self._connect()
        for symbol in wanted:
            if symbol not in self._subscribed:
                self._client.subscribe(symbol)
                self._subscribed.add(symbol)
        return wanted

    def snapshot(self, symbols: Sequence[str]) -> list[Quote]:
        wanted = self._subscribe(symbols)
        deadline = time.monotonic() + self._snapshot_timeout
        ticks = self._client.ticks(wanted)
        while time.monotonic() < deadline and any(
            t.bid is None and t.ask is None and t.last is None for t in ticks
        ):
            self._client.wait(0.25)
            ticks = self._client.ticks(wanted)
        self._handle_messages()
        quotes = [self._quote(t) for t in ticks]
        for symbol in wanted:
            self._client.unsubscribe(symbol)
            self._subscribed.discard(symbol)
        return quotes

    def latest(self, symbols: Sequence[str], *, idle_after_s: float = 300.0) -> list[Quote]:
        """Current quotes from subscriptions that stay open (deployments ask every second; KI-0080).

        A symbol's first request waits for data like ``snapshot``; later ones only read the current ticker.
        Subscriptions not requested for ``idle_after_s`` are cancelled. Never reconnects or backs off (it is
        called under the OMS quote lock, review 5c-5 M2): a lost connection raises ``IBConnectError`` and the
        caller rebuilds the provider after its own back-off. A ticker that has not updated for
        ``stale_after_s``, or any quote while IB reports lost connectivity (1100), is flagged ``stale``.
        """
        if not self._client.is_connected() and (self._subscribed or self._kept):
            self._last_error = f"connection to {self.endpoint} lost"
            raise IBConnectError(self._last_error)
        wanted = [s.upper() for s in symbols]
        new = [s for s in wanted if s not in self._subscribed]
        self._subscribe(wanted)
        now = time.monotonic()
        for symbol in wanted:
            self._kept[symbol] = now
        self._client.wait(0.05)  # process pending updates without blocking
        if self._handle_messages():  # 1101: resubscribe before reading, so no ticker from the old session
            for symbol in sorted(self._subscribed):
                self._client.subscribe(symbol)
            self._reconnects += 1
            new = list(wanted)
        ticks = self._client.ticks(wanted)
        if new:
            deadline = time.monotonic() + self._snapshot_timeout
            while time.monotonic() < deadline and any(
                t.symbol in new and t.bid is None and t.ask is None and t.last is None for t in ticks
            ):
                self._client.wait(0.25)
                ticks = self._client.ticks(wanted)
        for symbol, last in list(self._kept.items()):
            if now - last > idle_after_s:
                self._client.unsubscribe(symbol)
                self._subscribed.discard(symbol)
                del self._kept[symbol]
        return [self._aged(self._quote(t)) for t in ticks]

    def _aged(self, quote: Quote) -> Quote:
        """Flag a kept ticker whose last update is too old (measured against now, not the exchange time)."""
        age = (self._now() - quote.received_ts).total_seconds()
        if (self._link_down or age > self._stale_after) and "stale" not in quote.flags:
            return replace(quote, flags=tuple(sorted({*quote.flags, "stale"})))
        return quote

    def stream(self, symbols: Sequence[str]) -> Iterator[Quote]:
        wanted = self._subscribe(symbols)
        try:
            while True:
                if not self._client.is_connected():
                    self._reconnect()
                changed = self._client.wait(1.0)
                if self._handle_messages():
                    for symbol in sorted(self._subscribed):
                        self._client.subscribe(symbol)
                    self._reconnects += 1
                if not changed:
                    continue
                for tick in self._client.ticks([s for s in wanted if s in changed]):
                    yield self._quote(tick)
        finally:
            for symbol in wanted:
                if symbol in self._subscribed and self._client.is_connected():
                    self._client.unsubscribe(symbol)
                self._subscribed.discard(symbol)

    # ---- historical ---------------------------------------------------------------------------------
    def fetch_bars(self, request: BarRequest) -> RawBars:
        if request.resolution not in (Resolution.DAILY, Resolution.MINUTE):
            raise ProviderError("IBKR provider supports daily and minute resolution")
        if not request.symbols:
            raise ProviderError("IBKR needs explicit symbols")
        if request.start is None:
            raise ProviderError("IBKR historical requests need a start date (--start)")
        first = request.start.date()
        last = (request.end or self._now()).date()
        if last < first:
            raise ProviderError("end is before start")
        self._notes = []
        self._connect()
        frames = []
        for raw in request.symbols:
            symbol = raw.upper()
            if request.resolution is Resolution.DAILY:
                frames.append(self._daily(symbol, first, last))
            else:
                frames.append(self._minutes(symbol, first, last))
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if frame.empty:
            raise ProviderError(f"IBKR returned no bars for {list(request.symbols)} {first}..{last}")
        notes = [
            "IBKR TRADES bars, regular trading hours",
            "daily TRADES prices are split-adjusted by IB (assumption, KI-0044); no corporate actions stored",
            *self._notes,
        ]
        return RawBars(frame=frame, source=f"ibkr {self._host}:{self._port}", notes=tuple(notes))

    def _request(
        self, symbol: str, end: datetime, duration: str, bar_size: str, *, attempts: int = 4
    ) -> list[IBBar]:
        delay = 15.0
        for attempt in range(1, attempts + 1):
            self._pacer.acquire()
            bars = self._client.historical_bars(symbol, end, duration, bar_size, "TRADES", True)
            problems = [
                m for m in self._client.messages() if m.code in (HISTORICAL_ERROR, NO_SECURITY_DEFINITION)
            ]
            if any(m.code == NO_SECURITY_DEFINITION for m in problems):
                raise ProviderError(f"IBKR: no security definition for {symbol!r}")
            pacing = [m for m in problems if "pacing" in m.text.lower()]
            if pacing and attempt < attempts:
                self._notes.append(f"pacing violation for {symbol}; retried after {delay:.0f}s")
                self._sleep(delay)
                delay *= 2
                continue
            if pacing:
                raise ProviderError(f"IBKR pacing violation persisted for {symbol}: {pacing[-1].text}")
            other = [m for m in problems if "no data" not in m.text.lower()]
            if other and not bars:
                raise ProviderError(f"IBKR historical data error for {symbol}: {other[-1].text}")
            return bars
        return []  # pragma: no cover - loop always returns or raises

    def _daily(self, symbol: str, first: date, last: date) -> pd.DataFrame:
        """Windows of at most 365 calendar days, newest first, each ending at its last session close."""
        rows = []
        window_end = last
        while window_end >= first:
            window_start = max(first, window_end - timedelta(days=364))
            days = sessions(self.calendar, window_start, window_end)
            if days:
                end = session_close_utc(self.calendar, days[-1])
                duration = f"{(window_end - window_start).days + 1} D"
                for bar in self._request(symbol, end, duration, "1 day"):
                    day = bar.date.date() if isinstance(bar.date, datetime) else bar.date
                    if first <= day <= last:
                        rows.append(self._row(symbol, self._daily_end(day), bar))
            window_end = window_start - timedelta(days=1)
        return self._frame(rows)

    def _minutes(self, symbol: str, first: date, last: date) -> pd.DataFrame:
        """One request per regular session ("1 D" ending at the close); bar start + 1 min = bar end."""
        rows = []
        dropped = 0
        for _day, open_utc, close_utc in session_bounds(self.calendar, first, last):
            for bar in self._request(symbol, close_utc, "1 D", "1 min"):
                if not isinstance(bar.date, datetime) or bar.date.tzinfo is None:
                    raise ProviderError("IBKR minute bars must carry UTC timestamps (formatDate=2)")
                start = bar.date.astimezone(UTC)
                if not open_utc <= start < close_utc:
                    dropped += 1  # another session's bar (the "1 D" window can reach back)
                    continue
                rows.append(self._row(symbol, start + timedelta(minutes=1), bar))
        if dropped:
            self._notes.append(f"{symbol}: {dropped} bar(s) outside the requested sessions ignored")
        return self._frame(rows)

    def _daily_end(self, day: date) -> datetime:
        """Session close; a bar on a non-session day keeps its date so validation rejects it (no fix)."""
        if is_session(self.calendar, day):
            return session_close_utc(self.calendar, day)
        return datetime.combine(day, dtime(21), tzinfo=UTC)

    @staticmethod
    def _row(symbol: str, ts: datetime, bar: IBBar) -> dict[str, object]:
        volume = float(bar.volume)
        return {
            "symbol": symbol,
            "ts": ts,
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": volume if math.isfinite(volume) and volume >= 0 else float("nan"),
        }

    @staticmethod
    def _frame(rows: list[dict[str, object]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
        if not frame.empty:
            frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
            frame = frame.drop_duplicates(["symbol", "ts"], keep="last").sort_values("ts", kind="stable")
        return frame.reset_index(drop=True)
