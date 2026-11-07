"""Multi-run comparison (AN-004): rebased equity curves, metrics side by side, return correlations.

Pure: callers pass each run's equity points and QuantLab metrics. Curves are rebased to 1.0 at each run's
first daily observation (different start dates stay visible); correlations use daily returns on the dates all
runs share.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

import numpy as np

from quantlab.analytics.metrics import MetricsError, daily_series, simple_returns

METRIC_KEYS = (
    "total_return", "cagr", "annual_volatility", "sharpe", "sortino", "calmar", "max_drawdown",
    "max_drawdown_duration_days", "start", "end",
)  # fmt: skip


def compare_runs(runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """``runs``: [{"id", "label", "equity": [(datetime, value)], "metrics", "provenance"}], two or more."""
    if len(runs) < 2:
        raise MetricsError("comparison needs at least two runs")
    curves: list[dict[str, Any]] = []
    returns: list[dict[Any, float]] = []
    for run in runs:
        series = daily_series(_points(run["equity"]))
        if len(series) < 2:
            raise MetricsError(f"run {run['id']} has fewer than two daily equity observations")
        base = series[0][1]
        curves.append(
            {"id": run["id"], "label": run["label"], "equity": [[d.isoformat(), v / base] for d, v in series]}
        )
        rets = simple_returns([v for _, v in series])
        returns.append({series[i + 1][0]: r for i, r in enumerate(rets)})
    common = sorted(set.intersection(*(set(r) for r in returns)))
    correlation = None
    if len(common) >= 3:
        matrix = np.array([[r[d] for d in common] for r in returns])
        with np.errstate(invalid="ignore", divide="ignore"):
            c = np.corrcoef(matrix)
        correlation = [[None if not np.isfinite(x) else float(x) for x in row] for row in c]
    return {
        "runs": [
            {
                "id": run["id"],
                "label": run["label"],
                "provenance": run.get("provenance"),
                "metrics": {k: (run.get("metrics") or {}).get(k) for k in METRIC_KEYS},
            }
            for run in runs
        ],
        "equity": curves,
        "common_days": len(common),
        "correlation": correlation,
        "conventions": (
            "equity rebased to 1.0 at each run's first daily observation; "
            "correlation of daily returns on common dates"
        ),
    }


def _points(raw: Iterable[Any]) -> list[tuple[datetime, float]]:
    out = []
    for t, v in raw:
        out.append((t if isinstance(t, datetime) else datetime.fromisoformat(str(t)), float(v)))
    return out
