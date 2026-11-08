"""Return statistics for research (RES-004). Pure pandas functions; no QuantLab dependencies.

Conventions match ``quantlab.analytics.metrics``: simple returns by default, sample standard deviation
(n - 1), annualisation by ``periods_per_year`` (252 for daily bars). VaR / expected shortfall are
*historical* (empirical quantiles) and reported as positive loss fractions.
"""

from __future__ import annotations

import math
from typing import Any, Literal

import numpy as np
import pandas as pd


class StatisticsError(ValueError):
    pass


def returns(prices: pd.DataFrame | pd.Series, kind: Literal["simple", "log"] = "simple") -> Any:
    """Period returns of a price series or a wide price frame (rows = time). First row is dropped."""
    if kind not in ("simple", "log"):
        raise StatisticsError("kind must be 'simple' or 'log'")
    if (prices <= 0).to_numpy().any():
        raise StatisticsError("prices must be positive")
    result = np.log(prices / prices.shift(1)) if kind == "log" else prices.pct_change(fill_method=None)
    return result.iloc[1:]


def distribution(series: pd.Series, periods_per_year: int = 252, var_level: float = 0.95) -> dict[str, float]:
    """Moments, quantiles and historical tail risk of one return series (NaNs dropped)."""
    r = pd.Series(series, dtype="float64").dropna()
    if len(r) < 3:
        raise StatisticsError("at least three observations are required")
    if not 0.5 < var_level < 1:
        raise StatisticsError("var_level must be between 0.5 and 1")
    std = float(r.std(ddof=1))
    tail_q = float(r.quantile(1 - var_level))
    tail = r[r <= tail_q]
    return {
        "count": float(len(r)),
        "mean": float(r.mean()),
        "std": std,
        "annual_mean": float(r.mean()) * periods_per_year,
        "annual_volatility": std * math.sqrt(periods_per_year),
        "skew": float(r.skew()),
        "excess_kurtosis": float(r.kurt()),  # pandas: Fisher definition (normal = 0)
        "min": float(r.min()),
        "q01": float(r.quantile(0.01)),
        "q05": float(r.quantile(0.05)),
        "median": float(r.median()),
        "q95": float(r.quantile(0.95)),
        "q99": float(r.quantile(0.99)),
        "max": float(r.max()),
        "positive_fraction": float((r > 0).mean()),
        f"var_{int(var_level * 100)}": -tail_q,
        f"es_{int(var_level * 100)}": -float(tail.mean()),
    }


def correlation(
    frame: pd.DataFrame, method: Literal["pearson", "spearman"] = "pearson", min_periods: int = 20
) -> pd.DataFrame:
    """Pairwise correlation of a wide return frame (pairwise-complete observations)."""
    if method not in ("pearson", "spearman"):
        raise StatisticsError("method must be 'pearson' or 'spearman'")
    return frame.corr(method=method, min_periods=min_periods)


def rolling_volatility(
    series: pd.Series | pd.DataFrame, window: int = 21, periods_per_year: int = 252
) -> Any:
    """Annualised rolling volatility (sample std over ``window`` periods)."""
    if window < 2:
        raise StatisticsError("window must be at least 2")
    return series.rolling(window).std(ddof=1) * math.sqrt(periods_per_year)
