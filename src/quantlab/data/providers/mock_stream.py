"""Mock real-time provider for development (DATA-018): replays catalog bars as Level I quotes.

Every quote is ``DataState.SIMULATED`` — it can never pass a ``QuoteGuard`` that requires ``LIVE`` data.
Quotes are derived deterministically from bars: ``last`` = close, ``bid``/``ask`` = close -/+ half of
``spread_bps``, ``volume`` = cumulative bar volume within the session; ``exchange_ts`` = ``received_ts`` =
the bar's end time (a replay clock, so replays are reproducible). Optional ``interval_s`` paces replay in
wall time.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Sequence

import pandas as pd

from quantlab.data.market_data import DataState, MarketDataError, ProviderStatus, Quote, normalize_quote


class MockStreamingProvider:
    name = "mock"

    def __init__(
        self,
        bars: pd.DataFrame,
        *,
        source: str,
        spread_bps: float = 2.0,
        interval_s: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if bars.empty:
            raise MarketDataError("mock streaming needs bars to replay")
        self._bars = bars.sort_values(["ts", "symbol"], kind="stable").reset_index(drop=True)
        self._source = source
        self._half_spread = spread_bps / 20_000.0
        self._interval = interval_s
        self._sleep = sleep
        self._delivered = 0
        self._closed = False

    def _quote(self, row: pd.Series, cumulative_volume: float) -> Quote:
        ts = pd.Timestamp(row["ts"]).to_pydatetime()
        close = float(row["close"])
        return normalize_quote(
            Quote(
                symbol=str(row["symbol"]),
                state=DataState.SIMULATED,
                source=f"mock replay of {self._source}",
                received_ts=ts,
                exchange_ts=ts,
                bid=close * (1 - self._half_spread),
                ask=close * (1 + self._half_spread),
                last=close,
                last_size=float(row["volume"]) if pd.notna(row["volume"]) else None,
                volume=cumulative_volume,
            )
        )

    def _rows(self, symbols: Sequence[str]) -> pd.DataFrame:
        wanted = {s.upper() for s in symbols}
        rows = self._bars[self._bars["symbol"].isin(wanted)]
        if rows.empty:
            raise MarketDataError(f"no bars to replay for {sorted(wanted)}")
        return rows

    def snapshot(self, symbols: Sequence[str]) -> list[Quote]:
        rows = self._rows(symbols)
        last = rows.groupby("symbol", sort=True).tail(1)
        return [
            self._quote(r, float(r["volume"]) if pd.notna(r["volume"]) else 0.0) for _, r in last.iterrows()
        ]

    def stream(self, symbols: Sequence[str]) -> Iterator[Quote]:
        rows = self._rows(symbols)
        cumulative: dict[tuple[str, object], float] = {}
        for _, row in rows.iterrows():
            if self._closed:
                return
            key = (str(row["symbol"]), pd.Timestamp(row["ts"]).date())
            volume = float(row["volume"]) if pd.notna(row["volume"]) else 0.0
            cumulative[key] = cumulative.get(key, 0.0) + volume
            if self._interval > 0 and self._delivered:
                self._sleep(self._interval)
            self._delivered += 1
            yield self._quote(row, cumulative[key])

    def status(self) -> ProviderStatus:
        return ProviderStatus(
            self.name,
            connected=not self._closed,
            state=DataState.SIMULATED,
            detail=f"replaying {self._source}; {self._delivered} quote(s) delivered",
        )

    def close(self) -> None:
        self._closed = True
