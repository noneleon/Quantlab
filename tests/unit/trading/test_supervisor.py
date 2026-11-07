"""Paper trading supervisor (LIVE-001) on the local paper broker with synthetic SIMULATED quotes."""

import json
from datetime import timedelta

import pytest

from quantlab.brokers.base import OrderType
from quantlab.core.filelock import LockHeld, exclusive
from quantlab.oms.models import DailyEquity, EquitySample
from quantlab.oms.states import OrderState as S
from quantlab.oms.supervisor import Supervisor, read_status

from .test_oms_risk import WIDE, req, stack  # noqa: F401  (fixture)


def make(stack, **kw):  # noqa: F811
    args = {"interval_s": 5, "reconcile_every_s": 60, "equity_every_s": 60, "calendar": "XNYS",
            "clock": stack["clock"], **kw}  # fmt: skip
    return Supervisor(
        stack["oms"], stack["sessions"], stack["kill"], stack["tmp"] / "run" / "sup.json", **args
    )


def test_tick_applies_events_reconciles_on_schedule_and_writes_a_heartbeat(stack):  # noqa: F811
    oms, clock, quotes = stack["oms"], stack["clock"], stack["quotes"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=49.0))
    sup = make(stack)
    quotes.px["SPY"] = 48.5  # crosses the limit: the fill arrives as a broker event
    st = sup.tick()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.FILLED
    assert st.last_reconcile_clean is True and st.errors == []
    first = st.last_reconcile_at
    clock.advance(timedelta(seconds=30))
    assert sup.tick().last_reconcile_at == first  # not due yet
    clock.advance(timedelta(seconds=31))
    assert sup.tick().last_reconcile_at != first
    status = read_status(stack["tmp"] / "run" / "sup.json", clock.now())
    assert status["running"] is True and status["status"]["ticks"] == 3
    stale = read_status(stack["tmp"] / "run" / "sup.json", clock.now() + timedelta(minutes=5))
    assert stale["running"] is False  # a stopped supervisor is visible
    assert read_status(stack["tmp"] / "nope.json", clock.now()) == {"running": False, "status": None}


def test_equity_samples_and_start_of_day_only_during_the_session(stack):  # noqa: F811
    clock = stack["clock"]
    sup = make(stack)
    sup.tick()  # T0 = Monday 15:00 UTC, NYSE open
    with stack["sessions"]() as s:
        assert s.query(EquitySample).count() == 1
        assert s.query(DailyEquity).one().day == "2026-11-09"
    clock.advance(timedelta(seconds=61))
    sup.tick()
    with stack["sessions"]() as s:
        assert s.query(EquitySample).count() == 2 and s.query(DailyEquity).count() == 1
    clock.advance(timedelta(hours=8))  # 23:00 UTC: closed
    sup.tick()
    with stack["sessions"]() as s:
        assert s.query(EquitySample).count() == 2


def test_intraday_peak_from_samples_drives_the_drawdown_limit(stack):  # noqa: F811
    """KI-0073: a peak between start of day and now is no longer missed."""
    oms, clock, quotes = stack["oms"], stack["clock"], stack["quotes"]
    stack["limits"].write_text(WIDE.replace("allow_margin = false", "allow_margin = true"))
    assert oms.submit(req("buy", qty=300)).state is S.FILLED  # start of day ~20 000
    sup = make(stack)
    quotes.px["SPY"] = 70.0  # +6 000: equity ~26 000 sampled
    sup.tick()
    clock.advance(timedelta(seconds=61))
    quotes.px["SPY"] = 52.0  # back near the start of day: ~23 % below the intraday peak
    blocked = oms.submit(req("more", qty=1))
    assert blocked.state is S.RISK_REJECTED
    assert any(r.startswith("drawdown limit: 0.2") for r in blocked.decision.reasons)
    assert not any(r.startswith("daily loss limit") for r in blocked.decision.reasons)


def test_day_orders_expire_once_after_the_close(stack):  # noqa: F811
    oms, clock = stack["oms"], stack["clock"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    sup = make(stack)
    sup.tick()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.ACCEPTED
    clock.advance(timedelta(hours=6, minutes=5))  # 21:05 UTC: after the 21:00 UTC close
    st = sup.tick()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.EXPIRED
    assert st.day_orders_expired == "2026-11-09"


def test_position_mismatch_engages_the_kill_switch_but_never_disengages(stack):  # noqa: F811
    oms = stack["oms"]
    oms.submit(req("buy"))
    path = stack["tmp"] / "paper.json"
    data = json.loads(path.read_text())
    data["positions"]["SPY"]["quantity"] = 7
    path.write_bytes(json.dumps(data).encode())
    with stack["sessions"]() as s:
        assert not stack["kill"].engaged(s)
    st = make(stack).tick()
    with stack["sessions"]() as s:
        assert stack["kill"].engaged(s)
        assert "supervisor" in stack["kill"].state(s).actor
    assert st.kill_switch_engaged_by_supervisor is not None and st.last_reconcile_clean is False


def test_mismatch_engagement_can_be_turned_off(stack):  # noqa: F811
    oms = stack["oms"]
    oms.submit(req("buy"))
    path = stack["tmp"] / "paper.json"
    data = json.loads(path.read_text())
    data["positions"]["SPY"]["quantity"] = 7
    path.write_bytes(json.dumps(data).encode())
    make(stack, engage_on_mismatch=False).tick()
    with stack["sessions"]() as s:
        assert not stack["kill"].engaged(s)


def test_failures_are_recorded_and_the_next_steps_still_run(stack, monkeypatch):  # noqa: F811
    oms = stack["oms"]
    oms.submit(req("buy"))
    stack["quotes"].px.clear()  # held SPY cannot be marked: no equity sample
    sup = make(stack)
    st = sup.tick()
    assert any("equity" in e for e in st.errors) and st.last_reconcile_at is not None
    with stack["sessions"]() as s:
        assert s.query(EquitySample).count() == 0  # an unmarked equity is never stored


def test_supervisor_never_submits_orders(stack, monkeypatch):  # noqa: F811
    monkeypatch.setattr(stack["broker"], "submit", lambda o: pytest.fail("the supervisor submitted an order"))
    sup = make(stack)
    for _ in range(3):
        sup.tick()
        stack["clock"].advance(timedelta(minutes=2))


def test_only_one_supervisor_per_account(stack):  # noqa: F811
    sup = make(stack)
    with exclusive(stack["tmp"] / "run" / "sup.lock"), pytest.raises(LockHeld):
        sup.run(max_ticks=1, sleep=lambda s: None)
    sup.run(max_ticks=2, sleep=lambda s: None)
    assert sup.status.ticks == 2
