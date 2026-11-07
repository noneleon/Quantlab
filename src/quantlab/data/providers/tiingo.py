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
from datetime import UTC, date, datetime
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
        if request.resolution is not Resolution.DAILY:
            raise ProviderError("the Tiingo EOD provider supports daily resolution only")
        if not request.symbols:
            raise ProviderError("Tiingo requires at least one symbol")
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
