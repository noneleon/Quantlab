"""Paper broker (BRK-002) and portfolio book (PORT-002). Quotes are synthetic SIMULATED test values."""

from datetime import UTC, datetime

import pytest

from quantlab.brokers.base import (
    BrokerError,
    BrokerOrder,
    BrokerUnavailable,
    ExecutionReport,
    OrderAccepted,
    OrderCancelled,
    OrderExpired,
    OrderRejected,
    OrderType,
    Side,
    SubmitUnknown,
    TimeInForce,
)
from quantlab.brokers.paper import PaperBroker
from quantlab.data.market_data import DataState, Quote
from quantlab.portfolio.book import Book, Fill

NOW = datetime(2026, 11, 9, 15, tzinfo=UTC)


class Quotes:
    def __init__(self):
        self.q = {"SPY": (100.0, 100.10, 100.05)}

    def __call__(self, symbol):
        if symbol not in self.q:
            return None
        bid, ask, last = self.q[symbol]
        return Quote(symbol, DataState.SIMULATED, "test", NOW, NOW, bid=bid, ask=ask, last=last)


def broker(tmp_path, quotes=None, **kw):
    return PaperBroker(tmp_path / "paper.json", quotes or Quotes(), now=lambda: NOW, slippage_bps=0, **kw)


def drain(b):
    """Read and acknowledge broker events (the OMS does the same after committing)."""
    events = b.events()
    b.acknowledge(len(events))
    return events


def order(oid, side=Side.BUY, qty=10, kind=OrderType.MARKET, **kw):
    return BrokerOrder(oid, "SPY", side, qty, kind, **kw)


def test_market_buy_fills_at_ask_with_commission(tmp_path):
    b = broker(tmp_path)
    b.submit(order("o1"))
    events = drain(b)
    assert isinstance(events[0], OrderAccepted) and isinstance(events[1], ExecutionReport)
    fill = events[1]
    assert (fill.price, fill.quantity, fill.commission) == (100.10, 10, 1.0)
    assert b.cash() == pytest.approx(100_000 - 1001.0 - 1.0)
    assert [(p.symbol, p.quantity) for p in b.positions()] == [("SPY", 10)]


def test_limit_waits_until_crossed_then_fills_at_limit_or_better(tmp_path):
    quotes = Quotes()
    b = broker(tmp_path, quotes)
    b.submit(order("o1", kind=OrderType.LIMIT, limit_price=99.0))
    assert [type(e) for e in drain(b)] == [OrderAccepted] and b.open_order_ids() == {"o1"}
    quotes.q["SPY"] = (98.0, 98.5, 98.2)
    (fill,) = drain(b)
    assert fill.price == 98.5 and b.open_order_ids() == set()


def test_stop_triggers_on_last(tmp_path):
    quotes = Quotes()
    b = broker(tmp_path, quotes)
    b.submit(order("o1", kind=OrderType.STOP, stop_price=101.0))
    drain(b)
    quotes.q["SPY"] = (101.0, 101.2, 101.1)
    (fill,) = drain(b)
    assert fill.price == 101.2


def test_rejections_and_failure_modes(tmp_path):
    b = broker(tmp_path, starting_cash=500.0)
    b.submit(order("big"))  # 10 x 100.1 > 500 cash
    assert [type(e) for e in drain(b)] == [OrderAccepted, OrderRejected]
    b.submit(order("short", side=Side.SELL))
    assert isinstance(drain(b)[-1], OrderRejected)  # no shorting in a cash account
    b.fail_next = "reject"
    b.submit(order("r"))
    assert isinstance(drain(b)[0], OrderRejected)
    b.fail_next = "unavailable"
    with pytest.raises(BrokerUnavailable):
        b.submit(order("u"))
    b.fail_next = "unknown"
    with pytest.raises(SubmitUnknown):
        b.submit(order("x"))
    assert "x" not in b.open_order_ids()
    with pytest.raises(BrokerError, match="duplicate"):
        b.submit(order("big"))


