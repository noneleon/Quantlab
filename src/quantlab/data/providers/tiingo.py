"""Tiingo end-of-day provider (DATA-011).

Facts from Tiingo's documentation (read 2026-11-07, https://www.tiingo.com/documentation/end-of-day):

* ``GET https://api.tiingo.com/tiingo/daily/<ticker>/prices?startDate=YYYY-MM-DD&endDate=YYYY-MM-DD`` —
  historical data is returned only when startDate or endDate is given (otherwise only the latest price).
* Authentication: ``Authorization: Token <api key>`` header (used here; keeps keys out of URLs and logs).
* Records: ``date`` (session date at 00:00 UTC), ``open/high/low/close/volume`` (raw), ``adj*``
  (adjusted, ignored), ``divCash`` (cash dividend; ``date`` = ex-date), ``splitFactor`` (1.0 on normal days;
  "the factor used to adjust prices when a company splits, reverse splits, or pays a distribution").
* Permitted use (Basic/Power plans): "internal and personal use only. You may not redistribute the data in
  any form."
* Rate limits: hourly/daily requests and monthly bandwidth (no per-minute limits); HTTP 429 when exceeded.

QuantLab stores the raw OHLCV and turns ``divCash``/``splitFactor`` into corporate actions (DATA-008). The
direction of ``splitFactor`` (new shares per old share) must be confirmed against real data (KI-0038).
The API key is read from the secret store (``secret://quantlab/tiingo/api-key``) and never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pandas as pd
from pydantic import SecretStr

from quantlab.data.calendars import session_close_utc
from quantlab.data.providers.base import BarRequest, LicenseInfo, ProviderError, RawBars
from quantlab.data.schema import BAR_COLUMNS, Resolution, normalize_symbol

BASE_URL = "https://api.tiingo.com"
API_KEY_REF = "secret://quantlab/tiingo/api-key"
DOCS_URL = "https://www.tiingo.com/documentation/end-of-day"
LICENSE = LicenseInfo(
    "Tiingo Basic/Power: internal and personal use only; no redistribution in any form",
    redistribution_allowed=False,
    url=DOCS_URL,
)
DEFAULT_START = date(1962, 1, 2)  # earliest Tiingo EOD history; historical mode requires a startDate
MAX_RETRIES = 3
INTRADAY_RESPONSE_CAP = 10_000  # observed 2026-11-07: larger ranges return only the most recent 10 000 bars
INTRADAY_WINDOW_DAYS = 14  # <= 10 sessions x 390 minutes, far below the cap


class TiingoProvider:
    """DataProvider for Tiingo daily EOD prices (raw OHLCV + dividends + splits)."""

    name = "tiingo"
    license = LICENSE

    def __init__(
        self,
        api_key: SecretStr,
        *,
        calendar: str,
        transport: httpx.BaseTransport | None = None,
        min_interval_s: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not api_key.get_secret_value():
            raise ProviderError("Tiingo API key is empty")
        self._key = api_key
        self.calendar = calendar
        self._client = httpx.Client(base_url=BASE_URL, transport=transport, timeout=60.0)
        self._min_interval = min_interval_s
        self._sleep = sleep
        self._clock = clock
        self._last_request = 0.0

    def close(self) -> None:
        self._client.close()

    # ---- HTTP ----------------------------------------------------------------------------------
    def _get(self, path: str, params: dict[str, str]) -> Any:
        headers = {"Authorization": f"Token {self._key.get_secret_value()}", "Accept": "application/json"}
        for attempt in range(MAX_RETRIES + 1):
            wait = self._min_interval - (time.monotonic() - self._last_request)
            if wait > 0:
                self._sleep(wait)
            self._last_request = time.monotonic()
            try:
                response = self._client.get(path, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise ProviderError(f"Tiingo request failed: {type(exc).__name__}") from exc
            if response.status_code == 429:
                if attempt == MAX_RETRIES:
                    raise ProviderError(
                        "Tiingo rate limit (HTTP 429) persisted after retries; try again later"
                    )
                retry_after = response.headers.get("Retry-After", "")
                self._sleep(float(retry_after) if retry_after.isdigit() else 2.0**attempt * 5)
                continue
            if response.status_code in (401, 403):
                raise ProviderError(f"Tiingo rejected the API key (HTTP {response.status_code})")
            if response.status_code == 404:
                raise ProviderError(f"Tiingo: unknown ticker or endpoint ({path})")
            if response.status_code >= 400:
                raise ProviderError(f"Tiingo HTTP {response.status_code}: {response.text[:200]}")
            try:
                return response.json()
            except ValueError as exc:
                raise ProviderError("Tiingo returned non-JSON content") from exc
        raise AssertionError("unreachable")  # pragma: no cover - loop always returns or raises

    # ---- DataProvider ----------------------------------------------------------------------------
    def fetch_bars(self, request: BarRequest) -> RawBars:
        if not request.symbols:
            raise ProviderError("Tiingo requires at least one symbol")
        if request.resolution is Resolution.MINUTE:
            return self._fetch_minutes(request)
        if request.resolution is not Resolution.DAILY:
            raise ProviderError("Tiingo supports daily (EOD) and minute (IEX intraday) resolution")
        start = (request.start.date() if request.start else DEFAULT_START).isoformat()
        end = (request.end.date() if request.end else self._clock().date()).isoformat()
        frames, actions = [], []
        for raw_symbol in request.symbols:
            symbol = normalize_symbol(raw_symbol)
            records = self._get(
                f"/tiingo/daily/{symbol.lower()}/prices",
                {"startDate": start, "endDate": end, "resampleFreq": "daily", "format": "json"},
            )
            if not isinstance(records, list):
                raise ProviderError(f"Tiingo returned an unexpected payload for {symbol}")
            bars, sym_actions = self._parse(symbol, records)
            frames.append(bars)
            actions.extend(sym_actions)
        frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=list(BAR_COLUMNS))
        fetched = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        notes = (
            f"fetched from Tiingo EOD {start}..{end} at {fetched}",
            "raw OHLCV stored; Tiingo adj* fields ignored; divCash/splitFactor recorded as corporate actions",
        )
        action_frame = pd.DataFrame(actions, columns=["symbol", "ex_date", "kind", "value"])
        return RawBars(frame, f"tiingo:{','.join(request.symbols)}", notes, action_frame)

    # ---- intraday (IEX) --------------------------------------------------------------------------
    def _fetch_minutes(self, request: BarRequest) -> RawBars:
        """1-minute regular-session bars from Tiingo's IEX endpoint, fetched in windows below the cap.

        Verified 2026-11-07 against the real API: ``date`` is the bar *start* (first bar 13:30Z = 09:30 ET)
        and one response holds at most 10 000 bars, keeping the most RECENT ones — so a full response is
        treated as possibly truncated and the window is split, never accepted as complete.
        """
        if request.start is None:
            raise ProviderError(
                "intraday fetches need --start (Tiingo IEX history is limited and capped per request)"
            )
        start = request.start.date()
        end = (request.end or self._clock()).date()
        frames = []
        for raw_symbol in request.symbols:
            symbol = normalize_symbol(raw_symbol)
            records: list[dict[str, Any]] = []
            day = start
            while day <= end:
                window_end = min(end, day + timedelta(days=INTRADAY_WINDOW_DAYS - 1))
                records.extend(self._minute_window(symbol, day, window_end))
                day = window_end + timedelta(days=1)
            frames.append(self._parse_minutes(symbol, records))
        frame = pd.concat(frames, ignore_index=True)
        fetched = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        notes = (
            f"fetched from Tiingo IEX intraday 1min {start}..{end} at {fetched}",
            "IEX-only volume (not consolidated); regular session only; bar start times shifted to bar end",
        )
        return RawBars(frame, f"tiingo-iex:{','.join(request.symbols)}", notes)

    def _minute_window(self, symbol: str, first: date, last: date) -> list[dict[str, Any]]:
        records = self._get(
            f"/iex/{symbol.lower()}/prices",
            {
                "startDate": first.isoformat(),
                "endDate": last.isoformat(),
                "resampleFreq": "1min",
                "columns": "open,high,low,close,volume",
            },
        )
        if not isinstance(records, list):
            raise ProviderError(f"Tiingo returned an unexpected intraday payload for {symbol}")
        if len(records) >= INTRADAY_RESPONSE_CAP:
            if first >= last:
                raise ProviderError(
                    f"Tiingo intraday response for {symbol} on {first} hit the {INTRADAY_RESPONSE_CAP}"
                    "-bar cap; refusing possibly truncated data"
                )
            middle = first + (last - first) // 2
            return self._minute_window(symbol, first, middle) + self._minute_window(
                symbol, middle + timedelta(1), last
            )
        return records

    def _parse_minutes(self, symbol: str, records: list[dict[str, Any]]) -> pd.DataFrame:
        rows = []
        for rec in records:
            try:
                start = datetime.fromisoformat(str(rec["date"]).replace("Z", "+00:00")).astimezone(UTC)
                rows.append(
                    {
                        "symbol": symbol,
                        "ts": start + timedelta(minutes=1),
                        **{k: float(rec[k]) for k in ("open", "high", "low", "close")},
                        "volume": float(rec["volume"]) if rec.get("volume") is not None else float("nan"),
                    }
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(f"Tiingo intraday record for {symbol} is malformed: {exc}") from exc
        frame = pd.DataFrame(rows, columns=list(BAR_COLUMNS))
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        return frame

    def _parse(self, symbol: str, records: list[dict[str, Any]]) -> tuple[pd.DataFrame, list[dict[str, Any]]]:
        rows, actions = [], []
        for rec in records:
            try:
                day = date.fromisoformat(str(rec["date"])[:10])
                row = {
                    "symbol": symbol,
                    "ts": session_close_utc(self.calendar, day),
                    **{k: float(rec[k]) for k in ("open", "high", "low", "close")},
                    "volume": float(rec["volume"]) if rec.get("volume") is not None else float("nan"),
                }
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderError(f"Tiingo record for {symbol} is malformed: {exc}") from exc
            rows.append(row)
            div = float(rec.get("divCash") or 0.0)
            split = float(rec.get("splitFactor") or 1.0)
            if div > 0:
                actions.append({"symbol": symbol, "ex_date": day, "kind": "dividend", "value": div})
            if split != 1.0:
                actions.append({"symbol": symbol, "ex_date": day, "kind": "split", "value": split})
        frame = pd.DataFrame(rows, columns=list(BAR_COLUMNS))
        frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
        return frame, actions
