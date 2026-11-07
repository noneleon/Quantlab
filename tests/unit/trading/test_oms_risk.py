"""OMS + central risk gate + kill switch + reconciliation on the paper broker (synthetic SIMULATED quotes)."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DatabaseError

from quantlab.brokers.base import OrderType, Side
from quantlab.brokers.paper import PaperBroker
from quantlab.core.clock import ManualClock
from quantlab.core.db import session_factory
from quantlab.data.market_data import DataState, Quote
from quantlab.oms.models import OrderEvent, OrderRecordError
from quantlab.oms.service import OMS, OMSError, OrderRequest
from quantlab.oms.states import OrderState as S
from quantlab.risk.gate import DISENGAGE_CONFIRMATION, KillSwitch, RiskError, RiskGate
from quantlab.risk.limits import RiskConfigError, load_limits, write_template
from quantlab.risk.models import RiskDecision

T0 = datetime(2026, 11, 9, 15, 0, tzinfo=UTC)  # Monday 10:00 New York: XNYS regular session

LIMITS = """[limits]
max_order_quantity = 100
max_order_notional = 5000.0
max_position_notional = 8000.0
max_gross_exposure_fraction = 0.5
max_open_orders = 3
max_orders_per_minute = 50
daily_loss_limit_fraction = 0.02
max_drawdown_fraction = 0.10
max_rejections_per_hour = 50
allow_margin = false
max_quote_age_s = 15.0
max_spread_bps = 50.0
allow_short = false
require_live_quotes = false
trading_calendar = "none"
"""


class Quotes:
    def __init__(self, clock):
        self.clock = clock
        self.px = {"SPY": 50.0}
        self.state = DataState.SIMULATED
        self.age = timedelta(0)

    def __call__(self, symbol):
        if symbol not in self.px:
            return None
        p = self.px[symbol]
        ts = self.clock.now() - self.age
        return Quote(symbol, self.state, "test", ts, ts, bid=p - 0.01, ask=p + 0.01, last=p)


@pytest.fixture
def stack(db_engine, tmp_path):
    clock = ManualClock(T0)
    quotes = Quotes(clock)
    limits = tmp_path / "risk_limits.toml"
    limits.write_text(LIMITS)
    sessions = session_factory(db_engine)
    broker = PaperBroker(
        tmp_path / "paper.json", quotes, starting_cash=20_000.0, slippage_bps=0, now=clock.now
    )
    kill = KillSwitch(clock)
    oms = OMS(
        sessions,
        broker,
        RiskGate(limits, kill, clock),
        quotes,
        account="paper",
        starting_cash=20_000.0,
        clock=clock,
    )
    return {
        "oms": oms,
        "broker": broker,
        "quotes": quotes,
        "clock": clock,
        "limits": limits,
        "sessions": sessions,
        "kill": kill,
        "engine": db_engine,
        "tmp": tmp_path,
    }


def req(key, qty=10, side=Side.BUY, **kw):
    return OrderRequest(key, "SPY", side, qty, **kw)


def history(stack, order_id):
    with stack["sessions"]() as s:
        return [e.state for e in stack["oms"].events(s, order_id)]


def test_happy_path_fill_updates_book_from_confirmed_fill(stack):
    r = stack["oms"].submit(req("k1"))
    assert r.state is S.FILLED and r.decision.approved and not r.duplicate
    assert history(stack, r.order.id) == ["NEW", "PENDING_SUBMIT", "SUBMITTED", "ACCEPTED", "FILLED"]
    with stack["sessions"]() as s:
        book = stack["oms"].book(s)
        assert book.quantity("SPY") == 10 and book.cash == pytest.approx(20_000 - 500.1 - 1.0)
        decision = s.query(RiskDecision).one()
        assert decision.approved and decision.limits_sha256 == load_limits(stack["limits"]).sha256


def test_idempotency_never_sends_twice(stack):
    first = stack["oms"].submit(req("same"))
    again = stack["oms"].submit(req("same", qty=99))
    assert again.duplicate and again.order.id == first.order.id and again.state is S.FILLED
    assert len(stack["broker"].executions()) == 1


@pytest.mark.parametrize(
    ("change", "check"),
    [
        (lambda st: None, None),
        (lambda st: st["quotes"].px.update(SPY=600.0), "order notional"),
        (lambda st: setattr(st["quotes"], "age", timedelta(seconds=60)), "quote age"),
        (lambda st: st["quotes"].px.pop("SPY"), "quote"),
    ],
)
def test_risk_rejections_are_recorded_and_nothing_reaches_the_broker(stack, change, check):
    change(stack)
    r = stack["oms"].submit(req("k", qty=10 if check != "order notional" else 9))
    if check is None:
        assert r.state is S.FILLED
        return
    assert r.state is S.RISK_REJECTED and any(c["check"] == check and not c["ok"] for c in r.decision.checks)
    assert stack["broker"].executions() == [] and stack["broker"].open_order_ids() == set()


def test_quantity_short_and_live_quote_rules(stack):
    oms = stack["oms"]
    assert oms.submit(req("big", qty=101)).state is S.RISK_REJECTED
    assert oms.submit(req("short", side=Side.SELL)).state is S.RISK_REJECTED
    stack["limits"].write_text(LIMITS.replace("require_live_quotes = false", "require_live_quotes = true"))
    r = oms.submit(req("sim"))
    assert r.state is S.RISK_REJECTED and "SIMULATED (LIVE required)" in " ".join(r.decision.reasons)


def test_missing_or_invalid_limits_reject_everything(stack):
    stack["limits"].unlink()
    r = stack["oms"].submit(req("k"))
    assert (
        r.state is S.RISK_REJECTED and "no risk limits file" in r.decision.reasons[0] + r.decision.reasons[-1]
    )
    stack["limits"].write_text("[limits]\nmax_order_quantity = 1\n")
    assert stack["oms"].submit(req("k2")).state is S.RISK_REJECTED
    with pytest.raises(RiskConfigError):
        load_limits(stack["limits"])


def test_template_is_valid_and_never_overwritten(tmp_path):
    path = write_template(tmp_path / "risk_limits.toml")
    assert load_limits(path).limits.trading_calendar == "XNYS"
    with pytest.raises(RiskConfigError, match="never overwritten"):
        write_template(path)


def test_kill_switch_blocks_and_needs_typed_confirmation(stack):
    with stack["sessions"]() as s:
        stack["kill"].engage(s, actor="owner", reason="test")
        s.commit()
    r = stack["oms"].submit(req("k"))
    assert r.state is S.RISK_REJECTED and "kill switch" in r.decision.reasons[0]
    with stack["sessions"]() as s:
        with pytest.raises(RiskError, match=DISENGAGE_CONFIRMATION):
            stack["kill"].disengage(s, actor="owner", reason="done", confirmation="yes")
        with pytest.raises(RiskError):
            stack["kill"].engage(s, actor="owner", reason=" ")
        stack["clock"].advance(timedelta(seconds=1))
        stack["kill"].disengage(s, actor="owner", reason="done", confirmation=DISENGAGE_CONFIRMATION)
        s.commit()
    assert stack["oms"].submit(req("k2")).state is S.FILLED


def test_daily_loss_limit_allows_only_reducing_orders(stack):
    oms, quotes = stack["oms"], stack["quotes"]
    assert oms.submit(req("buy", qty=80)).state is S.FILLED  # start-of-day equity recorded at the first check
    quotes.px["SPY"] = 45.0  # 80 x -5 = -400 on 20 000 -> 2 % loss
    blocked = oms.submit(req("more", qty=1))
    assert blocked.state is S.RISK_REJECTED and any("daily loss limit" in r for r in blocked.decision.reasons)
    assert oms.submit(req("reduce", qty=40, side=Side.SELL)).state is S.FILLED


def test_limit_order_works_then_fills_and_cancel_flow(stack):
    oms, quotes = stack["oms"], stack["quotes"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=49.0))
    assert r.state is S.ACCEPTED
    quotes.px["SPY"] = 48.5
    oms.process_events()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is S.FILLED
    c = oms.submit(req("lim2", order_type=OrderType.LIMIT, limit_price=40.0))
    assert oms.cancel(c.order.id) is S.CANCELLED
    with pytest.raises(OMSError, match="cannot be cancelled"):
        oms.cancel(c.order.id)


def test_open_orders_limit(stack):
    oms = stack["oms"]
    for i in range(3):
        assert oms.submit(req(f"w{i}", order_type=OrderType.LIMIT, limit_price=10.0)).state is S.ACCEPTED
    r = oms.submit(req("w3", order_type=OrderType.LIMIT, limit_price=10.0))
    assert r.state is S.RISK_REJECTED and any("open orders" in x for x in r.decision.reasons)


@pytest.mark.parametrize(("mode", "resolved"), [("unknown", S.NOT_PLACED), ("unknown_placed", S.FILLED)])
def test_submit_unknown_is_resolved_only_by_reconciliation(stack, mode, resolved):
    oms, broker = stack["oms"], stack["broker"]
    broker.fail_next = mode
    r = oms.submit(req("u"))
    assert "SUBMIT_UNKNOWN" in history(stack, r.order.id)
    assert oms.submit(req("u")).duplicate  # retrying the same key never resubmits
    report = oms.reconcile()
    with stack["sessions"]() as s:
        assert oms.state(s, r.order.id) is resolved
    if mode == "unknown":  # nothing at the broker: reconciliation decides NOT_PLACED
        assert report.resolved == [{"order_id": r.order.id, "state": "NOT_PLACED"}]
    else:  # the broker's own events resolved it (SUBMIT_UNKNOWN -> ACCEPTED -> FILLED)
        assert history(stack, r.order.id)[-3:] == ["SUBMIT_UNKNOWN", "ACCEPTED", "FILLED"]
    assert len(broker.executions()) == (1 if mode == "unknown_placed" else 0)
    assert report.clean, report


def test_crash_after_pending_submit_is_recovered_by_reconcile(stack, monkeypatch):
    oms, broker = stack["oms"], stack["broker"]

    def crash(order):
        raise KeyboardInterrupt  # process dies between the durable PENDING_SUBMIT and the broker call

    monkeypatch.setattr(broker, "submit", crash)
    with pytest.raises(KeyboardInterrupt):
        oms.submit(req("c"))
    monkeypatch.undo()
    stack["clock"].advance(timedelta(seconds=61))  # past the in-flight grace period
    with stack["sessions"]() as s:
        ((order, state),) = oms.orders(s)
        assert state is S.PENDING_SUBMIT
    oms.reconcile()
    assert history(stack, order.id)[-2:] == ["SUBMIT_UNKNOWN", "NOT_PLACED"]


def test_unexpected_adapter_error_is_unknown_not_rejected(stack, monkeypatch):
    monkeypatch.setattr(stack["broker"], "submit", lambda order: (_ for _ in ()).throw(ValueError("boom")))
    assert stack["oms"].submit(req("e")).state is S.SUBMIT_UNKNOWN


def test_reconciliation_reports_but_never_rewrites_positions(stack):
    import json

    oms = stack["oms"]
    oms.submit(req("k"))
    state_file = stack["tmp"] / "paper.json"
    data = json.loads(state_file.read_text())
    data["positions"]["SPY"]["quantity"] = 7  # broker disagrees
    state_file.write_text(json.dumps(data))
    fresh = PaperBroker(state_file, stack["quotes"], now=stack["clock"].now)
    report = OMS(
        stack["sessions"],
        fresh,
        RiskGate(stack["limits"]),
        stack["quotes"],
        account="paper",
        starting_cash=20_000.0,
        clock=stack["clock"],
    ).reconcile()
    assert report.position_mismatches == [{"symbol": "SPY", "oms": 10, "broker": 7}] and not report.clean
    with stack["sessions"]() as s:
        assert oms.book(s).quantity("SPY") == 10  # confirmed book unchanged


def test_late_broker_event_is_an_anomaly_and_persisted_but_replays_are_silent(stack):
    from quantlab.brokers.base import OrderAccepted, OrderCancelled
    from quantlab.core.audit import AuditEntry

    oms = stack["oms"]
    r = oms.submit(req("k"))
    stack["broker"]._emit(OrderAccepted(r.order.id, "replayed", stack["clock"].now()))  # already in history
    stack["broker"]._save()
    oms.process_events()
    assert oms.anomalies == []
    stack["broker"]._emit(
        OrderCancelled(r.order.id, stack["clock"].now())
    )  # cancel after the fill: real anomaly
    stack["broker"]._save()
    oms.process_events()
    assert any("broker reported CANCELLED while FILLED" in a for a in oms.anomalies)
    with stack["sessions"]() as s:
        assert (
            s.query(AuditEntry).filter_by(action="oms.anomaly").count() == 1
        )  # persisted, not only in memory


def test_only_paper_brokers(stack):
    class Live:
        name, mode = "x", "live"

    with pytest.raises(OMSError, match="only paper brokers"):
        OMS(
            stack["sessions"],
            Live(),
            RiskGate(stack["limits"]),
            stack["quotes"],
            account="live",
            starting_cash=1.0,
        )


def test_trading_records_are_append_only(stack):
    r = stack["oms"].submit(req("k"))
    with stack["sessions"]() as s:
        stack["kill"].engage(s, actor="owner", reason="row for the trigger test")
        s.commit()
    with stack["sessions"]() as s:
        ev = s.query(OrderEvent).filter_by(order_id=r.order.id).first()
        ev.state = "CANCELLED"
        with pytest.raises(OrderRecordError):
            s.flush()
    for table in (
        "orders",
        "order_events",
        "fills",
        "risk_decisions",
        "kill_switch_events",
        "oms_daily_equity",
    ):
        with stack["engine"].connect() as conn, pytest.raises(DatabaseError):
            conn.execute(text(f"DELETE FROM {table}"))


def test_trading_session_check(stack):
    stack["limits"].write_text(LIMITS.replace('trading_calendar = "none"', 'trading_calendar = "XNYS"'))
    assert stack["oms"].submit(req("open")).state is S.FILLED  # Monday 10:00 New York
    stack["clock"].advance(timedelta(hours=8))  # 23:00 UTC: closed
    stack["quotes"].age = timedelta(0)
    r = stack["oms"].submit(req("closed"))
    assert r.state is S.RISK_REJECTED and any("trading session" in x for x in r.decision.reasons)


def test_working_orders_count_towards_position_and_exposure_limits(stack):
    """Review finding 1: resting orders could multiply the per-symbol limit."""
    oms = stack["oms"]
    stack["limits"].write_text(LIMITS.replace("max_open_orders = 3", "max_open_orders = 10"))
    # each order: 90 x 49 = 4 410 notional (< 5 000); position limit 8 000
    first = oms.submit(req("r1", qty=90, order_type=OrderType.LIMIT, limit_price=49.0))
    assert first.state is S.ACCEPTED
    second = oms.submit(req("r2", qty=90, order_type=OrderType.LIMIT, limit_price=49.0))
    assert second.state is S.RISK_REJECTED  # 180 x 50.01 worst case > 8 000
    assert any("incl. working orders" in r for r in second.decision.reasons)


def test_far_away_limit_or_stop_cannot_shrink_the_checked_notional(stack):
    """Review finding 2: orders were valued at a price far from where they fill."""
    oms = stack["oms"]
    stack["limits"].write_text(
        LIMITS.replace("allow_short = false", "allow_short = true").replace(
            "allow_margin = false", "allow_margin = true"
        )
    )
    low = oms.submit(req("s1", qty=99, side=Side.SELL, order_type=OrderType.LIMIT, limit_price=0.01))
    notional = next(c for c in low.decision.checks if c["check"] == "order notional")
    assert notional["detail"].startswith("4949.01")  # valued at the 49.99 bid, not at the 0.01 limit (0.99)
    stop = oms.submit(req("b1", qty=60, order_type=OrderType.STOP, stop_price=100.0))
    assert stop.state is S.RISK_REJECTED  # valued at the 100 stop, not the 50 touch: 6 000 > 5 000
    assert any("order notional: 6000.00" in r for r in stop.decision.reasons)


def test_colliding_exec_id_is_an_anomaly_not_silently_dropped(stack):
    """Review finding 3."""
    from quantlab.brokers.base import ExecutionReport

    oms, broker = stack["oms"], stack["broker"]
    a = oms.submit(req("a"))
    b = oms.submit(req("b", order_type=OrderType.LIMIT, limit_price=10.0))
    (fill,) = [e for e in broker.executions() if e.order_id == a.order.id]
    broker._emit(
        ExecutionReport(b.order.id, fill.exec_id, "SPY", Side.BUY, 3, 10.0, 1.0, stack["clock"].now())
    )
    broker._save()
    oms.process_events()
    assert any("NOT recorded" in x for x in oms.anomalies)
    with stack["sessions"]() as s:
        assert oms.state(s, b.order.id) is S.ACCEPTED


def test_fills_are_labelled_with_their_price_state(stack):
    """Review finding 4: simulated fills must never look like market P&L."""
    from quantlab.oms import queries

    stack["oms"].submit(req("k"))
    with stack["sessions"]() as s:
        states = queries.fill_price_states(s, "paper")
        assert states == ["SIMULATED"] and "not a performance claim" in queries.simulation_warning(states)
        assert queries.simulation_warning(["LIVE"], paper=False) is None  # only real-broker LIVE fills


def test_unresolved_submission_blocks_new_orders_until_reconciled(stack):
    """Review finding 11."""
    oms = stack["oms"]
    stack["broker"].fail_next = "unknown"
    oms.submit(req("u"))
    blocked = oms.submit(req("v"))
    assert blocked.state is S.RISK_REJECTED and any("unresolved" in r for r in blocked.decision.reasons)
    oms.reconcile()
    assert oms.submit(req("w")).state is S.FILLED


def test_starting_cash_cannot_change_after_first_use(stack):
    """Review finding 10."""
    stack["oms"].submit(req("k"))
    other = OMS(
        stack["sessions"],
        stack["broker"],
        RiskGate(stack["limits"]),
        stack["quotes"],
        account="paper",
        starting_cash=1_000_000.0,
        clock=stack["clock"],
    )
    with pytest.raises(OMSError, match="new paper account name"):
        other.submit(req("k2"))


def test_kill_switch_order_ignores_the_wall_clock(stack):
    """Review finding 7: a clock stepping backwards must not hide an engage."""
    from quantlab.core.clock import ManualClock

    with stack["sessions"]() as s:
        KillSwitch(ManualClock(T0 + timedelta(hours=1))).disengage(
            s, actor="owner", reason="x", confirmation=DISENGAGE_CONFIRMATION
        )
        KillSwitch(ManualClock(T0)).engage(s, actor="owner", reason="clock went back")  # earlier timestamp
        s.commit()
        assert stack["kill"].engaged(s)


def test_missing_bid_ask_and_future_quotes_fail(stack):
    """Review finding 8."""
    from quantlab.data.market_data import DataState, Quote

    oms = stack["oms"]
    now = stack["clock"].now()
    oms._quotes = lambda sym: Quote(sym, DataState.SIMULATED, "t", now, now, last=50.0)
    r = oms.submit(req("nobid"))
    assert r.state is S.RISK_REJECTED and any("spread unknown" in x for x in r.decision.reasons)
    future = now + timedelta(minutes=5)
    oms._quotes = lambda sym: Quote(
        sym, DataState.SIMULATED, "t", now, future, bid=49.99, ask=50.01, last=50.0
    )
    assert oms.submit(req("future")).state is S.RISK_REJECTED


def test_weekdays_calendar_blocks_weekends(stack):
    """Review finding 9."""
    stack["limits"].write_text(LIMITS.replace('trading_calendar = "none"', 'trading_calendar = "weekdays"'))
    stack["clock"].advance(timedelta(days=5))  # Saturday
    r = stack["oms"].submit(req("sat"))
    assert r.state is S.RISK_REJECTED and any("Saturday" in x for x in r.decision.reasons)


def test_refused_cancel_restores_partially_filled(stack, monkeypatch):
    """Review finding 13."""
    from quantlab.brokers.base import BrokerError

    oms = stack["oms"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    monkeypatch.setattr(stack["broker"], "cancel", lambda oid: (_ for _ in ()).throw(BrokerError("too late")))
    assert oms.cancel(r.order.id) is S.ACCEPTED


def test_paper_always_warns_even_on_live_quotes():
    from quantlab.oms.queries import simulation_warning

    assert "simulated executions" in simulation_warning(["LIVE"])
    assert simulation_warning(["LIVE"], paper=False) is None
    assert "SIMULATED quotes" in simulation_warning(["SIMULATED"])


def test_daily_loss_check_uses_market_marks_not_the_order_reference(stack):
    """Second review, finding 1: a far-away limit must not inflate equity in the daily loss check."""
    oms, quotes = stack["oms"], stack["quotes"]
    stack["limits"].write_text(
        LIMITS.replace("max_order_notional = 5000.0", "max_order_notional = 50000.0")
        .replace("max_position_notional = 8000.0", "max_position_notional = 50000.0")
        .replace("max_gross_exposure_fraction = 0.5", "max_gross_exposure_fraction = 5.0")
    )
    assert oms.submit(req("buy", qty=100)).state is S.FILLED  # 100 SPY at ~50
    quotes.px["SPY"] = 45.5  # -450 -> 2.25 % below start of day
    r = oms.submit(
        req("more", qty=1, order_type=OrderType.LIMIT, limit_price=60.0)
    )  # reference 60 would inflate
    assert r.state is S.RISK_REJECTED and any("daily loss limit" in x for x in r.decision.reasons)


# ---- third safety review (CHANGE-0027) ------------------------------------------------------------------


def test_late_fill_after_cancel_is_recorded_and_reported_never_hidden_as_a_replay(stack):
    """M3: a fill whose state the order already passed through (PARTIALLY_FILLED) is not a replay."""
    from quantlab.brokers.base import ExecutionReport
    from quantlab.core.audit import AuditEntry

    oms, broker, now = stack["oms"], stack["broker"], stack["clock"].now
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    broker._emit(ExecutionReport(r.order.id, "X-1", "SPY", Side.BUY, 3, 10.0, 1.0, now()))
    broker._save()
    oms.process_events()
    assert oms.cancel(r.order.id) is S.CANCELLED  # after the partial fill
    broker._emit(ExecutionReport(r.order.id, "X-2", "SPY", Side.BUY, 2, 10.0, 1.0, now()))  # late fill
    broker._save()
    oms.process_events()
    assert any("broker reported PARTIALLY_FILLED while CANCELLED" in a for a in oms.anomalies)
    with stack["sessions"]() as s:
        assert oms.book(s).quantity("SPY") == 5  # the fill itself is recorded (DEC-0008)
        assert oms.state(s, r.order.id) is S.CANCELLED
        assert s.query(AuditEntry).filter_by(action="oms.anomaly").count() == 1


def test_reconcile_reads_one_broker_snapshot(stack, monkeypatch):
    """M3: open orders, executions, positions and cash come from a single consistent read."""
    oms, broker = stack["oms"], stack["broker"]
    oms.submit(req("k"))
    for name in ("open_order_ids", "executions", "positions", "cash"):
        monkeypatch.setattr(broker, name, lambda *a, n=name: pytest.fail(f"reconcile called {n}()"))
    calls = []
    real = PaperBroker.snapshot
    monkeypatch.setattr(broker, "snapshot", lambda: calls.append(1) or real(broker))
    report = oms.reconcile()
    assert calls == [1] and report.clean


def test_young_pending_cancel_is_not_resolved_while_the_order_is_still_working(stack):
    """M3: another process may be between its PENDING_CANCEL commit and its broker call."""
    oms, clock = stack["oms"], stack["clock"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    with stack["sessions"]() as s:
        oms._event(s, r.order.id, S.PENDING_CANCEL, {})  # the other process's committed intent
        s.commit()
    report = oms.reconcile()
    assert report.open_order_mismatches == [
        {"order_id": r.order.id, "state": "PENDING_CANCEL", "broker": "cancel may be in flight"}
    ]
    assert history(stack, r.order.id)[-1] == "PENDING_CANCEL"
    clock.advance(timedelta(seconds=61))
    stack["quotes"].age = timedelta(0)
    report = oms.reconcile()
    assert report.resolved == [{"order_id": r.order.id, "state": "ACCEPTED"}]


def test_terminal_order_still_working_at_the_broker_is_reported(stack):
    """M3: an order the OMS thinks is finished but the broker still works is invisible to the risk gate."""
    oms = stack["oms"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    with stack["sessions"]() as s:
        oms._event(s, r.order.id, S.CANCELLED, {"note": "test: OMS and broker disagree"})
        s.commit()
    report = oms.reconcile()
    assert {"order_id": r.order.id, "state": "CANCELLED", "broker": "still working at broker"} in (
        report.open_order_mismatches
    )
    assert not report.clean


def test_cancel_refused_after_reconcile_resolved_it_does_not_crash(stack, monkeypatch):
    """m1: reconcile in another process restored ACCEPTED before this cancel's broker call was refused."""
    from quantlab.brokers.base import BrokerError

    oms = stack["oms"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))

    def refused(order_id):
        with stack["sessions"]() as s:  # the other process's reconcile
            oms._event(s, order_id, S.ACCEPTED, {"resolved_by": "reconciliation"})
            s.commit()
        raise BrokerError("too late")

    monkeypatch.setattr(stack["broker"], "cancel", refused)
    assert oms.cancel(r.order.id) is S.ACCEPTED


