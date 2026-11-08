"""Robustness statistics (OPT-004/005). Synthetic random data with fixed seeds — no market data."""

import math

import numpy as np
import pytest

from quantlab.analytics.robustness import (
    RobustnessError,
    deflated_sharpe,
    probability_of_backtest_overfitting,
    sensitivity,
    stationary_bootstrap,
    trade_monte_carlo,
)


def test_expected_max_sharpe_matches_paper_formula_and_monte_carlo():
    # unit-variance trial Sharpes, N = 100 (g = Euler-Mascheroni):
    # SR0 = (1-g)Φ⁻¹(0.99) + gΦ⁻¹(1-1/(100e)) = 2.5306
    rng = np.random.default_rng(3)
    trials = rng.standard_normal(100)
    trials = (trials - trials.mean()) / trials.std(ddof=1)  # exact unit variance
    dsr = deflated_sharpe(rng.normal(0.001, 0.01, 500), trials)
    assert dsr.expected_max_sharpe == pytest.approx(2.5306, abs=1e-3)
    mc = np.mean([rng.standard_normal(100).max() for _ in range(5000)])
    assert dsr.expected_max_sharpe == pytest.approx(mc, rel=0.03)  # the closed form is an approximation


def test_more_trials_deflate_the_same_sharpe():
    rng = np.random.default_rng(4)
    r = rng.normal(0.001, 0.01, 750)
    one = deflated_sharpe(r, [0.1])
    assert one.expected_max_sharpe == 0.0 and one.probability > 0.9
    many = deflated_sharpe(r, list(rng.normal(0, 0.05, 200)))
    assert many.probability < one.probability and many.trials == 200
    assert one.sharpe_annual == pytest.approx(one.sharpe * math.sqrt(252))
    with pytest.raises(RobustnessError):
        deflated_sharpe([0.1, 0.2], [0.1])


def test_pbo_low_for_a_genuinely_better_trial_and_near_half_for_noise():
    rng = np.random.default_rng(5)
    noise = rng.normal(0, 0.01, (400, 10))
    good = noise.copy()
    good[:, 0] += 0.004  # trial 0 is better in every period
    assert probability_of_backtest_overfitting(good, blocks=8).pbo == 0.0
    pure = probability_of_backtest_overfitting(noise, blocks=8)
    assert 0.2 < pure.pbo < 0.8 and pure.splits == math.comb(8, 4)
    with pytest.raises(RobustnessError):
        probability_of_backtest_overfitting(noise[:, :1])


def test_bootstrap_is_seeded_and_degenerate_for_constant_returns():
    flat = stationary_bootstrap([0.001] * 50, samples=50, seed=1)
    assert (
        flat["total_return"]["p5"] == pytest.approx(flat["total_return"]["p95"])
        and flat["probability_of_loss"] == 0
    )
    rng = np.random.default_rng(6)
    r = rng.normal(0, 0.01, 300)
    a, b = stationary_bootstrap(r, samples=200, seed=9), stationary_bootstrap(r, samples=200, seed=9)
    assert a == b and a["max_drawdown"]["p5"] <= a["max_drawdown"]["p95"]
    assert stationary_bootstrap(r, samples=200, seed=10) != a


def test_trade_monte_carlo():
    out = trade_monte_carlo([100.0, -50.0, 30.0, -20.0, 60.0], 10_000.0, samples=300, seed=2)
    assert out["trades"] == 5 and 0 < out["max_drawdown_shuffled"]["p95"] < 0.01
    assert out["net_pnl_resampled"]["p50"] == pytest.approx(120.0, abs=150)
    with pytest.raises(RobustnessError):
        trade_monte_carlo([1.0], 100.0)


def test_sensitivity_plateau_vs_spike():
    plateau = [
        {"parameters": {"a": a, "b": b}, "objective": 1.0 - 0.01 * abs(a - 2)}
        for a in (1, 2, 3)
        for b in (10, 20)
    ]
    s = sensitivity(plateau)
    assert s["best"]["parameters"]["a"] == 2 and s["neighbourhood_ratio"] == pytest.approx(0.99, abs=0.01)
    assert [m["value"] for m in s["marginal"]["a"]] == ["1", "2", "3"]
    spike = [{"parameters": {"a": a}, "objective": 5.0 if a == 2 else 0.5} for a in (1, 2, 3)]
    assert sensitivity(spike)["neighbourhood_ratio"] == pytest.approx(0.1)
    with pytest.raises(RobustnessError):
        sensitivity([{"parameters": {"a": 1}, "objective": None}])
