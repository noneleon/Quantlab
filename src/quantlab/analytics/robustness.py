"""Robustness and overfitting statistics (OPT-004, OPT-005). Pure functions; seeded randomness only.

References (formulas implemented as published):
* Deflated Sharpe Ratio — Bailey, D. & López de Prado, M. (2014), "The Deflated Sharpe Ratio: Correcting for
  Selection Bias, Backtest Overfitting and Non-Normality", Journal of Portfolio Management 40(5).
  SR0 = sqrt(V[SR_n]) * ((1 - g) Φ⁻¹(1 - 1/N) + g Φ⁻¹(1 - 1/(N e))),
  DSR = Φ( (SR - SR0) * sqrt(T - 1) / sqrt(1 - g3 SR + (g4 - 1)/4 SR²) ), all Sharpe ratios per period;
  g = Euler-Mascheroni constant, g3/g4 = skewness/kurtosis of the selected returns.
* Probability of Backtest Overfitting via CSCV — Bailey, Borwein, López de Prado & Zhu (2017), "The
  Probability of
  Backtest Overfitting", Journal of Computational Finance 20(4). For every split of S blocks into equal
  IS/OOS halves,
  the best IS trial's OOS relative rank ω gives λ = ln(ω / (1 - ω)); PBO = P(λ ≤ 0).
* Stationary bootstrap — Politis, D. & Romano, J. (1994), JASA 89(428): blocks with geometric lengths (mean
  L).

Conventions: inputs are simple per-period returns (daily for QuantLab runs); annualisation uses
``periods_per_year``.
Results are descriptive statistics of *this* history and these trials — not predictions.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from itertools import combinations
from statistics import NormalDist
from typing import Any

import numpy as np

EULER_GAMMA = 0.5772156649015329
_N = NormalDist()


class RobustnessError(ValueError):
    pass


def _sharpe(returns: np.ndarray) -> float:
    sd = float(np.std(returns, ddof=1)) if len(returns) > 1 else 0.0
    return float(np.mean(returns)) / sd if sd > 0 else 0.0


def _moments(returns: np.ndarray) -> tuple[float, float]:
    """Skewness and (non-excess) kurtosis."""
    centred = returns - returns.mean()
    var = float(np.mean(centred**2))
    if var == 0:
        return 0.0, 3.0
    return float(np.mean(centred**3)) / var**1.5, float(np.mean(centred**4)) / var**2


@dataclass(frozen=True)
class DeflatedSharpe:
    sharpe: float  # per period, of the selected strategy
    sharpe_annual: float
    expected_max_sharpe: float  # SR0 per period: the best Sharpe expected from N trials of pure noise
    trials: int
    observations: int
    skewness: float
    kurtosis: float
    probability: float  # DSR: probability that the true Sharpe exceeds SR0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def deflated_sharpe(
    returns: Sequence[float], trial_sharpes: Sequence[float], periods_per_year: int = 252
) -> DeflatedSharpe:
    """DSR of the selected strategy's ``returns`` given the per-period Sharpe ratios of *all* trials tried."""
    r = np.asarray(returns, dtype=float)
    if len(r) < 3:
        raise RobustnessError("deflated Sharpe needs at least 3 returns")
    n = len(trial_sharpes)
    if n < 1:
        raise RobustnessError("deflated Sharpe needs the Sharpe ratios of the trials")
    sr = _sharpe(r)
    g3, g4 = _moments(r)
    if n == 1:
        sr0 = 0.0
    else:
        v = float(np.var(np.asarray(trial_sharpes, dtype=float), ddof=1))
        sr0 = math.sqrt(v) * (
            (1 - EULER_GAMMA) * _N.inv_cdf(1 - 1 / n) + EULER_GAMMA * _N.inv_cdf(1 - 1 / (n * math.e))
        )
    denom = 1 - g3 * sr + (g4 - 1) / 4 * sr**2
    z = (
        (sr - sr0) * math.sqrt(len(r) - 1) / math.sqrt(denom)
        if denom > 0
        else math.copysign(math.inf, sr - sr0)
    )
    return DeflatedSharpe(
        sharpe=sr,
        sharpe_annual=sr * math.sqrt(periods_per_year),
        expected_max_sharpe=sr0,
        trials=n,
        observations=len(r),
        skewness=g3,
        kurtosis=g4,
        probability=_N.cdf(z) if math.isfinite(z) else (1.0 if z > 0 else 0.0),
    )


