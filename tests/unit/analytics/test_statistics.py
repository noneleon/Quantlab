import math

import numpy as np
import pandas as pd
import pytest

from quantlab.analytics.statistics import (
    StatisticsError,
    correlation,
    distribution,
    returns,
    rolling_volatility,
)


def test_simple_and_log_returns():
    p = pd.Series([100.0, 110.0, 99.0])
    assert returns(p).tolist() == pytest.approx([0.10, -0.10])
    assert returns(p, kind="log").tolist() == pytest.approx([math.log(1.1), math.log(0.9)])
    with pytest.raises(StatisticsError):
        returns(pd.Series([1.0, 0.0]))
    with pytest.raises(StatisticsError):
        returns(p, kind="cubic")


def test_distribution_hand_computed():
    r = pd.Series([0.01, -0.02, 0.03, -0.04, 0.05])
    d = distribution(r, var_level=0.8)
    assert d["count"] == 5 and d["mean"] == pytest.approx(0.006)
    assert d["std"] == pytest.approx(float(np.std(r, ddof=1)))
    assert d["annual_volatility"] == pytest.approx(d["std"] * math.sqrt(252))
    assert d["positive_fraction"] == pytest.approx(0.6)
    q = float(r.quantile(0.2))
    assert d["var_80"] == pytest.approx(-q)
    assert d["es_80"] == pytest.approx(-float(r[r <= q].mean()))
    assert d["min"] == -0.04 and d["max"] == 0.05


def test_distribution_validation():
    with pytest.raises(StatisticsError):
        distribution(pd.Series([0.1, None]))
    with pytest.raises(StatisticsError):
        distribution(pd.Series([0.1, 0.2, 0.3]), var_level=1.5)


def test_correlation_and_rolling_vol():
    rng = np.random.default_rng(7)  # synthetic noise, test only
    a = pd.Series(rng.normal(size=100))
    frame = pd.DataFrame({"a": a, "b": 2 * a, "c": -a})
    corr = correlation(frame)
    assert corr.loc["a", "b"] == pytest.approx(1.0) and corr.loc["a", "c"] == pytest.approx(-1.0)
    vol = rolling_volatility(a, window=10)
    assert vol.iloc[:9].isna().all()
    assert vol.iloc[9] == pytest.approx(a.iloc[:10].std(ddof=1) * math.sqrt(252))
    with pytest.raises(StatisticsError):
        rolling_volatility(a, window=1)
    with pytest.raises(StatisticsError):
        correlation(frame, method="kendall")  # type: ignore[arg-type]