def test_repeated_reconcile_records_an_anomaly_once(stack):
    """m2: reconcile re-applies every execution; the same anomaly is audited once."""
    from quantlab.core.audit import AuditEntry

    oms, broker = stack["oms"], stack["broker"]
    broker._state["executions"].append(
        {
            "order_id": "UNKNOWN",
            "exec_id": "E-x",
            "symbol": "SPY",
            "side": Side.BUY.value,
            "quantity": 1.0,
            "price": 50.0,
            "commission": 1.0,
            "ts": stack["clock"].now().isoformat(),
            "price_state": "SIMULATED",
        }
    )
    broker._save()
    for _ in range(3):
        oms.reconcile()
    with stack["sessions"]() as s:
        assert s.query(AuditEntry).filter_by(action="oms.anomaly").count() == 1
    assert len(oms.anomalies) == 1


def test_reducing_exemption_needs_no_working_order_on_the_symbol(stack):
    """m5: an opposite-side working order could flip the position, so "reduces" no longer exempts."""
    oms, quotes = stack["oms"], stack["quotes"]
    assert oms.submit(req("buy", qty=80)).state is S.FILLED
    rest = oms.submit(req("rest", qty=10, order_type=OrderType.LIMIT, limit_price=1.0))
    assert rest.state is S.ACCEPTED
    quotes.px["SPY"] = 45.0  # 2 % daily loss: only reducing orders
    blocked = oms.submit(req("reduce", qty=40, side=Side.SELL))
    assert blocked.state is S.RISK_REJECTED
    assert any("daily loss limit" in r for r in blocked.decision.reasons)
    assert oms.cancel(rest.order.id) is S.CANCELLED
    assert oms.submit(req("reduce2", qty=40, side=Side.SELL)).state is S.FILLED