@dataclass(frozen=True)
class PBOResult:
    pbo: float
    splits: int
    blocks: int
    trials: int
    observations: int
    logits: list[float]
    oos_rank_of_is_best_median: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def probability_of_backtest_overfitting(matrix: Sequence[Sequence[float]], blocks: int = 16) -> PBOResult:
    """CSCV on a T x N matrix of per-period returns (rows = periods, columns = trials)."""
    m = np.asarray(matrix, dtype=float)
    if m.ndim != 2 or m.shape[1] < 2:
        raise RobustnessError("PBO needs a T x N return matrix with at least 2 trials")
    t, n = m.shape
    s = min(blocks, (t // 2) // 2 * 2)  # at least 2 observations per block, even count
    if s < 2:
        raise RobustnessError(f"PBO needs at least 4 observations (got {t})")
    edges = np.linspace(0, t, s + 1).astype(int)
    parts = [m[edges[i] : edges[i + 1]] for i in range(s)]
    logits = []
    ranks = []
    for chosen in combinations(range(s), s // 2):
        is_rows = np.vstack([parts[i] for i in chosen])
        oos_rows = np.vstack([parts[i] for i in range(s) if i not in chosen])
        is_perf = np.array([_sharpe(is_rows[:, j]) for j in range(n)])
        oos_perf = np.array([_sharpe(oos_rows[:, j]) for j in range(n)])
        best = int(np.argmax(is_perf))
        rank = int(np.sum(oos_perf <= oos_perf[best]))  # 1..N, ascending
        omega = rank / (n + 1)
        logits.append(math.log(omega / (1 - omega)))
        ranks.append(omega)
    pbo = float(np.mean(np.asarray(logits) <= 0))
    return PBOResult(pbo, len(logits), s, n, t, logits, float(np.median(ranks)))


def _max_drawdown(equity: np.ndarray) -> float:
    peak = np.maximum.accumulate(equity)
    return float(np.max(1 - equity / peak))


def _percentiles(values: np.ndarray) -> dict[str, float]:
    return {f"p{q}": float(np.percentile(values, q)) for q in (5, 25, 50, 75, 95)}


def stationary_bootstrap(
    returns: Sequence[float],
    *,
    samples: int = 1000,
    mean_block: float = 5.0,
    seed: int = 0,
    periods_per_year: int = 252,
) -> dict[str, Any]:
    """Distributions of total return, annualised Sharpe and max drawdown under resampled return paths."""
    r = np.asarray(returns, dtype=float)
    t = len(r)
    if t < 10:
        raise RobustnessError("bootstrap needs at least 10 returns")
    if samples < 10 or mean_block < 1:
        raise RobustnessError("samples >= 10 and mean_block >= 1 required")
    rng = np.random.default_rng(seed)
    p = 1.0 / mean_block
    totals, sharpes, drawdowns = np.empty(samples), np.empty(samples), np.empty(samples)
    for k in range(samples):
        jumps = rng.random(t) < p
        jumps[0] = True  # a new block starts at a random position at time 0 and at every jump
        starts = rng.integers(t, size=t)
        positions = np.arange(t)
        block_start = np.maximum.accumulate(np.where(jumps, positions, 0))
        idx = (starts[block_start] + positions - block_start) % t
        path = r[idx]
        equity = np.cumprod(1 + path)
        totals[k] = equity[-1] - 1
        sharpes[k] = _sharpe(path) * math.sqrt(periods_per_year)
        drawdowns[k] = _max_drawdown(np.concatenate([[1.0], equity]))
    return {
        "method": "stationary bootstrap (Politis & Romano 1994)",
        "samples": samples,
        "mean_block": mean_block,
        "seed": seed,
        "observations": t,
        "total_return": _percentiles(totals),
        "sharpe_annual": _percentiles(sharpes),
        "max_drawdown": _percentiles(drawdowns),
        "probability_of_loss": float(np.mean(totals < 0)),
    }


def trade_monte_carlo(
    trade_pnls: Sequence[float], starting_equity: float, *, samples: int = 1000, seed: int = 0
) -> dict[str, Any]:
    """Order-shuffled (drawdown risk) and resampled-with-replacement (outcome range) trade sequences."""
    pnl = np.asarray(trade_pnls, dtype=float)
    if len(pnl) < 2:
        raise RobustnessError("trade Monte Carlo needs at least 2 round trips")
    if starting_equity <= 0:
        raise RobustnessError("starting equity must be positive")
    rng = np.random.default_rng(seed)
    shuffled_dd, resampled_final = np.empty(samples), np.empty(samples)
    for k in range(samples):
        order = rng.permutation(pnl)
        equity = starting_equity + np.concatenate([[0.0], np.cumsum(order)])
        shuffled_dd[k] = _max_drawdown(np.maximum(equity, 1e-12))
        resampled_final[k] = float(np.sum(rng.choice(pnl, size=len(pnl), replace=True)))
    return {
        "method": "trade-order permutation + resampling with replacement",
        "samples": samples,
        "seed": seed,
        "trades": len(pnl),
        "max_drawdown_shuffled": _percentiles(shuffled_dd),
        "net_pnl_resampled": _percentiles(resampled_final),
        "probability_of_net_loss": float(np.mean(resampled_final < 0)),
    }


def sensitivity(trials: Sequence[Mapping[str, Any]], objective: str = "objective") -> dict[str, Any]:
    """Per-parameter marginal objective (mean/min/max per value) and the best trial's neighbourhood ratio.

    ``trials``: [{"parameters": {...}, objective: float | None}]; trials without a value are ignored. The
    neighbourhood ratio = mean objective of grid neighbours (one parameter one step away) / best objective —
    values
    near 1 mean a plateau, low values a spike (fragile optimum).
    """
    valid = [t for t in trials if t.get(objective) is not None]
    if not valid:
        raise RobustnessError("no trials with an objective value")
    names = sorted({k for t in valid for k in t["parameters"]})
    marginal: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        groups: dict[str, list[float]] = defaultdict(list)
        for t in valid:
            groups[str(t["parameters"].get(name))].append(float(t[objective]))
        marginal[name] = [
            {"value": v, "mean": float(np.mean(xs)), "min": min(xs), "max": max(xs), "trials": len(xs)}
            for v, xs in sorted(groups.items(), key=lambda kv: _sort_key(kv[0]))
        ]
    best = max(valid, key=lambda t: float(t[objective]))
    axis = {n: sorted({str(t["parameters"].get(n)) for t in valid}, key=_sort_key) for n in names}
    by_params = {tuple(str(t["parameters"].get(n)) for n in names): float(t[objective]) for t in valid}
    best_key = tuple(str(best["parameters"].get(n)) for n in names)
    neighbours = []
    for i, n in enumerate(names):
        values = axis[n]
        pos = values.index(best_key[i])
        for step in (-1, 1):
            if 0 <= pos + step < len(values):
                key = (*best_key[:i], values[pos + step], *best_key[i + 1 :])
                if key in by_params:
                    neighbours.append(by_params[key])
    best_value = float(best[objective])
    ratio = float(np.mean(neighbours)) / best_value if neighbours and best_value not in (0.0,) else None
    return {
        "parameters": names,
        "marginal": marginal,
        "best": {"parameters": best["parameters"], "objective": best_value},
        "neighbours": len(neighbours),
        "neighbourhood_ratio": ratio,
    }


def _sort_key(value: str) -> tuple[int, float | str]:
    try:
        return (0, float(value))
    except ValueError:
        return (1, value)
