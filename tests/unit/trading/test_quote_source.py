"""The paper OMS quote function's back-off (review 5c-5 M2): it is called under a lock, so a failing source
must answer at once instead of waiting or reconnecting on every call. Synthetic quotes only."""

from datetime import UTC, datetime

import pytest

from quantlab.bootstrap import AppContext, initialize
from quantlab.data.market_data import DataState, MarketDataError, Quote


class Source:
    def __init__(self):
        self.calls: list[str] = []
        self.closed = 0
        self.fail: Exception | None = None

    def latest(self, symbols):
        self.calls.extend(symbols)
        if self.fail is not None:
            raise self.fail
        now = datetime.now(UTC)
        return [Quote(s, DataState.SIMULATED, "test", now, now, bid=1.0, ask=1.01, last=1.0) for s in symbols]

    def close(self):
        self.closed += 1


@pytest.fixture
def quote_fn(ql_home, memory_keyring, monkeypatch):
    import quantlab.bootstrap as bootstrap
    import quantlab.data.factory as factory

    initialize()
    built: list[Source] = []

    def make(*args):
        built.append(Source())
        return built[-1]

    clock = [1000.0]
    monkeypatch.setattr(factory, "realtime_provider", make)
    monkeypatch.setattr(bootstrap.time, "monotonic", lambda: clock[0])
    context = AppContext.open(configure_logging=False)
    with context.sessions() as session:
        oms = context.paper_oms(session)
    yield oms.quote, built, clock
    context.close()


def test_a_symbol_error_backs_off_only_that_symbol(quote_fn):
    quote, built, clock = quote_fn
    assert quote("SPY") is not None
    built[0].fail = MarketDataError("no data for XYZ")
    assert quote("XYZ") is None
    built[0].fail = None
    calls = len(built[0].calls)
    assert quote("XYZ") is None and len(built[0].calls) == calls  # backed off: the source is not asked
    assert quote("SPY") is not None  # other symbols are unaffected
    clock[0] += 31
    assert quote("XYZ") is not None


def test_a_lost_source_is_closed_and_rebuilt_only_after_the_back_off(quote_fn):
    quote, built, clock = quote_fn
    assert quote("SPY") is not None
    built[0].fail = ConnectionError("IB Gateway lost")
    assert quote("SPY") is None
    assert built[0].closed == 1  # frees the IB client id before a new connection
    assert quote("SPY") is None and len(built) == 1  # no rebuild (no connect wait) within 30 s
    clock[0] += 31
    assert quote("SPY") is not None and len(built) == 2