def test_order_working_at_broker_but_finished_in_oms_blocks_new_orders(stack):
    """Fourth review: such an order is invisible to the exposure checks, so the gate fails closed."""
    oms = stack["oms"]
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    with stack["sessions"]() as s:
        oms._event(s, r.order.id, S.CANCELLED, {"note": "test: OMS and broker disagree"})
        s.commit()
    blocked = oms.submit(req("next"))
    assert blocked.state is S.RISK_REJECTED
    assert any("unresolved submissions" in x for x in blocked.decision.reasons)


def test_distinct_late_fills_are_each_audited(stack):
    """Fourth review: anomaly dedupe must not merge two different fills."""
    from quantlab.brokers.base import ExecutionReport
    from quantlab.core.audit import AuditEntry

    oms, broker, now = stack["oms"], stack["broker"], stack["clock"].now
    r = oms.submit(req("lim", order_type=OrderType.LIMIT, limit_price=10.0))
    assert oms.cancel(r.order.id) is S.CANCELLED
    for x in ("X-1", "X-2"):
        broker._emit(ExecutionReport(r.order.id, x, "SPY", Side.BUY, 1, 10.0, 1.0, now()))
    broker._save()
    oms.process_events()
    with stack["sessions"]() as s:
        assert s.query(AuditEntry).filter_by(action="oms.anomaly").count() == 2


