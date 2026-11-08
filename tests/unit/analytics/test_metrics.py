import math
from datetime import UTC, datetime, timedelta, timezone

import pytest
from hypothesis import given
from hypothesis import strategies as st

from quantlab.analytics.metrics import (
    MetricsError,
    cross_check,
    daily_series,
    drawdown_stats,
    equity_metrics,
)


def day(n, hour=16):
    return datetime(2024, 1, 1, hour, tzinfo=UTC) + timedelta(days=n)


def curve(values):
    return [(day(i), v) for i, v in enumerate(values)]


def test_hand_computed_example():
    # 100 -> 110 -> 99 -> 121 ; returns: +10 %, -10 %, +22.2222 %
    m = equity_metrics(curve([100.0, 110.0, 99.0, 121.0]))
    r = [0.10, -0.10, 121 / 99 - 1]
    mean = sum(r) / 3
    sd = math.sqrt(sum((x - mean) ** 2 for x in r) / 2)
    assert m.total_return == pytest.approx(0.21)
    assert m.annual_volatility == pytest.approx(sd * math.sqrt(252))
    assert m.sharpe == pytest.approx(mean / sd * math.sqrt(252))
    downside = math.sqrt((0.10**2) / 3)
    assert m.sortino == pytest.approx(mean / downside * math.sqrt(252))
    assert m.max_drawdown == pytest.approx(0.10)
    assert m.max_drawdown_duration_days == 2  # peak day1 -> recovered day3
    assert m.cagr == pytest.approx(1.21 ** (365.25 / 3) - 1)
    assert m.calmar == pytest.approx(m.cagr / 0.10)
    assert (m.best_day, m.worst_day) == (pytest.approx(r[2]), pytest.approx(-0.10))
    assert (m.start, m.end, m.observations) == ("2024-01-01", "2024-01-04", 4)


def test_unrecovered_drawdown_runs_to_end():
    m = equity_metrics(curve([100.0, 120.0, 90.0, 100.0]))
    assert m.max_drawdown == pytest.approx(0.25)
    assert m.max_drawdown_duration_days == 2


def test_monotonic_curve_has_no_drawdown_or_sortino():
    m = equity_metrics(curve([100.0, 101.0, 102.0]))
    assert m.max_drawdown == 0.0 and m.calmar is None and m.sortino is None


def test_flat_curve_has_no_sharpe():
    m = equity_metrics(curve([100.0, 100.0, 100.0]))
    assert m.sharpe is None and m.total_return == 0.0


def test_risk_free_rate_reduces_sharpe():
    pts = curve([100.0, 101.0, 100.5, 102.0, 101.0])
    assert equity_metrics(pts, risk_free_rate=0.05).sharpe < equity_metrics(pts).sharpe


def test_resampling_keeps_last_value_per_utc_day():
    est = timezone(timedelta(hours=-5))
    pts = [
        (datetime(2024, 1, 1, 10, tzinfo=UTC), 100.0),
        (datetime(2024, 1, 1, 15, tzinfo=UTC), 101.0),
        (datetime(2024, 1, 1, 20, tzinfo=est), 102.0),  # = 2024-01-02 01:00 UTC
    ]
    assert [v for _, v in daily_series(pts)] == [101.0, 102.0]


@pytest.mark.parametrize(
    "pts",
    [
        [(datetime(2024, 1, 1), 100.0), (datetime(2024, 1, 2), 101.0)],  # noqa: DTZ001
        curve([100.0, 0.0]),
        curve([100.0, float("nan")]),
        curve([100.0]),
        [],
    ],
)
def test_invalid_input(pts):
    with pytest.raises(MetricsError):
        equity_metrics(pts)


@given(st.lists(st.floats(min_value=1.0, max_value=1e6), min_size=2, max_size=60))
def test_drawdown_properties(values):
    days = [day(i).date() for i in range(len(values))]
    dd, duration = drawdown_stats(days, values)
    assert 0.0 <= dd < 1.0
    assert 0 <= duration <= len(values) - 1
    worst = max(1 - v / max(values[: i + 1]) for i, v in enumerate(values))
    assert dd == pytest.approx(worst)


def test_cross_check_statuses():
    m = equity_metrics(curve([100000.0, 99900.0, 99839.57]))
    checks = {c.metric: c for c in cross_check(m, {"Net Profit": -0.0016, "Drawdown": 0.05})}
    assert checks["total_return"].status == "match"
    assert checks["max_drawdown"].status == "mismatch"
    assert checks["cagr"].status == "unavailable"


# ---- DEC-0032: full-resolution total return / CAGR / drawdown (found by the first real LEAN run) ----------


def test_starting_equity_and_exact_span_define_return_and_cagr():
    """Intraday points: the curve starts at the starting cash, not at the first daily close."""
    t0 = datetime(2013, 10, 7, 0, 0, tzinfo=UTC)
    pts = [
        (t0, 100_000.0),  # starting cash
        (t0 + timedelta(hours=14), 99_000.0),  # first day's close (after buying)
        (t0 + timedelta(days=4, hours=16), 101_000.0),
    ]
    m = equity_metrics(pts)
    assert m.start_equity == 100_000.0 and m.total_return == pytest.approx(0.01)
    years = (4 + 16 / 24) / 365.25
    assert m.cagr == pytest.approx(1.01 ** (1 / years) - 1)
    assert m.worst_day == pytest.approx(-0.01)  # day one's return is measured from the starting cash


def test_intraday_drawdown_between_daily_closes_counts():
    t0 = datetime(2024, 1, 2, 15, tzinfo=UTC)
    pts = [
        (t0, 100.0),
        (t0 + timedelta(hours=2), 90.0),  # intraday dip
        (t0 + timedelta(hours=5), 101.0),  # closes higher
        (t0 + timedelta(days=1), 102.0),
    ]
    m = equity_metrics(pts)
    assert m.max_drawdown == pytest.approx(0.10)  # daily closes alone would show none


def test_daily_only_curves_keep_their_previous_metrics():
    pts = [(datetime(2024, 1, d, 21, tzinfo=UTC), v) for d, v in ((2, 100.0), (3, 110.0), (4, 99.0))]
    m = equity_metrics(pts)
    assert m.total_return == pytest.approx(-0.01) and m.max_drawdown == pytest.approx(0.1)
    assert m.best_day == pytest.approx(0.10)  # no extra base point when the first point is a daily close
