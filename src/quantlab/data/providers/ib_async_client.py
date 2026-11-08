"""``IBClient`` implemented with ``ib_async`` (BSD-2, optional extra ``ibkr``) — DATA-019.

Kept deliberately thin: it translates ``ib_async`` objects into the plain ``IBBar`` / ``IBTick`` /
``IBMessage`` records of ``quantlab.data.providers.ibkr`` so all behaviour (pacing, state mapping,
reconnection, guards) lives in the provider and is tested without a network.

The connection is always opened with ``readonly=True``: this client cannot place or cancel orders.
US stocks are requested as ``Stock(symbol, "SMART", "USD")`` and qualified once per symbol.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any

from quantlab.data.providers.base import ProviderError
from quantlab.data.providers.ibkr import IBBar, IBMessage, IBTick

ib_async: Any
try:  # optional dependency
    import ib_async
except ImportError:  # pragma: no cover - exercised only without the extra
    ib_async = None


def _require() -> Any:
    if ib_async is None:  # pragma: no cover
        raise ProviderError("the IBKR provider needs the 'ibkr' extra: `uv sync --extra ibkr`")
    return ib_async


def _num(v: Any) -> float | None:
    if v is None:
        return None
    f = float(v)
    return f if math.isfinite(f) and f >= 0 else None


def _utc(t: datetime | None) -> datetime | None:
    if t is None or t.replace(tzinfo=None) == datetime.min:  # noqa: DTZ901 - ib_async's 'never' value
        return None
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


def tick_from_ticker(symbol: str, ticker: Any) -> IBTick:
    """Snapshot of an ``ib_async.Ticker`` (NaN/-1 → None; ``halted`` > 0 → halted)."""
    halted = _num(getattr(ticker, "halted", None))
    return IBTick(
        symbol=symbol,
        market_data_type=int(ticker.marketDataType),
        received=_utc(ticker.time),
        last_trade_time=_utc(getattr(ticker, "lastTimestamp", None)),
        bid=_num(ticker.bid),
        ask=_num(ticker.ask),
        last=_num(ticker.last),
        bid_size=_num(ticker.bidSize),
        ask_size=_num(ticker.askSize),
        last_size=_num(ticker.lastSize),
        volume=_num(ticker.volume),
        halted=bool(halted and halted > 0),
    )


def bar_from_bardata(bar: Any) -> IBBar:
    d = bar.date
    if isinstance(d, datetime):
        d = _utc(d)
    elif not isinstance(d, date):  # pragma: no cover - defensive
        raise ProviderError(f"unexpected IB bar date {d!r}")
    return IBBar(
        date=d,
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=float(bar.volume),
    )


class IbAsyncClient:
    """Real TWS / IB Gateway connection (blocking ``ib_async`` API)."""

    def __init__(self) -> None:
        lib = _require()
        self._lib = lib
        self._ib = lib.IB()
        self._contracts: dict[str, Any] = {}
        self._tickers: dict[str, Any] = {}
        self._changed: set[str] = set()
        self._messages: list[IBMessage] = []
        self._ib.errorEvent += self._on_error
        self._ib.pendingTickersEvent += self._on_tickers

    # events
    def _on_error(self, req_id: int, code: int, text: str, *_: Any) -> None:
        self._messages.append(IBMessage(int(req_id), int(code), str(text)))

    def _on_tickers(self, tickers: Any) -> None:
        for t in tickers:
            for symbol, mine in self._tickers.items():
                if mine is t:
                    self._changed.add(symbol)

    # IBClient
    def connect(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self._ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=True)

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()
        self._tickers.clear()

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def set_market_data_type(self, code: int) -> None:
        self._ib.reqMarketDataType(code)

    def _contract(self, symbol: str) -> Any:
        if symbol not in self._contracts:
            contract = self._lib.Stock(symbol, "SMART", "USD")
            qualified = self._ib.qualifyContracts(contract)
            if not qualified:
                raise ProviderError(f"IBKR: no security definition for {symbol!r}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    def historical_bars(
        self, symbol: str, end: datetime, duration: str, bar_size: str, what_to_show: str, use_rth: bool
    ) -> list[IBBar]:
        bars = self._ib.reqHistoricalData(
            self._contract(symbol),
            endDateTime=end,
            durationStr=duration,
            barSizeSetting=bar_size,
            whatToShow=what_to_show,
            useRTH=use_rth,
            formatDate=2,
        )
        return [bar_from_bardata(b) for b in bars or []]

    def subscribe(self, symbol: str) -> None:
        contract = self._contract(symbol)
        if symbol in self._tickers:  # restore after "data lost": cancel, then request again
            self._ib.cancelMktData(contract)
        self._tickers[symbol] = self._ib.reqMktData(contract)

    def unsubscribe(self, symbol: str) -> None:
        if symbol in self._tickers:
            self._ib.cancelMktData(self._contracts[symbol])
            del self._tickers[symbol]

    def ticks(self, symbols: Sequence[str]) -> list[IBTick]:
        return [tick_from_ticker(s, self._tickers[s]) for s in symbols if s in self._tickers]

    def wait(self, timeout: float) -> set[str]:
        self._ib.waitOnUpdate(timeout=timeout)
        changed, self._changed = self._changed, set()
        return changed

    def messages(self) -> list[IBMessage]:
        out, self._messages = self._messages, []
        return out