# ---- Phase 5b-1: drawdown, buying power, rejection cooldown, cancel-all ---------------------------------

WIDE = (
    LIMITS.replace("max_order_quantity = 100", "max_order_quantity = 1000")
    .replace("max_order_notional = 5000.0", "max_order_notional = 50000.0")
    .replace("max_position_notional = 8000.0", "max_position_notional = 50000.0")
    .replace("max_gross_exposure_fraction = 0.5", "max_gross_exposure_fraction = 5.0")
    .replace("max_open_orders = 3", "max_open_orders = 10")
)


def test_drawdown_from_high_water_mark_allows_only_reducing_orders(stack):
    from quantlab.core.ids import new_id
    from quantlab.oms.models import DailyEquity

    oms = stack["oms"]
    assert oms.submit(req("buy", qty=20)).state is S.FILLED  # today's start-of-day equity ~20 000
    with stack["sessions"]() as s:  # an earlier day at 25 000: today is 20 % below the high-water mark
        s.add(DailyEquity(id=new_id(), account="paper", day="2026-11-02", equity=25_000.0, created_at=T0))
        s.commit()
    blocked = oms.submit(req("more", qty=1))
    assert blocked.state is S.RISK_REJECTED
    assert any(r.startswith("drawdown limit: 0.2") for r in blocked.decision.reasons)
    assert oms.submit(req("reduce", qty=10, side=Side.SELL)).state is S.FILLED


