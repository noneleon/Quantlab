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
    assert read_status(stack["tmp"] / "nope.json", clock.now()) == {
        "running": False,
        "healthy": False,
        "status": None,
    }


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
    with exclusive(stack["tmp"] / "run" / "sup.json.lock"), pytest.raises(LockHeld):
        sup.run(max_ticks=1, sleep=lambda s: None)
    sup.run(max_ticks=2, sleep=lambda s: None)
    assert sup.status.ticks == 2


# ---- supervisor safety review (CHANGE-0033) -------------------------------------------------------


def test_day_orders_of_a_missed_close_expire_after_a_restart_next_day(stack):  # noqa: F811
    """M1: the supervisor was down from before the close until after UTC midnight."""
    oms, clock = stack["oms"], stack["clock"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    make(stack).tick()  # Monday 15:00 UTC: Friday's close is the last one; Monday's order is not touched
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.ACCEPTED
    clock.advance(timedelta(hours=11))  # Tuesday 02:00 UTC: Monday's close (21:00) passed while it was down
    stack["quotes"].age = timedelta(0)
    restarted = make(stack)
    assert restarted.status.day_orders_expired == "2026-11-06"  # restored from the status file
    restarted.tick()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.EXPIRED


def test_a_day_order_placed_after_the_close_belongs_to_the_next_session(stack):  # noqa: F811
    oms, clock = stack["oms"], stack["clock"]
    sup = make(stack)
    sup.tick()
    clock.advance(timedelta(hours=6, minutes=30))  # 21:30 UTC, after Monday's close, before the expiry tick
    late = oms.submit(req("late", order_type=OrderType.LIMIT, limit_price=10.0))
    assert late.state is S.ACCEPTED
    sup.tick()  # expiry for Monday's close runs now
    with stack["sessions"]() as s:
        assert oms.state(s, late.order.id) is S.ACCEPTED  # not created before that close
    clock.advance(timedelta(days=1))  # after Tuesday's close
    sup.tick()
    with stack["sessions"]() as s:
        assert oms.state(s, late.order.id) is S.EXPIRED


@pytest.mark.parametrize("problem", ["crossed", "stale_flag", "old", "delayed"])
def test_equity_samples_need_clean_quotes(stack, problem):  # noqa: F811
    """M2: samples are permanent and feed the high-water mark: a bad quote records nothing."""
    from quantlab.data.market_data import DataState, Quote

    oms, clock = stack["oms"], stack["clock"]
    oms.submit(req("buy"))
    good = stack["quotes"]

    def bad(symbol):
        q = good(symbol)
        now = clock.now()
        if problem == "crossed":
            return Quote(symbol, q.state, "t", now, now, bid=51.0, ask=50.0, last=50.5)
        if problem == "stale_flag":
            return Quote(symbol, q.state, "t", now, now, bid=q.bid, ask=q.ask, last=q.last, flags=("stale",))
        if problem == "old":
            then = now - timedelta(minutes=5)
            return Quote(symbol, q.state, "t", then, then, bid=q.bid, ask=q.ask, last=q.last)
        return Quote(symbol, DataState.DELAYED, "t", now, now, bid=q.bid, ask=q.ask, last=q.last)

    oms._quotes = bad
    assert oms.record_equity(source="supervisor") is None
    oms._quotes = good
    assert oms.record_equity(source="supervisor") is not None
    with stack["sessions"]() as s:
        (sample,) = s.query(EquitySample).all()
        assert sample.source == "supervisor:SIMULATED"  # synthetic prices stay labelled


def test_a_failed_reconcile_is_never_shown_as_clean_or_healthy(stack, monkeypatch):  # noqa: F811
    """M4."""
    oms, clock = stack["oms"], stack["clock"]
    sup = make(stack)
    st = sup.tick()
    assert st.last_reconcile_clean is True and st.healthy is True
    path = stack["tmp"] / "run" / "sup.json"
    assert read_status(path, clock.now())["healthy"] is True

    def down():
        raise ConnectionError("broker down")

    monkeypatch.setattr(oms, "reconcile", down)
    clock.advance(timedelta(seconds=61))
    st = sup.tick()
    assert st.last_reconcile_clean is None and "broker down" in (st.last_reconcile_error or "")
    assert st.healthy is False
    status = read_status(path, clock.now())
    assert status["running"] is True and status["healthy"] is False


def test_status_reader_rejects_future_heartbeats_and_corrupt_files(stack):  # noqa: F811
    clock = stack["clock"]
    path = stack["tmp"] / "run" / "sup.json"
    make(stack).tick()
    assert (
        read_status(path, clock.now() - timedelta(minutes=10))["running"] is False
    )  # heartbeat "in the future"
    path.write_text("{not json")
    broken = read_status(path, clock.now())
    assert broken["running"] is False and "unreadable" in broken["error"]


def test_an_unknown_calendar_fails_at_start(stack):  # noqa: F811
    with pytest.raises(Exception, match=r"XXXX|calendar"):
        make(stack, calendar="XXXX")