def test_cancel_expire_and_restart_persistence(tmp_path):
    b = broker(tmp_path)
    b.submit(order("lim", kind=OrderType.LIMIT, limit_price=50.0))
    b.submit(order("gtc", kind=OrderType.LIMIT, limit_price=50.0, time_in_force=TimeInForce.GTC))
    b.submit(order("buy"))
    drain(b)
    b.cancel("lim")
    with pytest.raises(BrokerError):
        b.cancel("lim")
    b.expire_day_orders()
    assert [type(e) for e in drain(b)] == [OrderCancelled]  # gtc stays; lim already cancelled
    restarted = broker(tmp_path)  # same state file
    assert restarted.open_order_ids() == {"gtc"} and restarted.cash() == b.cash()
    assert [e.order_id for e in restarted.executions()] == ["buy"]
    b2 = broker(tmp_path / "x")
    b2.submit(order("d", kind=OrderType.LIMIT, limit_price=1.0))
    b2.expire_day_orders()
    assert any(isinstance(e, OrderExpired) for e in drain(b2))


def test_no_quote_no_fill(tmp_path):
    b = broker(tmp_path)
    b.submit(BrokerOrder("q", "QQQ", Side.BUY, 1, OrderType.MARKET))
    assert [type(e) for e in drain(b)] == [OrderAccepted] and b.open_order_ids() == {"q"}


def test_book_average_cost_realized_and_flip():
    book = Book.from_fills(
        10_000.0,
        [
            Fill("SPY", 10, 100.0, 1.0),
            Fill("SPY", 10, 110.0, 1.0),
            Fill("SPY", -15, 120.0, 1.0),
            Fill("SPY", -10, 90.0, 1.0),
        ],
    )
    p = book.positions["SPY"]
    # avg 105 for 20; sell 15 @120 -> +225; sell 10 @90 closes 5 (-75) and opens short 5 @90
    assert p.realized_pnl == pytest.approx(150.0) and p.quantity == -5 and p.average_price == 90.0
    assert book.cash == pytest.approx(10_000 - 1000 - 1100 + 1800 + 900 - 4)
    snap = book.snapshot({"SPY": 80.0})
    assert snap["unrealized_pnl"] == pytest.approx(50.0) and snap["gross_exposure"] == 400.0
    assert snap["equity"] == pytest.approx(book.cash - 400.0)
    assert book.snapshot()["equity"] is None and book.snapshot()["unmarked_symbols"] == ["SPY"]


def test_events_survive_a_crash_until_acknowledged(tmp_path):
    b = broker(tmp_path)
    b.submit(order("o1"))
    assert len(b.events()) == 2  # not acknowledged: e.g. the OMS crashed before committing
    restarted = broker(tmp_path)
    replay = restarted.events()
    assert [type(e) for e in replay] == [OrderAccepted, ExecutionReport]
    assert replay[1].price_state == "SIMULATED"  # fills keep the state of the quote they used
    restarted.acknowledge(len(replay))
    assert broker(tmp_path).events() == []


def test_exec_ids_are_unique_across_account_resets(tmp_path):
    a = broker(tmp_path / "a")
    a.submit(order("x"))
    b = broker(tmp_path / "b")  # a fresh state file restarts every counter
    b.submit(order("y"))
    assert {e.exec_id for e in a.executions()}.isdisjoint({e.exec_id for e in b.executions()})


def test_two_processes_do_not_overwrite_each_other(tmp_path):
    """Second review: a stale in-memory copy must never overwrite another process's fills."""
    a = broker(tmp_path)
    b = broker(tmp_path)  # built earlier, e.g. a slow `paper positions`
    a.submit(order("from-a"))
    b.submit(order("from-b"))
    assert {e.order_id for e in broker(tmp_path).executions()} == {"from-a", "from-b"}
    assert broker(tmp_path).cash() == pytest.approx(100_000 - 2 * (1001.0 + 1.0))


def test_lock_held_by_another_process_makes_the_broker_unavailable(tmp_path, monkeypatch):
    import quantlab.brokers.paper as paper

    b = broker(tmp_path)
    (tmp_path / "paper.lock").write_text("held")
    monkeypatch.setattr(paper, "LOCK_TIMEOUT_S", 0.05)
    with pytest.raises(BrokerUnavailable, match="locked"):
        b.submit(order("x"))
    assert "x" not in broker(tmp_path).open_order_ids()