def test_cash_account_reserves_buying_power_for_working_buys(stack):
    oms = stack["oms"]
    stack["limits"].write_text(WIDE)
    rest = oms.submit(req("rest", qty=300, order_type=OrderType.LIMIT, limit_price=49.0))
    assert rest.state is S.ACCEPTED  # 300 x 50.01 reference = 15 003 of 20 000 cash
    big = oms.submit(req("big", qty=200))  # 200 x 50.01 + reserved 300 x 50.01 > 20 000
    assert big.state is S.RISK_REJECTED
    assert any(r.startswith("buying power:") and "reserved" in r for r in big.decision.reasons)
    assert oms.submit(req("small", qty=50)).state is S.FILLED  # 2 500 + 15 003 fits
    assert oms.submit(req("sell", qty=10, side=Side.SELL)).state is S.FILLED  # sells need no buying power
    stack["limits"].write_text(WIDE.replace("allow_margin = false", "allow_margin = true"))
    margin = oms.submit(req("big2", qty=200))
    assert not any(c["check"] == "buying power" for c in margin.decision.checks)


def test_rejection_cooldown_blocks_new_orders_for_an_hour(stack):
    oms, clock = stack["oms"], stack["clock"]
    stack["limits"].write_text(LIMITS.replace("max_rejections_per_hour = 50", "max_rejections_per_hour = 2"))
    for i in range(2):
        assert oms.submit(req(f"bad{i}", qty=1000)).state is S.RISK_REJECTED  # over max_order_quantity
    cooled = oms.submit(req("good"))
    assert cooled.state is S.RISK_REJECTED
    assert any(r.startswith("rejection cooldown: 2 rejected") for r in cooled.decision.reasons)
    clock.advance(timedelta(minutes=61))  # the window only counts the last hour
    assert oms.submit(req("good2")).state is S.FILLED


