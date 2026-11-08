"""Trailing stops (OMS-001) and modifying working orders (OMS-006). Synthetic SIMULATED quotes only."""

import pytest

from quantlab.oms import queries
from quantlab.oms.service import OMSError, OrderRequest
from quantlab.oms.states import OrderState as S

from .test_oms_risk import OrderType, Side, stack  # noqa: F401  (fixture)


def buy(oms, key, qty=10):
    return oms.submit(OrderRequest(key, "SPY", Side.BUY, qty))


def trailing(oms, key, qty=10, trail=2.0, percent=False, side=Side.SELL):
    return oms.submit(
        OrderRequest(
            key, "SPY", side, qty, OrderType.TRAILING_STOP, trail_amount=trail, trail_percent=percent
        )
    )


def test_a_sell_trailing_stop_follows_the_high_and_fires_on_the_pullback(stack):  # noqa: F811
    oms, quotes = stack["oms"], stack["quotes"]
    assert buy(oms, "pos").state is S.FILLED
    r = trailing(oms, "trail", trail=2.0)
    assert r.state is S.ACCEPTED
    for px in (51.0, 53.0, 52.0):  # high 53 -> stop 51; 52 does not reach it
        quotes.px["SPY"] = px
        oms.process_events()
    w = stack["broker"]._state["working"][r.order.id]
    assert (w["trail_ref"], w["stop_price"]) == (53.0, 51.0)
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.ACCEPTED
    quotes.px["SPY"] = 50.9  # pulls back through the stop: fills like a market order at the bid
    oms.process_events()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.FILLED
        (fill,) = [f for f in queries.fills(s, "paper") if f.order_id == r.order.id]
        assert fill.price == pytest.approx(50.89)


def test_percent_trailing_stops_and_buy_side(stack):  # noqa: F811
    oms, quotes = stack["oms"], stack["quotes"]
    assert buy(oms, "pos").state is S.FILLED
    r = trailing(oms, "pct", trail=10.0, percent=True)  # 10 % below the high
    quotes.px["SPY"] = 60.0
    oms.process_events()
    assert stack["broker"]._state["working"][r.order.id]["stop_price"] == pytest.approx(54.0)
    oms.cancel(r.order.id)
    quotes.px["SPY"] = 50.0
    b = trailing(oms, "buy-trail", trail=1.0, side=Side.BUY)  # buys once the price rises 1 above the low
    quotes.px["SPY"] = 48.0
    oms.process_events()
    with stack["sessions"]() as s:
        assert oms.state(s, b.order.id) is S.ACCEPTED  # low 48 -> stop 49
    quotes.px["SPY"] = 49.5
    oms.process_events()
    with stack["sessions"]() as s:
        assert oms.state(s, b.order.id) is S.FILLED  # 49.5 >= 49


def test_trailing_stops_need_a_valid_trail(stack):  # noqa: F811
    oms = stack["oms"]
    with pytest.raises(OMSError, match="positive trail amount"):
        trailing(oms, "none", trail=0)
    with pytest.raises(OMSError, match="positive trail amount"):
        trailing(oms, "pct", trail=100, percent=True)
    with pytest.raises(OMSError, match="only for trailing stop"):
        oms.submit(OrderRequest("lim", "SPY", Side.BUY, 1, OrderType.LIMIT, 40.0, trail_amount=1.0))


def test_modify_a_limit_order_through_the_risk_gate(stack):  # noqa: F811
    oms = stack["oms"]
    r = oms.submit(OrderRequest("lim", "SPY", Side.BUY, 10, OrderType.LIMIT, 40.0))
    assert r.state is S.ACCEPTED
    m = oms.modify(r.order.id, quantity=20, limit_price=45.0)
    assert m.status == "applied" and m.terms.quantity == 20 and m.terms.limit_price == 45.0
    assert stack["broker"]._state["working"][r.order.id]["quantity"] == 20
    refused = oms.modify(r.order.id, quantity=500)  # max_order_quantity = 100
    assert refused.status == "refused" and any("order quantity" in x for x in refused.reasons)
    assert refused.terms.quantity == 20  # unchanged
    with stack["sessions"]() as s:
        assert [a.status for a in queries.amendments(s, r.order.id)] == ["requested", "applied", "requested",
                                                                         "refused"]  # fmt: skip
        assert queries.terms(s, s.get(queries.Order, r.order.id)).quantity == 20
    filled = oms.modify(r.order.id, limit_price=51.0)  # now marketable: fills at the ask
    assert filled.status == "applied"
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.FILLED
        (fill,) = [f for f in queries.fills(s, "paper") if f.order_id == r.order.id]
        assert fill.quantity == 20  # the amended quantity


def test_modification_rules(stack):  # noqa: F811
    oms = stack["oms"]
    r = oms.submit(OrderRequest("lim", "SPY", Side.BUY, 10, OrderType.LIMIT, 40.0))
    with pytest.raises(OMSError, match="nothing to change"):
        oms.modify(r.order.id, limit_price=40.0)
    with pytest.raises(OMSError, match="no stop price"):
        oms.modify(r.order.id, stop_price=39.0)
    with pytest.raises(OMSError, match="positive"):
        oms.modify(r.order.id, quantity=-1)
    with pytest.raises(OMSError, match="no order"):
        oms.modify("missing", quantity=1)
    done = buy(oms, "mkt")
    with pytest.raises(OMSError, match="FILLED"):
        oms.modify(done.order.id, quantity=5)
    stack["broker"].fail_next = None
    with stack["sessions"]() as s:
        stack["kill"].engage(s, actor="owner", reason="test", channel="test")
        s.commit()
    assert oms.modify(r.order.id, quantity=11).status == "refused"  # the kill switch blocks changes too
    assert oms.cancel(r.order.id) is S.CANCELLED  # cancelling always works


def test_amended_terms_drive_exposure_checks(stack):  # noqa: F811
    """A working buy enlarged by a modification counts with its new size in later risk checks."""
    oms = stack["oms"]
    r = oms.submit(OrderRequest("lim", "SPY", Side.BUY, 10, OrderType.LIMIT, 40.0))
    assert oms.modify(r.order.id, quantity=90).status == "applied"  # 90 x 50 = 4 500 reserved
    big = oms.submit(OrderRequest("next", "SPY", Side.BUY, 70, OrderType.LIMIT, 40.0))
    assert big.state is S.RISK_REJECTED  # position notional 8 000 incl. the amended working buy
