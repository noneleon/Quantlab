"""Chart datasets / trades / benchmark (AN-002, BT-008). Synthetic values, hand-computed expectations."""

from datetime import UTC, date, datetime, timedelta
from itertools import pairwise

import pytest

from quantlab.analytics.charts import (
    Fill,
    annual_returns,
    benchmark_comparison,
    chart_datasets,
    drawdown_series,
    monthly_returns,
    rolling_sharpe,
    round_trips,
    trade_stats,
)
from quantlab.analytics.metrics import MetricsError


def ts(day, hour=20):
    return datetime(2024, 1, 1, hour, tzinfo=UTC) + timedelta(days=day)


def test_drawdown_series():
    s = [(date(2024, 1, d), v) for d, v in [(1, 100.0), (2, 110.0), (3, 99.0), (4, 121.0)]]
    assert [round(v, 6) for _, v in drawdown_series(s)] == [0.0, 0.0, -0.1, 0.0]


def test_monthly_and_annual_returns_chain_period_ends():
    s = [
        (date(2023, 12, 29), 100.0),
        (date(2024, 1, 15), 105.0),
        (date(2024, 1, 31), 110.0),
        (date(2024, 2, 29), 99.0),
    ]
    monthly = monthly_returns(s)
    assert [(m["year"], m["month"]) for m in monthly] == [(2023, 12), (2024, 1), (2024, 2)]
    assert [round(m["return"], 6) for m in monthly] == [0.0, 0.1, -0.1]
    assert [(a["year"], round(a["return"], 6)) for a in annual_returns(s)] == [(2023, 0.0), (2024, -0.01)]


def test_rolling_sharpe_window():
    s = [(date(2024, 1, d), v) for d, v in enumerate([100.0, 101.0, 100.0, 102.0, 101.0], start=1)]
    out = rolling_sharpe(s, window=3, periods_per_year=252)
    assert [v is None for _, v in out] == [True, True, False, False]
    with pytest.raises(MetricsError):
        rolling_sharpe(s, window=1)


def test_fifo_round_trips_partial_and_flip():
    fills = [
        Fill(ts(0), "SPY", 10, 100.0),
        Fill(ts(1), "SPY", 5, 110.0),
        Fill(ts(2), "SPY", -12, 120.0),  # closes 10 @100 and 2 @110
        Fill(ts(3), "SPY", -8, 115.0),  # closes 3 @110, opens short 5 @115
        Fill(ts(4), "SPY", 5, 105.0),  # covers the short
    ]
    trips = round_trips(fills)
    assert [(t.direction, t.quantity, t.entry_price, t.exit_price, t.pnl) for t in trips] == [
        ("long", 10, 100.0, 120.0, 200.0),
        ("long", 2, 110.0, 120.0, 20.0),
        ("long", 3, 110.0, 115.0, 15.0),
        ("short", 5, 115.0, 105.0, 50.0),
    ]
    stats = trade_stats(trips + round_trips([Fill(ts(0), "QQQ", 1, 10.0), Fill(ts(2), "QQQ", -1, 7.0)]))
    assert (stats["trades"], stats["wins"], stats["losses"]) == (5, 4, 1)
    assert stats["profit_factor"] == pytest.approx(285 / 3) and stats["net_pnl"] == pytest.approx(282)
    assert stats["largest_loss"] == -3.0 and "gross of fees" in stats["basis"]
    assert trade_stats([])["win_rate"] is None


def test_benchmark_beta_one_and_zero_alpha_when_identical():
    s = [(date(2024, 1, d), v) for d, v in enumerate([100.0, 102.0, 101.0, 104.0, 103.0], start=1)]
    stats = benchmark_comparison(s, [(d, v / 2) for d, v in s], name="same")
    assert stats.beta == pytest.approx(1.0) and stats.alpha_annual == pytest.approx(0.0, abs=1e-12)
    assert stats.correlation == pytest.approx(1.0) and stats.excess_return == pytest.approx(0.0)
    assert stats.tracking_error == pytest.approx(0.0, abs=1e-12) and stats.information_ratio is None


def test_benchmark_leveraged_strategy_has_beta_two():
    bench = [(date(2024, 1, d), v) for d, v in enumerate([100.0, 101.0, 99.0, 102.0], start=1)]
    strat = [(bench[0][0], 100.0)]
    for (_, prev), (d, cur) in pairwise(bench):
        strat.append((d, strat[-1][1] * (1 + 2 * (cur / prev - 1))))
    assert benchmark_comparison(strat, bench, name="b").beta == pytest.approx(2.0)
    with pytest.raises(MetricsError, match="fewer than 3"):
        benchmark_comparison(strat, [(date(2030, 1, 1), 1.0)], name="b")


def test_chart_datasets_with_benchmark_rebased():
    equity = [(ts(i), 100_000.0 + 1000 * i) for i in range(5)]
    bench = [(ts(i), 50.0 + i) for i in range(-2, 7)]  # wider than the run
    out = chart_datasets(
        equity,
        [Fill(ts(0), "SPY", 1, 10.0), Fill(ts(3), "SPY", -1, 11.0)],
        benchmark=bench,
        benchmark_name="SPY",
    )
    assert len(out["equity"]) == 5 and out["drawdown"][-1][1] == 0.0
    assert out["trade_stats"]["trades"] == 1
    b = out["benchmark"]
    assert b["equity"][0] == [ts(0).date().isoformat(), 100_000.0] and len(b["equity"]) == 5
    assert b["stats"]["common_days"] == 5
    with pytest.raises(MetricsError):
        chart_datasets(equity[:1])


def test_compare_runs_rebases_and_correlates():
    from quantlab.analytics.comparison import compare_runs

    base = [(ts(i), 100.0 * (1.01**i)) for i in range(5)]
    double = [(ts(i), 50.0 * (1.02**i)) for i in range(5)]
    out = compare_runs(
        [
            {"id": "a", "label": "A", "equity": base, "metrics": {"sharpe": 1.0}},
            {
                "id": "b",
                "label": "B",
                "equity": double,
                "metrics": {"sharpe": 2.0},
                "provenance": "synthetic",
            },
        ]
    )
    assert out["equity"][0]["equity"][0][1] == 1.0 and out["equity"][1]["equity"][-1][1] == pytest.approx(
        1.02**4
    )
    assert out["common_days"] == 4 and out["runs"][1]["metrics"]["sharpe"] == 2.0
    assert (
        out["correlation"] is None or len(out["correlation"]) == 2
    )  # constant returns: undefined correlation
    with pytest.raises(MetricsError):
        compare_runs([{"id": "a", "label": "A", "equity": base, "metrics": {}}])