def test_limits_files_from_older_versions_fail_closed_with_a_hint(stack):
    old = "\n".join(
        line
        for line in LIMITS.splitlines()
        if not line.startswith(("max_drawdown_fraction", "max_rejections_per_hour", "allow_margin"))
    )
    stack["limits"].write_text(old + "\n")
    r = stack["oms"].submit(req("k"))
    assert r.state is S.RISK_REJECTED
    assert any("quantlab risk template" in x for x in r.decision.reasons)


def test_cancel_all_cancels_every_working_order_and_is_audited(stack):
    from quantlab.core.audit import AuditEntry

    oms = stack["oms"]
    filled = oms.submit(req("mkt"))
    a = oms.submit(req("a", order_type=OrderType.LIMIT, limit_price=10.0))
    b = oms.submit(req("b", qty=5, side=Side.SELL, order_type=OrderType.LIMIT, limit_price=99.0))
    out = oms.cancel_all(reason="kill switch: test")
    assert out.results == {a.order.id: "CANCELLED", b.order.id: "CANCELLED"} and out.complete
    with stack["sessions"]() as s:
        assert oms.state(s, filled.order.id) is S.FILLED  # finished orders untouched
        assert s.query(AuditEntry).filter_by(action="oms.cancel_all").count() == 1
        assert s.query(AuditEntry).filter_by(action="oms.cancel_all.result").count() == 1
    assert stack["broker"].open_order_ids() == set()
    again = oms.cancel_all(reason="again")
    assert again.results == {} and again.complete


