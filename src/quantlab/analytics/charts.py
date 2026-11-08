"""Report datasets for a backtest (AN-002) and benchmark comparison (BT-008).

The single implementation of chart/report series in QuantLab (MODULE_OWNERSHIP.md): the API, the web UI and
the downloadable report all serve what ``chart_datasets`` returns and never recompute it.

Conventions (in addition to ``metrics``):
* Every series is built on the daily-resampled equity curve (``metrics.daily_series``: last value per UTC
  date).
* Drawdown = value / running peak - 1 (0 or negative).
* Monthly / annual returns chain the last value of each period to the last value of the previous period; the
  first period is measured from the first observation (so a partial first month is a partial-month return).
* Rolling Sharpe uses ``window`` daily returns, annualised with ``periods_per_year``; ``None`` until the
  window is full or when volatility is zero.
* Trades are FIFO round trips reconstructed from *filled orders* (quantity and the order's reported price);
  they are **gross of fees and slippage** (fees are not in the order records) and labelled so.
* Benchmark statistics use the dates both series have; alpha/beta are CAPM-style on simple daily returns,
  alpha annualised by ``periods_per_year``; tracking error and information ratio use the active return.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from typing import Any

from quantlab.analytics.metrics import MetricsError, _mean, _stdev, daily_series, simple_returns

Series = list[tuple[date, float]]


@dataclass(frozen=True)
class Fill:
    time: datetime
    symbol: str
    quantity: float  # signed: + buy, - sell
    price: float


@dataclass(frozen=True)
class RoundTrip:
    symbol: str
    direction: str  # "long" | "short"
    quantity: float  # absolute
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl: float  # gross, account currency
    return_pct: float  # pnl / (entry_price * quantity)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["entry_time"], d["exit_time"] = self.entry_time.isoformat(), self.exit_time.isoformat()
        return d


def drawdown_series(series: Series) -> Series:
    out: Series = []
    peak = -math.inf
    for day, value in series:
        peak = max(peak, value)
        out.append((day, value / peak - 1.0))
    return out


def _period_returns(series: Series, key: Any) -> list[tuple[Any, float]]:
    last_by_period: dict[Any, float] = {}
    for day, value in series:
        last_by_period[key(day)] = value
    out = []
    previous = series[0][1]
    for period, value in last_by_period.items():
        out.append((period, value / previous - 1.0))
        previous = value
    return out


def monthly_returns(series: Series) -> list[dict[str, Any]]:
    return [
        {"year": y, "month": m, "return": r}
        for (y, m), r in _period_returns(series, lambda d: (d.year, d.month))
    ]


def annual_returns(series: Series) -> list[dict[str, Any]]:
    return [{"year": y, "return": r} for y, r in _period_returns(series, lambda d: d.year)]


def rolling_sharpe(
    series: Series, window: int = 63, periods_per_year: int = 252, risk_free_rate: float = 0.0
) -> list[tuple[date, float | None]]:
    if window < 2:
        raise MetricsError("rolling window must be at least 2 returns")
    rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    rets = simple_returns([v for _, v in series])
    out: list[tuple[date, float | None]] = []
    for i, (day, _) in enumerate(series[1:], start=1):
        chunk = rets[max(0, i - window) : i]
        value = None
        if len(chunk) == window:
            sd = _stdev(chunk)
            if sd:
                value = (_mean(chunk) - rf) / sd * math.sqrt(periods_per_year)
        out.append((day, value))
    return out


def round_trips(fills: Iterable[Fill]) -> list[RoundTrip]:
    """FIFO matching per symbol; a fill that flips the position closes the old lots and opens new ones."""
    lots: dict[str, deque[list[Any]]] = defaultdict(deque)  # symbol -> [signed qty, price, time]
    trips: list[RoundTrip] = []
    for fill in sorted(fills, key=lambda f: f.time):
        remaining = fill.quantity
        book = lots[fill.symbol]
        while remaining and book and (book[0][0] > 0) != (remaining > 0):
            lot = book[0]
            closed = min(abs(lot[0]), abs(remaining))
            long = lot[0] > 0
            pnl = (fill.price - lot[1]) * closed * (1 if long else -1)
            trips.append(
                RoundTrip(
                    symbol=fill.symbol,
                    direction="long" if long else "short",
                    quantity=closed,
                    entry_time=lot[2],
                    exit_time=fill.time,
                    entry_price=lot[1],
                    exit_price=fill.price,
                    pnl=pnl,
                    return_pct=pnl / (lot[1] * closed) if lot[1] else 0.0,
                )
            )
            lot[0] += closed if lot[0] < 0 else -closed
            remaining += closed if remaining < 0 else -closed
            if math.isclose(lot[0], 0.0, abs_tol=1e-12):
                book.popleft()
        if remaining and not math.isclose(remaining, 0.0, abs_tol=1e-12):
            book.append([remaining, fill.price, fill.time])
    return trips


def trade_stats(trips: Sequence[RoundTrip]) -> dict[str, Any]:
    wins = [t.pnl for t in trips if t.pnl > 0]
    losses = [t.pnl for t in trips if t.pnl < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    holding = [(t.exit_time - t.entry_time).total_seconds() / 86400 for t in trips]
    return {
        "basis": "FIFO round trips from filled orders; gross of fees and slippage",
        "trades": len(trips),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / len(trips) if trips else None,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else None,
        "expectancy": sum(t.pnl for t in trips) / len(trips) if trips else None,
        "average_win": gross_win / len(wins) if wins else None,
        "average_loss": -gross_loss / len(losses) if losses else None,
        "largest_win": max(wins) if wins else None,
        "largest_loss": min(losses) if losses else None,
        "average_holding_days": sum(holding) / len(holding) if holding else None,
        "net_pnl": sum(t.pnl for t in trips),
    }


@dataclass(frozen=True)
class BenchmarkStats:
    name: str
    common_days: int
    strategy_return: float
    benchmark_return: float
    excess_return: float
    beta: float | None
    alpha_annual: float | None
    correlation: float | None
    tracking_error: float | None
    information_ratio: float | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def benchmark_comparison(
    strategy: Series,
    benchmark: Series,
    *,
    name: str,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> BenchmarkStats:
    bench = dict(benchmark)
    common = [(d, v, bench[d]) for d, v in strategy if d in bench]
    if len(common) < 3:
        raise MetricsError(f"benchmark {name!r} shares fewer than 3 dates with the strategy")
    rs = simple_returns([v for _, v, _ in common])
    rb = simple_returns([b for _, _, b in common])
    rf = (1.0 + risk_free_rate) ** (1.0 / periods_per_year) - 1.0
    mean_s, mean_b = _mean(rs), _mean(rb)
    var_b = sum((x - mean_b) ** 2 for x in rb) / (len(rb) - 1)
    cov = sum((x - mean_s) * (y - mean_b) for x, y in zip(rs, rb, strict=True)) / (len(rb) - 1)
    sd_s, sd_b = _stdev(rs), _stdev(rb)
    beta = cov / var_b if var_b > 0 else None
    alpha = ((mean_s - rf) - beta * (mean_b - rf)) * periods_per_year if beta is not None else None
    corr = cov / (sd_s * sd_b) if sd_s and sd_b else None
    active = [x - y for x, y in zip(rs, rb, strict=True)]
    sd_active = _stdev(active)
    te = sd_active * math.sqrt(periods_per_year) if sd_active is not None else None
    ir = _mean(active) * periods_per_year / te if te else None
    s_ret = common[-1][1] / common[0][1] - 1.0
    b_ret = common[-1][2] / common[0][2] - 1.0
    return BenchmarkStats(
        name=name,
        common_days=len(common),
        strategy_return=s_ret,
        benchmark_return=b_ret,
        excess_return=s_ret - b_ret,
        beta=beta,
        alpha_annual=alpha,
        correlation=corr,
        tracking_error=te,
        information_ratio=ir,
    )


def _points(series: Iterable[tuple[date, float | None]]) -> list[list[Any]]:
    return [[d.isoformat(), v] for d, v in series]


def chart_datasets(
    equity: Iterable[tuple[datetime, float]],
    fills: Iterable[Fill] = (),
    *,
    benchmark: Iterable[tuple[datetime, float]] | None = None,
    benchmark_name: str | None = None,
    rolling_window: int = 63,
    periods_per_year: int = 252,
    risk_free_rate: float = 0.0,
) -> dict[str, Any]:
    """Everything the run-detail page and the report draw, as JSON-ready data."""
    series = daily_series(equity)
    if len(series) < 2:
        raise MetricsError("at least two daily equity observations are required")
    trips = round_trips(fills)
    out: dict[str, Any] = {
        "conventions": {
            "resampling": "daily UTC last",
            "periods_per_year": periods_per_year,
            "risk_free_rate": risk_free_rate,
            "rolling_window": rolling_window,
        },
        "equity": _points(series),
        "drawdown": _points(drawdown_series(series)),
        "monthly_returns": monthly_returns(series),
        "annual_returns": annual_returns(series),
        "rolling_sharpe": _points(rolling_sharpe(series, rolling_window, periods_per_year, risk_free_rate)),
        "trades": [t.to_dict() for t in trips],
        "trade_stats": trade_stats(trips),
        "benchmark": None,
    }
    if benchmark is not None:
        name = benchmark_name or "benchmark"
        bench = daily_series(benchmark)
        stats = benchmark_comparison(
            series, bench, name=name, periods_per_year=periods_per_year, risk_free_rate=risk_free_rate
        )
        bench_map = dict(bench)
        first_common = next(d for d, _ in series if d in bench_map)
        scale = dict(series)[first_common] / bench_map[first_common]
        last_day = series[-1][0]
        out["benchmark"] = {
            "name": name,
            "stats": stats.to_dict(),
            # benchmark rebased to the strategy's equity on the first common date, for overlay
            "equity": [[d.isoformat(), v * scale] for d, v in bench if first_common <= d <= last_day],
            "rebased_from": first_common.isoformat(),
        }
    return out
