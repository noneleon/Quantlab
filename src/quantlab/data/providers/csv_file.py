"""CSV/Parquet file importer for user-owned data (DATA-002).

Turns a file with arbitrary column names into the canonical bar frame:

* columns are matched case-insensitively (``Date``/``datetime``/``timestamp``/``time``, ``Open``, …,
  ``Volume``); explicit names can be given in ``ColumnMap``;
* **date-only** values are trading-session dates: the bar end time becomes the session close from the
  dataset calendar (e.g. NYSE 16:00 New York → 20:00/21:00 UTC);
* **datetime** values are interpreted in ``timezone`` when naive and converted to UTC; with
  ``timestamp_is_bar_start`` the bar length is added so ``ts`` is the bar *end* (LEAN convention);
* an ``Adj Close``-style column is never used silently: the dataset's declared adjustment governs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from quantlab.data.calendars import session_close_utc
from quantlab.data.providers.base import BarRequest, LicenseInfo, ProviderError, RawBars
from quantlab.data.schema import BAR_COLUMNS, Resolution, normalize_symbol

_ALIASES = {
    "ts": ("timestamp", "datetime", "date", "time", "ts"),
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c", "last"),
    "volume": ("volume", "vol", "v"),
    "symbol": ("symbol", "ticker"),
}
_ADJUSTED_HINTS = ("adj close", "adj_close", "adjclose", "adjusted close", "adjusted_close")


@dataclass(frozen=True)
class ColumnMap:
    ts: str | None = None
    open: str | None = None
    high: str | None = None
    low: str | None = None
    close: str | None = None
    volume: str | None = None
    symbol: str | None = None


def _resolve_columns(columns: list[str], explicit: ColumnMap) -> dict[str, str | None]:
    lowered = {c.strip().lower(): c for c in columns}
    resolved: dict[str, str | None] = {}
    for canonical, aliases in _ALIASES.items():
        given = getattr(explicit, canonical)
        if given is not None:
            if given not in columns:
                raise ProviderError(f"column {given!r} not found; available: {columns}")
            resolved[canonical] = given
            continue
        resolved[canonical] = next((lowered[a] for a in aliases if a in lowered), None)
    missing = [c for c in ("ts", "open", "high", "low", "close") if resolved[c] is None]
    if missing:
        raise ProviderError(f"required columns not found: {missing}; available: {columns}")
    return resolved


class CsvFileProvider:
    """DataProvider for a local CSV or Parquet file supplied by the user."""

    name = "file"

    def __init__(
        self,
        path: Path,
        *,
        calendar: str,
        symbol: str | None = None,
        timezone: str = "UTC",
        timestamp_is_bar_start: bool = False,
        columns: ColumnMap | None = None,
        license: LicenseInfo | None = None,
    ) -> None:
        self.path = path
        self.calendar = calendar
        self.symbol = normalize_symbol(symbol) if symbol else None
        try:
            self.timezone = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ProviderError(f"unknown timezone {timezone!r}") from exc
        self.timestamp_is_bar_start = timestamp_is_bar_start
        self.columns = columns or ColumnMap()
        self.license = license or LicenseInfo(
            "user-supplied file; licence not stated", redistribution_allowed=False
        )

    def _read(self) -> pd.DataFrame:
        if not self.path.is_file():
            raise ProviderError(f"file not found: {self.path}")
        try:
            if self.path.suffix.lower() == ".parquet":
                return pd.read_parquet(self.path)
            return pd.read_csv(
                self.path, dtype=str, keep_default_na=False, na_values=["", "NaN", "nan", "null"]
            )
        except Exception as exc:  # pandas raises many parser-specific types
            raise ProviderError(f"could not read {self.path}: {exc}") from exc

    def _timestamps(self, raw: pd.Series, resolution: Resolution) -> tuple[pd.Series, list[str]]:
        notes: list[str] = []
        text = raw.astype(str).str.strip()
        date_only = text.str.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}|\d{4}/\d{2}/\d{2}").all()
        if resolution is Resolution.DAILY and date_only:
            dates = pd.to_datetime(text, errors="coerce", format="mixed")
            if dates.isna().any():
                raise ProviderError(f"unparseable dates, e.g. {text[dates.isna()].iloc[0]!r}")
            try:
                ends = [session_close_utc(self.calendar, d.date()) for d in dates]
            except ValueError as exc:
                raise ProviderError(f"date is not a trading session of {self.calendar}: {exc}") from exc
            notes.append(f"daily dates mapped to {self.calendar} session close (UTC)")
            return pd.Series(pd.DatetimeIndex(ends), index=raw.index), notes
        parsed = pd.to_datetime(text, errors="coerce", format="mixed", utc=False)
        if parsed.isna().any():
            raise ProviderError(f"unparseable timestamps, e.g. {text[parsed.isna()].iloc[0]!r}")
        if parsed.dt.tz is None:
            parsed = parsed.dt.tz_localize(self.timezone, ambiguous="raise", nonexistent="raise")
            notes.append(f"naive timestamps interpreted as {self.timezone.key}")
        parsed = parsed.dt.tz_convert("UTC")
        if self.timestamp_is_bar_start:
            parsed = parsed + pd.Timedelta(seconds=resolution.seconds)
            notes.append("bar start times shifted to bar end")
        return parsed, notes

    def fetch_bars(self, request: BarRequest) -> RawBars:
        df = self._read()
        cols = _resolve_columns([str(c) for c in df.columns], self.columns)
        notes: list[str] = []
        if any(c.strip().lower() in _ADJUSTED_HINTS for c in map(str, df.columns)):
            notes.append(
                "file contains an adjusted-close column; it was ignored (declared adjustment applies)"
            )

        if cols["symbol"] is not None:
            symbols = df[cols["symbol"]].astype(str).map(normalize_symbol)
            if self.symbol is not None:
                symbols = symbols.where(symbols == self.symbol)
                df, symbols = df[symbols.notna()], symbols.dropna()
        elif self.symbol is not None:
            symbols = pd.Series(self.symbol, index=df.index)
        else:
            raise ProviderError("file has no symbol column; pass a symbol")

        ts, ts_notes = self._timestamps(df[cols["ts"]], request.resolution)
        notes += ts_notes
        out = pd.DataFrame({"symbol": symbols.to_numpy(), "ts": ts.reset_index(drop=True).array})
        for col in ("open", "high", "low", "close", "volume"):
            source = cols[col]
            if source is None:
                out[col] = float("nan")
                notes.append("no volume column; volume set to NaN")
                continue
            values = pd.to_numeric(df[source].astype(str).str.replace(",", "", regex=False), errors="coerce")
            out[col] = values.astype("float64").values
        out["ts"] = pd.to_datetime(out["ts"], utc=True)
        if request.symbols:
            out = out[out["symbol"].isin(request.symbols)]
        if request.start is not None:
            out = out[out["ts"] >= pd.Timestamp(request.start)]
        if request.end is not None:
            out = out[out["ts"] <= pd.Timestamp(request.end)]
        return RawBars(out[list(BAR_COLUMNS)].reset_index(drop=True), f"file:{self.path.name}", tuple(notes))


_ACTION_ALIASES = {
    "symbol": ("symbol", "ticker"),
    "ex_date": ("ex_date", "exdate", "ex-date", "date"),
    "kind": ("kind", "type", "action"),
    "value": ("value", "ratio", "amount", "dividend"),
}


def load_actions_csv(path: Path, symbol: str | None = None) -> pd.DataFrame:
    """Read a corporate-actions CSV (columns: [symbol], ex_date, kind = split|dividend, value).

    ``value``: split ratio as new shares per old (4 for 4:1) or cash dividend per share in raw terms.
    """
    from quantlab.data.corporate_actions import CorporateActionError, normalize_actions

    if not path.is_file():
        raise ProviderError(f"file not found: {path}")
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    lowered = {c.strip().lower(): c for c in raw.columns}
    columns = {
        k: next((lowered[a] for a in aliases if a in lowered), None) for k, aliases in _ACTION_ALIASES.items()
    }
    missing = [k for k in ("ex_date", "kind", "value") if columns[k] is None]
    if missing:
        raise ProviderError(f"corporate actions file needs columns {missing}; found {list(raw.columns)}")
    if columns["symbol"] is None and symbol is None:
        raise ProviderError("corporate actions file has no symbol column; pass a symbol")
    frame = pd.DataFrame(
        {
            "symbol": raw[columns["symbol"]] if columns["symbol"] else normalize_symbol(symbol or ""),
            "ex_date": raw[str(columns["ex_date"])],
            "kind": raw[str(columns["kind"])],
            "value": raw[str(columns["value"])],
        }
    )
    if symbol is not None:
        frame = frame[frame["symbol"].astype(str).str.upper() == normalize_symbol(symbol)]
    try:
        return normalize_actions(frame)
    except CorporateActionError as exc:
        raise ProviderError(f"{path.name}: {exc}") from exc
