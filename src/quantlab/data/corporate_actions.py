"""Corporate actions: splits and cash dividends (DATA-008).

Canonical actions frame::

    symbol   str     ticker (upper case)
    ex_date  date    ex-date in exchange-local calendar terms
    kind     str     "split" | "dividend"
    value    float   split: new shares per old share (4.0 for 4:1); dividend: cash per share (raw)

One factor computation serves both consumers, so research and backtests can never disagree:

* LEAN factor files (``factor_file_rows``) — LEAN semantics, verified against LEAN's own AAPL factor file:
  a row dated ``d`` (the session *before* an event) holds the cumulative product of the factors of that
  event and every later one, and applies to all bars dated ``<= d``; dividend factor
  ``(close_d - dividend) / close_d`` with raw values; split factor ``1 / ratio``; the file ends with
  ``20501231,1,1,0`` and starts at the first data date.
* read-time adjustment (``adjust_bars``): adjusted = raw x price_factor x split_factor (``split_dividend``) or
  raw x split_factor (``split``); volume / split_factor.

Adjustments are applied only when *reading*; stored prices stay raw, so there is no look-ahead in stored data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from typing import Literal

import numpy as np
import pandas as pd

ACTION_COLUMNS = ("symbol", "ex_date", "kind", "value")
KINDS = ("split", "dividend")
FAR_FUTURE = date(2050, 12, 31)
Adjustment = Literal["raw", "split", "split_dividend"]


class CorporateActionError(ValueError):
    pass


@dataclass(frozen=True)
class FactorRow:
    date: date
    price_factor: float
    split_factor: float
    reference_price: float

    def lean_line(self) -> str:
        ref = "0" if self.date == FAR_FUTURE else _num(self.reference_price)
        return f"{self.date:%Y%m%d},{_num(self.price_factor)},{_num(self.split_factor)},{ref}"


def _num(value: float) -> str:
    text = f"{value:.8f}".rstrip("0").rstrip(".")
    return text or "0"


def empty_actions() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": pd.Series(dtype="str"),
            "ex_date": pd.Series(dtype="object"),
            "kind": pd.Series(dtype="str"),
            "value": pd.Series(dtype="float64"),
        }
    )


def normalize_actions(frame: pd.DataFrame) -> pd.DataFrame:
    """Canonical dtypes and order; rejects unknown kinds and non-positive values."""
    missing = [c for c in ACTION_COLUMNS if c not in frame.columns]
    if missing:
        raise CorporateActionError(f"actions are missing columns {missing}")
    out = frame[list(ACTION_COLUMNS)].copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["kind"] = out["kind"].astype(str).str.strip().str.lower()
    out["ex_date"] = pd.to_datetime(out["ex_date"], errors="coerce").dt.date
    out["value"] = pd.to_numeric(out["value"], errors="coerce").astype("float64")
    if out["ex_date"].isna().any():
        raise CorporateActionError("unparseable ex_date in corporate actions")
    bad_kind = sorted(set(out["kind"]) - set(KINDS))
    if bad_kind:
        raise CorporateActionError(f"unknown corporate action kinds {bad_kind}; expected {KINDS}")
    if not np.isfinite(out["value"]).all() or (out["value"] <= 0).any():
        raise CorporateActionError("corporate action values must be positive numbers")
    return out.sort_values(["symbol", "ex_date", "kind"], kind="stable").reset_index(drop=True)


def actions_hash(actions: pd.DataFrame) -> str | None:
    """Content hash of the actions (None when there are none, keeping older version hashes unchanged)."""
    if actions.empty:
        return None
    digest = hashlib.sha256()
    for row in normalize_actions(actions).itertuples(index=False):
        digest.update(f"{row.symbol}|{row.ex_date.isoformat()}|{row.kind}|{float(row.value)!r}\n".encode())
    return digest.hexdigest()


def validate_actions(actions: pd.DataFrame, sessions_by_symbol: dict[str, pd.Series]) -> list[str]:
    """Problems that make actions unusable.

    ``sessions_by_symbol``: raw closes per symbol, indexed by session date (ascending).
    """
    problems: list[str] = []
    dup = actions.duplicated(["symbol", "ex_date", "kind"], keep=False)
    if dup.any():
        problems.append(f"{int(dup.sum())} duplicate actions (same symbol, ex_date and kind)")
    for row in actions.itertuples(index=False):
        closes = sessions_by_symbol.get(row.symbol)
        label = f"{row.kind} {row.symbol} {row.ex_date.isoformat()}"
        if closes is None or closes.empty:
            problems.append(f"{label}: symbol has no bars")
            continue
        before = closes[closes.index < row.ex_date]
        if before.empty:
            problems.append(f"{label}: no bar before the ex-date (needs the previous close)")
            continue
        if row.ex_date > closes.index[-1]:
            problems.append(f"{label}: ex-date after the last bar")
        if row.kind == "dividend" and row.value >= before.iloc[-1]:
            problems.append(
                f"{label}: dividend {row.value} is not below the previous close {before.iloc[-1]}"
            )
    return problems


def factor_file_rows(closes: pd.Series, actions: pd.DataFrame) -> list[FactorRow]:
    """LEAN factor rows for one symbol. ``closes``: raw closes indexed by session date, ascending."""
    if closes.empty:
        raise CorporateActionError("no closes")
    first_day = closes.index[0]
    events: dict[date, tuple[float, float, float]] = {}  # prev session -> (price mult, split mult, ref close)
    for row in actions.sort_values("ex_date").itertuples(index=False):
        before = closes[closes.index < row.ex_date]
        if before.empty:
            raise CorporateActionError(f"{row.kind} on {row.ex_date}: no bar before the ex-date")
        prev_day, prev_close = before.index[-1], float(before.iloc[-1])
        price_mult, split_mult, _ = events.get(prev_day, (1.0, 1.0, prev_close))
        if row.kind == "dividend":
            price_mult *= (prev_close - float(row.value)) / prev_close
        else:
            split_mult *= 1.0 / float(row.value)
        events[prev_day] = (price_mult, split_mult, prev_close)

    rows: list[FactorRow] = [FactorRow(FAR_FUTURE, 1.0, 1.0, 0.0)]
    price_factor = split_factor = 1.0
    for day in sorted(events, reverse=True):
        price_mult, split_mult, ref = events[day]
        price_factor *= price_mult
        split_factor *= split_mult
        rows.append(FactorRow(day, price_factor, split_factor, ref))
    if rows[-1].date > first_day:
        rows.append(FactorRow(first_day, price_factor, split_factor, 1.0))
    return list(reversed(rows))


def factors_for_dates(days: pd.Series, rows: list[FactorRow]) -> tuple[np.ndarray, np.ndarray]:
    """(price_factor, split_factor) per session date: the first row whose date is >= the day."""
    row_days = np.array([r.date for r in rows], dtype="datetime64[D]")
    idx = np.searchsorted(row_days, np.array(list(days), dtype="datetime64[D]"), side="left")
    idx = np.minimum(idx, len(rows) - 1)
    price = np.array([rows[i].price_factor for i in idx])
    split = np.array([rows[i].split_factor for i in idx])
    return price, split


def adjust_bars(
    bars: pd.DataFrame, session_days: pd.Series, actions: pd.DataFrame, mode: Adjustment
) -> pd.DataFrame:
    """Copy of canonical bars adjusted with ``actions`` (``mode='raw'`` returns an unchanged copy)."""
    if mode not in ("raw", "split", "split_dividend"):
        raise CorporateActionError("adjustment must be 'raw', 'split' or 'split_dividend'")
    out = bars.copy()
    if mode == "raw" or actions.empty or bars.empty:
        return out
    out["_day"] = list(session_days)
    for symbol, group in out.groupby("symbol", sort=False):
        sym_actions = actions[actions["symbol"] == symbol]
        if sym_actions.empty:
            continue
        closes = group.drop_duplicates("_day", keep="last").set_index("_day")["close"].sort_index()
        rows = factor_file_rows(closes, sym_actions)
        price, split = factors_for_dates(group["_day"], rows)
        scale = price * split if mode == "split_dividend" else split
        for col in ("open", "high", "low", "close"):
            out.loc[group.index, col] = group[col].to_numpy() * scale
        out.loc[group.index, "volume"] = group["volume"].to_numpy() / split
    return out.drop(columns="_day")
