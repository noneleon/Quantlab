"""IBKRProvider (DATA-019) against a fake IB client — no network, no IB Gateway.

All prices below are synthetic test values, not market data.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from quantlab.data.calendars import sessions
from quantlab.data.market_data import DataState, DataStateError, MarketDataError, QuoteGuard
from quantlab.data.providers.base import BarRequest, ProviderError
from quantlab.data.providers.ibkr import (
    HistoricalPacer,
    IBBar,
    IBConnectError,
    IBKRProvider,
    IBMessage,
    IBTick,
)
from quantlab.data.schema import Resolution
from quantlab.data.validation import validate_bars

NOW = datetime(2024, 7, 2, 15, 0, tzinfo=UTC)


class FakeIB:
    def __init__(self, *, refuse_connects: int = 0) -> None:
        self.connected = False
        self.refuse_connects = refuse_connects
        self.connect_calls: list[tuple] = []
        self.data_types: list[int] = []
        self.subscribed: list[str] = []
        self.unsubscribed: list[str] = []
        self.history: list[tuple] = []
        self.history_reply = lambda symbol, end, duration, bar_size: []
        self.pending: list[IBMessage] = []
        self.history_messages: list[list[IBMessage]] = []
        self.market_type = 1
        self.drop_after_waits: int | None = None
        self.waits = 0
        self.wait_messages: dict[int, list[IBMessage]] = {}

    def connect(self, host, port, client_id, timeout):
        self.connect_calls.append((host, port, client_id))
        if self.refuse_connects:
            self.refuse_connects -= 1
            raise ConnectionRefusedError("refused")
        self.connected = True

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def set_market_data_type(self, code):
        self.data_types.append(code)

    def historical_bars(self, symbol, end, duration, bar_size, what_to_show, use_rth):
        assert (what_to_show, use_rth) == ("TRADES", True)
        self.history.append((symbol, end, duration, bar_size))
        if self.history_messages:
            self.pending.extend(self.history_messages.pop(0))
        return self.history_reply(symbol, end, duration, bar_size)

    def subscribe(self, symbol):
        self.subscribed.append(symbol)

    def unsubscribe(self, symbol):
        self.unsubscribed.append(symbol)

    def ticks(self, symbols):
        last_trade = NOW - timedelta(seconds=1)
        return [
            IBTick(
                symbol=s,
                market_data_type=self.market_type,
                received=NOW,
                last_trade_time=last_trade,
                bid=100.0,
                ask=100.02,
                last=100.01,
                bid_size=3,
                ask_size=4,
                last_size=1,
                volume=1234,
            )
            for s in symbols
        ]

    def wait(self, timeout):
        self.waits += 1
        self.pending.extend(self.wait_messages.pop(self.waits, []))
        if self.drop_after_waits is not None and self.waits == self.drop_after_waits:
            self.connected = False
            return set()
        return set(self.subscribed)

    def messages(self):
        out, self.pending = self.pending, []
        return out


def provider(fake: FakeIB, **kw) -> IBKRProvider:
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("now", lambda: NOW)
    kw.setdefault("pacer", HistoricalPacer(min_interval_s=0, sleep=lambda s: None))
    return IBKRProvider(fake, host="127.0.0.1", port=4002, client_id=18, **kw)


def test_live_quote_is_normalised_and_passes_live_guard():
    fake = FakeIB()
    p = provider(fake, require_live=True)
    (q,) = p.snapshot(["spy"])
    assert (q.symbol, q.state, q.bid, q.ask, q.last, q.volume) == (
        "SPY",
        DataState.LIVE,
        100.0,
        100.02,
        100.01,
        1234,
    )
    assert q.flags == () and q.latency_s == 1.0
    QuoteGuard.for_mode(True).check(q)
    assert fake.data_types == [1] and fake.connect_calls == [("127.0.0.1", 4002, 18)]
    assert fake.unsubscribed == ["SPY"]


@pytest.mark.parametrize(
    ("ib_type", "state", "flags"),
    [(2, DataState.FROZEN, ()), (3, DataState.DELAYED, ()), (4, DataState.DELAYED, ("frozen",))],
)
def test_state_comes_from_what_ib_delivers(ib_type, state, flags):
    fake = FakeIB()
    fake.market_type = ib_type
    (q,) = provider(fake).snapshot(["SPY"])  # requested live (type 1)
    assert fake.data_types == [1]
    assert q.state is state and q.flags == flags
    with pytest.raises(DataStateError, match=f"{state.value} data rejected"):
        QuoteGuard.for_mode(True).check(q)


def test_silent_fallback_to_delayed_is_labelled_and_reported():
    fake = FakeIB()
    fake.market_type = 3
    fake.pending = [
        IBMessage(1, 10167, "Requested market data is not subscribed. Displaying delayed market data.")
    ]
    p = provider(fake)
    (q,) = p.snapshot(["SPY"])
    assert q.state is DataState.DELAYED
    status = p.status()
    assert status.state is DataState.DELAYED and "10167" in (status.last_error or "")


def test_require_live_only_requests_live():
    with pytest.raises(MarketDataError, match="ibkr_data_type='live'"):
        provider(FakeIB(), require_live=True, data_type="delayed")
    fake = FakeIB()
    provider(fake, data_type="delayed").snapshot(["SPY"])
    assert fake.data_types == [3]


def test_connect_failure_is_a_clear_error_and_status_reports_it():
    fake = FakeIB(refuse_connects=10)
    p = provider(fake)
    with pytest.raises(IBConnectError, match="Start IB Gateway or TWS"):
        p.snapshot(["SPY"])
    status = provider(FakeIB(refuse_connects=10)).status()
    assert not status.connected and "cannot connect" in (status.last_error or "")


def test_stale_last_trade_flags_live_quote():
    fake = FakeIB()
    p = provider(fake, stale_after_s=0.5)  # last trade 1 s old
    (q,) = p.snapshot(["SPY"])
    assert "stale" in q.flags
    with pytest.raises(DataStateError, match="stale"):
        QuoteGuard.for_mode(True).check(q)


def test_stream_reconnects_with_backoff_and_resubscribes():
    fake = FakeIB()
    fake.drop_after_waits = 2
    sleeps: list[float] = []
    p = provider(fake, sleep=sleeps.append)
    stream = p.stream(["SPY", "QQQ"])
    quotes = [next(stream) for _ in range(4)]
    stream.close()
    assert {q.symbol for q in quotes} == {"SPY", "QQQ"}
    assert p.status().reconnects == 1 and sleeps == [1.0]
    assert fake.subscribed == ["SPY", "QQQ", "QQQ", "SPY"]  # restored after the reconnect
    assert set(fake.unsubscribed) == {"SPY", "QQQ"}


def test_stream_gives_up_after_max_reconnects():
    fake = FakeIB()
    fake.drop_after_waits = 1
    sleeps: list[float] = []
    p = provider(fake, sleep=sleeps.append, max_reconnects=3)
    stream = p.stream(["SPY"])
    fake.refuse_connects = 0
    original_connect = fake.connect

    def refuse(*a):
        if fake.waits >= 1:
            raise ConnectionRefusedError("down")
        original_connect(*a)

    fake.connect = refuse
    with pytest.raises(MarketDataError, match="3 reconnect attempts failed"):
        next(stream)
    assert sleeps == [1.0, 2.0, 4.0]


def test_data_lost_after_restore_resubscribes():
    fake = FakeIB()
    fake.wait_messages = {2: [IBMessage(-1, 1101, "Connectivity restored - data lost")]}
    p = provider(fake)
    stream = p.stream(["SPY"])
    for _ in range(3):
        next(stream)
    stream.close()
    assert fake.subscribed.count("SPY") == 2 and "1101" in (p.status().last_error or "")


# ---- historical ---------------------------------------------------------------------------------------
def daily_reply(symbol, end, duration, bar_size):
    days = int(duration.split()[0])
    last = end.date()
    window = sessions("XNYS", last - timedelta(days=days + 5), last)  # reaches beyond the window; filtered
    return [IBBar(d, 10.0, 11.0, 9.0, 10.5, 1000.0) for d in window]


def test_non_session_daily_bar_is_passed_to_validation_not_hidden():
    fake = FakeIB()
    fake.history_reply = lambda *a: [IBBar(date(2024, 7, 4), 10.0, 11.0, 9.0, 10.5, 1.0)]  # US holiday
    raw = provider(fake).fetch_bars(
        BarRequest(
            ("SPY",), Resolution.DAILY, datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 7, 5, tzinfo=UTC)
        )
    )
    _, report = validate_bars(raw.frame, Resolution.DAILY, "XNYS")
    assert "non_session_bar" in {i.check for i in report.errors}


def test_daily_history_windows_and_session_close_timestamps():
    fake = FakeIB()
    fake.history_reply = daily_reply
    p = provider(fake)
    raw = p.fetch_bars(
        BarRequest(
            ("SPY",), Resolution.DAILY, datetime(2022, 1, 3, tzinfo=UTC), datetime(2023, 6, 30, tzinfo=UTC)
        )
    )
    assert [h[2] for h in fake.history] == ["365 D", "179 D"] and all(h[3] == "1 day" for h in fake.history)
    frame = raw.frame
    assert frame["ts"].min() == datetime(2022, 1, 3, 21, tzinfo=UTC)  # 16:00 ET close
    assert frame["ts"].max() == datetime(2023, 6, 30, 20, tzinfo=UTC)
    assert frame["ts"].is_unique and raw.source.startswith("ibkr")
    assert any("split-adjusted" in n for n in raw.notes)


def minute_reply(symbol, end, duration, bar_size):
    assert (duration, bar_size) == ("1 D", "1 min")
    start = end - timedelta(minutes=390 if end.hour == 20 else 210)
    bars = [
        IBBar(start + timedelta(minutes=i), 10.0, 10.1, 9.9, 10.0, 5.0)
        for i in range(int((end - start).seconds / 60))
    ]
    return [IBBar(start - timedelta(days=1), 10.0, 10.1, 9.9, 10.0, 5.0), *bars]  # one bar of another day


def test_minute_history_per_session_bar_end_and_early_close():
    fake = FakeIB()
    fake.history_reply = minute_reply
    raw = provider(fake).fetch_bars(
        BarRequest(
            ("SPY",), Resolution.MINUTE, datetime(2024, 7, 2, tzinfo=UTC), datetime(2024, 7, 3, tzinfo=UTC)
        )
    )
    assert len(fake.history) == 2 and len(raw.frame) == 390 + 210
    assert raw.frame["ts"].min() == datetime(2024, 7, 2, 13, 31, tzinfo=UTC)
    assert raw.frame["ts"].max() == datetime(2024, 7, 3, 17, 0, tzinfo=UTC)  # 13:00 ET early close
    assert any("outside the requested sessions ignored" in n for n in raw.notes)


def test_pacing_violation_is_retried_then_fails_clearly():
    fake = FakeIB()
    fake.history_reply = daily_reply
    pacing = IBMessage(
        5,
        162,
        "Historical Market Data Service error message:API historical data query cancelled: pacing violation",
    )
    fake.history_messages = [[pacing], []]
    sleeps: list[float] = []
    p = provider(fake, sleep=sleeps.append)
    request = BarRequest(
        ("SPY",), Resolution.DAILY, datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 7, 5, tzinfo=UTC)
    )
    assert not p.fetch_bars(request).frame.empty and sleeps == [15.0]
    fake.history_messages = [[pacing]] * 4
    with pytest.raises(ProviderError, match="pacing violation persisted"):
        p.fetch_bars(request)


def test_history_errors():
    fake = FakeIB()
    fake.history_messages = [[IBMessage(5, 200, "No security definition has been found for the request")]]
    p = provider(fake)
    request = BarRequest(
        ("NOPE",), Resolution.DAILY, datetime(2024, 7, 1, tzinfo=UTC), datetime(2024, 7, 5, tzinfo=UTC)
    )
    with pytest.raises(ProviderError, match="no security definition"):
        p.fetch_bars(request)
    fake.history_messages = [[IBMessage(5, 162, "HMDS query returned no data")]]
    with pytest.raises(ProviderError, match="returned no bars"):
        p.fetch_bars(request)
    with pytest.raises(ProviderError, match="start date"):
        p.fetch_bars(BarRequest(("SPY",), Resolution.DAILY))
    with pytest.raises(ProviderError, match="daily and minute"):
        p.fetch_bars(BarRequest(("SPY",), Resolution.HOUR, datetime(2024, 7, 1, tzinfo=UTC)))


def test_pacer_sliding_window():
    clock = [0.0]
    sleeps: list[float] = []

    def sleep(s):
        sleeps.append(s)
        clock[0] += s

    pacer = HistoricalPacer(
        max_requests=3, window_s=10.0, min_interval_s=1.0, clock=lambda: clock[0], sleep=sleep
    )
    for _ in range(4):
        pacer.acquire()
    assert sleeps == [1.0, 1.0, 8.0]  # min gap twice, then wait for the window to free a slot


# ---- ib_async translation (real ib_async objects, no connection) ----------------------------------------
def test_ib_async_ticker_and_bar_translation():
    ib_async = pytest.importorskip("ib_async")
    from quantlab.data.providers.ib_async_client import bar_from_bardata, tick_from_ticker

    ticker = ib_async.Ticker(contract=ib_async.Stock("SPY", "SMART", "USD"))
    ticker.marketDataType = 3
    ticker.bid, ticker.ask, ticker.last, ticker.volume = 100.0, 100.02, float("nan"), -1.0
    ticker.time = datetime(2024, 7, 2, 15, 0, tzinfo=UTC)
    tick = tick_from_ticker("SPY", ticker)
    assert (tick.market_data_type, tick.bid, tick.ask, tick.last, tick.volume) == (
        3,
        100.0,
        100.02,
        None,
        None,
    )
    assert tick.received == ticker.time and tick.last_trade_time is None and not tick.halted

    daily = bar_from_bardata(
        ib_async.BarData(date=date(2024, 7, 2), open=1, high=2, low=0.5, close=1.5, volume=10)
    )
    assert daily.date == date(2024, 7, 2)
    minute = bar_from_bardata(
        ib_async.BarData(
            date=datetime(2024, 7, 2, 13, 30, tzinfo=UTC), open=1, high=2, low=0.5, close=1.5, volume=10
        )
    )
    assert minute.date == datetime(2024, 7, 2, 13, 30, tzinfo=UTC)


def test_ibkr_fetch_into_catalog_requires_matching_adjustment(db_session, tmp_path):
    from quantlab.data.catalog import DataCatalog, DataError
    from quantlab.data.providers.base import LicenseInfo
    from quantlab.data.schema import AssetClass, PriceAdjustment, Provenance

    catalog = DataCatalog(tmp_path / "data")
    for name, adjustment in (("spy-raw", PriceAdjustment.RAW), ("spy-ib", PriceAdjustment.SPLIT_ADJUSTED)):
        catalog.create_dataset(
            db_session,
            name,
            asset_class=AssetClass.EQUITY,
            market="usa",
            resolution=Resolution.DAILY,
            provenance=Provenance.SYNTHETIC,  # fake client: synthetic values
            price_adjustment=adjustment,
            source="ibkr (fake client)",
            license=LicenseInfo("t", False),
        )
    fake = FakeIB()
    fake.history_reply = daily_reply
    request = BarRequest(
        ("SPY",), Resolution.DAILY, datetime(2024, 6, 3, tzinfo=UTC), datetime(2024, 6, 28, tzinfo=UTC)
    )
    with pytest.raises(DataError, match="delivers split_adjusted prices"):
        catalog.fetch(db_session, "spy-raw", provider(fake), request)
    result = catalog.fetch(db_session, "spy-ib", provider(fake), request)
    db_session.commit()
    assert result.created and result.version.row_count == 19  # June 2024 XNYS sessions (Juneteenth closed)


# ---- latest(): kept subscriptions for deployments (KI-0080) ----------------------------------------------


def test_latest_keeps_subscriptions_open_instead_of_resubscribing_every_call(monkeypatch):
    fake = FakeIB()
    p = provider(fake)
    for _ in range(5):  # a deployment asks every second
        (q,) = p.latest(["spy"])
        assert q.symbol == "SPY" and q.bid == 100.0
    assert fake.subscribed == ["SPY"] and fake.unsubscribed == []  # one subscription, never torn down


def test_latest_drops_idle_subscriptions_and_restores_after_a_disconnect(monkeypatch):
    import quantlab.data.providers.ibkr as ibkr

    clock = [1000.0]
    monkeypatch.setattr(ibkr.time, "monotonic", lambda: clock[0])
    fake = FakeIB()
    p = provider(fake)
    p.latest(["SPY"])
    p.latest(["QQQ"])
    clock[0] += 301  # SPY idle for more than 5 minutes
    p.latest(["QQQ"])
    assert fake.unsubscribed == ["SPY"]
    fake.connected = False  # gateway restart
    p.latest(["QQQ"])
    assert fake.subscribed.count("QQQ") == 2  # restored after reconnecting