def test_cancel_all_reports_orders_it_could_not_cancel(stack, monkeypatch):
    """5b-1 review M2: refused cancels and in-flight submissions are never reported as done."""
    from quantlab.brokers.base import BrokerError

    oms = stack["oms"]
    a = oms.submit(req("a", order_type=OrderType.LIMIT, limit_price=10.0))
    inflight = oms.submit(req("b", order_type=OrderType.LIMIT, limit_price=11.0))
    with stack["sessions"]() as s:  # simulate another process's in-flight order (not cancellable yet)
        oms._event(s, inflight.order.id, S.PENDING_CANCEL, {})
        s.commit()
    monkeypatch.setattr(stack["broker"], "cancel", lambda oid: (_ for _ in ()).throw(BrokerError("refused")))
    out = oms.cancel_all(reason="kill switch: test")
    assert out.results[a.order.id] == "ACCEPTED" and not out.complete
    assert out.still_working[a.order.id] == "ACCEPTED"
    assert out.still_working[inflight.order.id] == "PENDING_CANCEL" and inflight.order.id not in out.results


def test_cooldown_still_allows_reducing_orders(stack):
    """5b-1 review M1: a cooldown must never stop the owner from getting out of a position."""
    oms = stack["oms"]
    assert oms.submit(req("buy", qty=20)).state is S.FILLED
    stack["limits"].write_text(LIMITS.replace("max_rejections_per_hour = 50", "max_rejections_per_hour = 2"))
    for i in range(3):
        assert oms.submit(req(f"bad{i}", qty=1000)).state is S.RISK_REJECTED
    assert oms.submit(req("more", qty=1)).state is S.RISK_REJECTED
    assert oms.submit(req("out", qty=20, side=Side.SELL)).state is S.FILLED


def test_cash_account_cannot_allow_shorting(tmp_path):
    p = tmp_path / "l.toml"
    p.write_text(LIMITS.replace("allow_short = false", "allow_short = true"))
    with pytest.raises(RiskConfigError, match="cash account cannot sell short"):
        load_limits(p)
