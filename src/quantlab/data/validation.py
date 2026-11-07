"""Bar validation (DATA-005).

Severity policy:
* **error** — the batch is rejected and quarantined (never partially imported, never silently fixed):
  non-finite or non-positive prices, OHLC inconsistencies, negative volume, conflicting duplicates,
  bars on non-session days (daily data).
* **warning** — recorded in the version's completeness report: exact duplicates (removed), missing
  sessions (gaps), suspicious jumps, stale prices, missing volume.

Gap detection uses the dataset calendar and only checks *within* each symbol's first/last bar, so a
delisted or newly listed symbol is not reported as incomplete outside its trading life.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np
import pandas as pd

from quantlab.data.calendars import session_dates, sessions
from quantlab.data.schema import BAR_COLUMNS, PRICE_COLUMNS, Resolution

MAX_EXAMPLES = 10


@dataclass(frozen=True)
class Issue:
    severity: str  # "error" | "warning"
    check: str
    symbol: str | None
    count: int
    message: str
    examples: tuple[str, ...] = ()


@dataclass
class ValidationReport:
    rows_in: int
    rows_out: int = 0
    issues: list[Issue] = field(default_factory=list)
    completeness: dict[str, float] = field(default_factory=dict)  # symbol -> sessions present / expected

    @property
    def ok(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            "issues": [asdict(i) for i in self.issues],
            "completeness": self.completeness,
        }


def _examples(frame: pd.DataFrame) -> tuple[str, ...]:
    return tuple(f"{r.symbol}@{r.ts.isoformat()}" for r in frame.head(MAX_EXAMPLES).itertuples())


def validate_bars(
    frame: pd.DataFrame,
    resolution: Resolution,
    calendar: str,
    *,
    max_abs_return: float = 0.5,
    stale_run: int = 5,
) -> tuple[pd.DataFrame, ValidationReport]:
    """Validate a canonical frame. Returns (cleaned frame sorted by symbol/ts, report)."""
    missing_cols = [c for c in BAR_COLUMNS if c not in frame.columns]
    if missing_cols:
        raise ValueError(f"frame is missing canonical columns {missing_cols}")
    report = ValidationReport(rows_in=len(frame))
    df = frame[list(BAR_COLUMNS)].copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    df = df.sort_values(["symbol", "ts"], kind="stable").reset_index(drop=True)
    add = report.issues.append

    if df.empty:
        add(Issue("error", "empty", None, 0, "no rows to import"))
        return df, report

    prices = df[list(PRICE_COLUMNS)]
    bad = ~np.isfinite(prices).all(axis=1) | (prices <= 0).any(axis=1)
    if bad.any():
        add(
            Issue(
                "error",
                "price_values",
                None,
                int(bad.sum()),
                "non-finite or non-positive prices",
                _examples(df[bad]),
            )
        )
    o, h, lo, c = df["open"], df["high"], df["low"], df["close"]
    inconsistent = ~bad & ((h < np.maximum(o, c)) | (lo > np.minimum(o, c)) | (h < lo))
    if inconsistent.any():
        add(
            Issue(
                "error",
                "ohlc_consistency",
                None,
                int(inconsistent.sum()),
                "high below open/close or low above open/close",
                _examples(df[inconsistent]),
            )
        )
    if (df["volume"] < 0).any():
        neg = df["volume"] < 0
        add(Issue("error", "volume_negative", None, int(neg.sum()), "negative volume", _examples(df[neg])))
    if df["volume"].isna().any():
        add(Issue("warning", "volume_missing", None, int(df["volume"].isna().sum()), "volume missing (NaN)"))

    dup_key = df.duplicated(["symbol", "ts"], keep=False)
    if dup_key.any():
        exact = df.duplicated(keep="first")
        conflicting = dup_key & ~df.duplicated(list(BAR_COLUMNS), keep=False)
        if conflicting.any():
            add(
                Issue(
                    "error",
                    "duplicate_conflict",
                    None,
                    int(conflicting.sum()),
                    "same symbol/timestamp with different values",
                    _examples(df[conflicting]),
                )
            )
        if exact.any():
            add(Issue("warning", "duplicate_exact", None, int(exact.sum()), "exact duplicate rows removed"))
            df = df[~exact].reset_index(drop=True)

    for symbol, group in df.groupby("symbol", sort=True):
        sym = str(symbol)
        closes = group["close"].to_numpy()
        if len(closes) > 1 and np.isfinite(closes).all() and (closes > 0).all():
            jumps = np.abs(np.diff(np.log(closes))) > np.log1p(max_abs_return)
            if jumps.any():
                add(
                    Issue(
                        "warning",
                        "price_jump",
                        sym,
                        int(jumps.sum()),
                        f"close-to-close move larger than {max_abs_return:.0%} (split or bad tick?)",
                        _examples(group.iloc[1:][jumps]),
                    )
                )
            run = 1
            stale = 0
            for a, b in pairwise(closes):
                run = run + 1 if a == b else 1
                if run == stale_run:
                    stale += 1
            if stale:
                add(
                    Issue("warning", "stale_prices", sym, stale, f"{stale_run}+ identical consecutive closes")
                )

        if resolution is Resolution.DAILY and calendar != "none":
            session_close_dates = session_dates(group["ts"], calendar, Resolution.DAILY)
            expected = sessions(calendar, session_close_dates.min(), session_close_dates.max())
            present = set(session_close_dates)
            off_session = sorted(present - set(expected))
            if off_session:
                add(
                    Issue(
                        "error",
                        "non_session_bar",
                        sym,
                        len(off_session),
                        f"bars on days that are not {calendar} sessions",
                        tuple(d.isoformat() for d in off_session[:MAX_EXAMPLES]),
                    )
                )
            missing = [d for d in expected if d not in present]
            if missing:
                add(
                    Issue(
                        "warning",
                        "missing_sessions",
                        sym,
                        len(missing),
                        f"missing {calendar} sessions",
                        tuple(d.isoformat() for d in missing[:MAX_EXAMPLES]),
                    )
                )
            report.completeness[sym] = (
                round(len(present & set(expected)) / len(expected), 6) if expected else 1.0
            )

    report.rows_out = len(df)
    return df, report
