"""Performance metrics from an equity curve (AN-001, equity-based subset).

The single implementation of these metrics in QuantLab: UI, optimizer and reports must call this
module, never re-derive formulas (MODULE_OWNERSHIP.md).

Conventions (documented so results are interpretable and reproducible):
* The equity curve is resampled to **one value per UTC calendar date** (last observation of the day).
* Returns are simple daily returns between consecutive resampled values.
* Annualisation uses ``periods_per_year`` (default 252) for volatility/Sharpe/Sortino and calendar
  time (days / 365.25) for CAGR.
* ``risk_free_rate`` is an annual rate converted to a per-period rate as ``(1 + rf) ** (1/periods) - 1``.
* Drawdowns are measured on the resampled curve; duration is in calendar days from peak to recovery
  (or to the last observation when not recovered).

Trade-based metrics (win rate, profit factor, expectancy, costs) need fill-level data and are not
computed here yet (see IMPLEMENTATION_STATUS AN-001).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Any


class MetricsError(ValueError):
    pass


@dataclass(frozen=True)
class EquityMetrics:
    start: str
    end: str
    observations: int  # daily points after resampling
    start_equity: float
    end_equity: float
    total_return: float
    cagr: float | None
    annual_volatility: float | None
    sharpe: float | None
    sortino: float | None
    max_drawdown: float  # positive fraction, e.g. 0.12 for -12 %
    max_drawdown_duration_days: int
    calmar: float | None
    best_day: float | None
    worst_day: float | None
    periods_per_year: int
    risk_free_rate: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def daily_series(points: Iterable[tuple[datetime, float]]) -> list[tuple[date, float]]:
    """Resample to the last value per UTC date.

    Rejects naive timestamps and non-positive or non-finite equity values.
    """
    by_day: dict[date, tuple[datetime, float]] = {}
    for ts, value in points:
        if ts.tzinfo is None:
            raise MetricsError("equity timestamps must be timezone-aware")
        if not math.isfinite(value) or value <= 0:
            raise MetricsError(f"equity must be positive and finite, got {value!r} at {ts.isoformat()}")
        day = ts.astimezone(UTC).date()
        current = by_day.get(day)
        if current is None or ts >= current[0]:
            by_day[day] = (ts, value)
    return [(d, by_day[d][1]) for d in sorted(by_day)]


def simple_returns(values: Sequence[float]) -> list[float]:
    return [values[i] / values[i - 1] - 1.0 for i in range(1, len(values))]


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs)


def _stdev(xs: Sequence[float]) -> float | None:
    """Sample standard deviation (n - 1)."""
    if len(xs) < 2:
        return None
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def drawdown_stats(days: Sequence[date], values: Sequence[float]) -> tuple[float, int]:
    """Max drawdown (positive fraction) and the longest peak-to-recovery duration in calendar days."""
    peak_value, peak_day = values[0], days[0]
    max_dd, longest = 0.0, 0
    for day, value in zip(days, values, strict=True):
        if value >= peak_value:
            longest = max(longest, (day - peak_day).days)
            peak_value, peak_day = value, day
        else:
            max_dd = max(max_dd, 1.0 - value / peak_value)
    if values[-1] < peak_value:  # still under water at the end
        longest = max(longest, (days[-1] - peak_day).days)
    return max_dd, longest


def equity_metrics(
    points: Iterable[tuple[datetime, float]],
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> EquityMetrics:
    series = daily_series(points)
    if len(series) < 2:
        raise MetricsError("at least two daily equity observations are required")
    days = [d for d, _ in series]
    values = [v for _, v in series]
    rets = simple_returns(values)

    total_return = values[-1] / values[0] - 1.0
    years = (days[-1] - days[0]).days / 365.25
    cagr = (values[-1] / values[0]) ** (1.0 / years) - 1.0 if years > 0 else None

    rf_period = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    excess = [r - rf_period for r in rets]
    sd = _stdev(rets)
    annual_vol = sd * math.sqrt(periods_per_year) if sd is not None else None
    sharpe = _mean(excess) / sd * math.sqrt(periods_per_year) if sd is not None and sd > 0 else None
    downside = math.sqrt(_mean([min(x, 0.0) ** 2 for x in excess])) if excess else 0.0
    sortino = _mean(excess) / downside * math.sqrt(periods_per_year) if downside > 0 else None

    max_dd, dd_days = drawdown_stats(days, values)
    calmar = cagr / max_dd if cagr is not None and max_dd > 0 else None

    return EquityMetrics(
        start=days[0].isoformat(),
        end=days[-1].isoformat(),
        observations=len(values),
        start_equity=values[0],
        end_equity=values[-1],
        total_return=total_return,
        cagr=cagr,
        annual_volatility=annual_vol,
        sharpe=sharpe,
        sortino=sortino,
        max_drawdown=max_dd,
        max_drawdown_duration_days=dd_days,
        calmar=calmar,
        best_day=max(rets) if rets else None,
        worst_day=min(rets) if rets else None,
        periods_per_year=periods_per_year,
        risk_free_rate=risk_free_rate,
    )


@dataclass(frozen=True)
class CrossCheck:
    """Comparison of a QuantLab metric with the engine's own statistic."""

    metric: str
    engine_statistic: str
    quantlab: float | None
    engine: float | None
    tolerance: float
    status: str  # "match" | "mismatch" | "unavailable"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# QuantLab metric -> (engine statistic name, absolute tolerance). Tolerances cover LEAN's rounding to
# 3 decimals of a percentage plus definition differences; provisional until calibrated (KI-0017).
CROSS_CHECKS: dict[str, tuple[str, float]] = {
    "total_return": ("Net Profit", 0.0005),
    "cagr": ("Compounding Annual Return", 0.005),
    "max_drawdown": ("Drawdown", 0.0015),
}


def cross_check(metrics: EquityMetrics, engine_values: dict[str, float | None]) -> list[CrossCheck]:
    """Compare against engine statistics already converted to floats (fractions for percentages)."""
    checks = []
    for metric, (stat, tolerance) in CROSS_CHECKS.items():
        ours = getattr(metrics, metric)
        theirs = engine_values.get(stat)
        if ours is None or theirs is None:
            status = "unavailable"
        else:
            status = "match" if abs(ours - theirs) <= tolerance else "mismatch"
        checks.append(CrossCheck(metric, stat, ours, theirs, tolerance, status))
    return checks
