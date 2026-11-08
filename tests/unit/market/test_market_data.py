"""Market-data interface: normalisation, data-state guard, mock streaming, config switch, comparison."""

from datetime import UTC, datetime, timedelta
from itertools import islice

import pandas as pd
import pytest

from quantlab.core.config import ConfigError, load_settings
from quantlab.data.compare import compare_bars
from quantlab.data.factory import historical_provider, quote_guard, realtime_provider
from quantlab.data.market_data import (
    DataState,
    DataStateError,
    MarketDataError,
    Quote,
    QuoteGuard,
    RealtimeProvider,
    normalize_quote,
)
from quantlab.data.providers.base import ProviderError
from quantlab.data.providers.mock_stream import MockStreamingProvider

NOW = datetime(2024, 7, 2, 15, 0, tzinfo=UTC)


def quote(state=DataState.LIVE, **kw):
    base = {
        "symbol": "spy",
        "state": state,
        "source": "test",
        "received_ts": NOW,
        "exchange_ts": NOW,
        "bid": 100.0,
        "ask": 100.1,
        "last": 100.05,
    }
    return normalize_quote(Quote(**{**base, **kw}))


def test_normalize_flags_and_sanitises():
    q = quote(bid=-1, ask=float("nan"))
    assert q.symbol == "SPY" and q.bid is None and q.ask is None and q.last == 100.05 and q.mid is None
    assert "stale" in quote(exchange_ts=NOW - timedelta(seconds=60)).flags
    assert "stale" not in quote(state=DataState.DELAYED, exchange_ts=NOW - timedelta(minutes=15)).flags
    assert "clock_skew" in quote(exchange_ts=NOW + timedelta(seconds=10)).flags
    assert "crossed" in quote(bid=101.0, ask=100.0).flags
    assert "no_price" in quote(bid=None, ask=None, last=None).flags
    assert quote(exchange_ts=NOW - timedelta(seconds=2)).latency_s == pytest.approx(2.0)
    with pytest.raises(MarketDataError, match="naive"):
        normalize_quote(Quote("X", DataState.LIVE, "t", datetime(2024, 1, 1)))  # noqa: DTZ001


@pytest.mark.parametrize("state", [DataState.DELAYED, DataState.FROZEN, DataState.SIMULATED])
def test_live_guard_never_accepts_non_live_data(state):
    with pytest.raises(DataStateError, match=state.value):
        QuoteGuard.for_mode(require_live=True).check(quote(state=state))
    assert QuoteGuard.for_mode(require_live=False).check(quote(state=state)).state is state


def test_guard_rejects_flagged_quotes():
    guard = QuoteGuard.for_mode(require_live=True)
    assert guard.check(quote()).state is DataState.LIVE
    with pytest.raises(DataStateError, match="stale"):
        guard.check(quote(exchange_ts=NOW - timedelta(minutes=5)))


def bars(symbols=("SPY", "QQQ"), n=3):
    rows = []
    for i in range(n):
        for j, s in enumerate(symbols):
            rows.append(
                {
                    "symbol": s,
                    "ts": pd.Timestamp("2024-07-02T13:31Z") + pd.Timedelta(minutes=i),
                    "open": 1.0,
                    "high": 1.0,
                    "low": 1.0,
                    "close": 100.0 + i + 10 * j,
                    "volume": 10.0,
                }
            )
    return pd.DataFrame(rows)


def test_mock_stream_is_always_simulated_and_deterministic():
    p = MockStreamingProvider(bars(), source="fixture", spread_bps=10)
    assert isinstance(p, RealtimeProvider)
    quotes = list(p.stream(["spy"]))
    assert [q.last for q in quotes] == [100.0, 101.0, 102.0]
    assert all(q.state is DataState.SIMULATED for q in quotes)
    assert quotes[0].bid == pytest.approx(100.0 * (1 - 0.0005)) and quotes[0].ask == pytest.approx(100.05)
    assert [q.volume for q in quotes] == [10.0, 20.0, 30.0]
    assert quotes == list(MockStreamingProvider(bars(), source="fixture", spread_bps=10).stream(["SPY"]))
    snap = p.snapshot(["SPY", "QQQ"])
    assert {q.symbol: q.last for q in snap} == {"QQQ": 112.0, "SPY": 102.0}
    assert p.status().state is DataState.SIMULATED
    with pytest.raises(DataStateError):
        QuoteGuard.for_mode(require_live=True).check(quotes[0])
    p.close()
    assert list(p.stream(["SPY"])) == [] and not p.status().connected
    with pytest.raises(MarketDataError):
        p.snapshot(["NOPE"])


def test_mock_stream_paces_in_wall_time():
    sleeps = []
    p = MockStreamingProvider(bars(), source="f", interval_s=0.5, sleep=sleeps.append)
    list(islice(p.stream(["SPY", "QQQ"]), 4))
    assert sleeps == [0.5, 0.5, 0.5]


def test_config_switch_and_validation(ql_home):
    s = load_settings()
    assert (s.market_data.historical, s.market_data.realtime, s.market_data.require_live) == (
        "tiingo",
        "mock",
        False,
    )
    with pytest.raises(ConfigError, match="SIMULATED"):
        load_settings(overrides={"market_data": {"require_live": True}})
    prod = load_settings(
        overrides={"market_data": {"historical": "ibkr", "realtime": "ibkr", "require_live": True}}
    )
    hist, live = historical_provider(prod, None, "XNYS"), realtime_provider(prod, None, None)
    assert hist.name == live.name == "ibkr"  # built without connecting
    assert "4002" in live.endpoint and "client id 18" in live.endpoint  # paper port, market-data client id
    with pytest.raises(ConfigError, match="ibkr_data_type='live'"):
        load_settings(
            overrides={"market_data": {"realtime": "ibkr", "require_live": True, "ibkr_data_type": "delayed"}}
        )
    assert quote_guard(prod).allowed == frozenset({DataState.LIVE})
    with pytest.raises(MarketDataError, match="needs a dataset"):
        realtime_provider(s, None, None)


def test_historical_provider_requires_key(ql_home):
    from quantlab.core.secret_store import SecretStore

    store = SecretStore(backend=type("K", (), {"get_password": lambda *a: None})(), env={})
    with pytest.raises(ProviderError, match="secret set tiingo/api-key"):
        historical_provider(load_settings(), store, "XNYS")
    store = SecretStore(
        backend=type("K", (), {"get_password": lambda *a: None})(),
        env={"QUANTLAB_SECRET__TIINGO__API_KEY": "k"},
    )
    provider = historical_provider(load_settings(), store, "XNYS")
    assert provider.name == "tiingo"
    provider.close()


def test_compare_bars_overlap_differences_and_convention_hint():
    a = bars(("SPY",), 5)
    b = a.copy()
    b.loc[1, "close"] = b.loc[1, "close"] * 1.01  # one bar differs by 1 %
    b = b.drop(index=4)
    [c] = compare_bars(a, b)
    assert (c.rows_a, c.rows_b, c.overlap, c.only_a, c.only_b) == (5, 4, 4, 1, 0)
    assert c.close_rel_diff_max == pytest.approx(0.01) and "differ by more than" in c.notes[0]
    shifted = a.assign(
        ts=a["ts"] - pd.Timedelta(minutes=1) + pd.Timedelta(seconds=30)
    )  # start vs end mismatch
    [d] = compare_bars(a, shifted)
    assert d.overlap == 0 and "bar-start vs bar-end" in d.notes[0]
    [e] = compare_bars(a, a.assign(symbol="QQQ"))[:1]
    assert "only one dataset" in e.notes[0]
