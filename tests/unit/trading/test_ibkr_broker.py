"""IBKR paper adapter (BRK-003/004) with a fake IBKR client: paper-only guards, mapping, OMS integration.

No network, no real IBKR account: prices are synthetic test values and the fills are fake broker reports.
"""

from dataclasses import replace
from datetime import timedelta

import pytest

from quantlab.brokers.base import (
    BrokerError,
    BrokerOrder,
    BrokerUnavailable,
    ExecutionReport,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    OrderType,
    Side,
    SubmitUnknown,
)
from quantlab.brokers.ibkr import IBExecRecord, IBKRPaperBroker, IBOrderRecord, IBPositionRecord
from quantlab.core.clock import ManualClock
from quantlab.core.db import session_factory
from quantlab.oms.service import OMS, OrderRequest
from quantlab.oms.states import OrderState as S
from quantlab.risk.gate import KillSwitch, RiskGate

from .test_oms_risk import LIMITS, T0, Quotes

ACCOUNT = "DU1234567"


class FakeIB:
    """Stands in for IbAsyncTradingClient. ``on_place`` decides what IBKR does with a new order."""

    def __init__(self, accounts=(ACCOUNT,)):
        self.accounts = list(accounts)
        self.connected = False
        self.connects = 0
        self.fail_connect: Exception | None = None
        self.orders_: dict[str, IBOrderRecord] = {}
        self.manual: list[IBOrderRecord] = []
        self.execs: list[IBExecRecord] = []
        self.positions_: list[IBPositionRecord] = []
        self.cash_: float | None = 1_000_000.0
        self.placed: list[tuple[BrokerOrder, str]] = []
        self.on_place = self.accept
        self.perm = 1000
        self.now = T0

    # behaviours
    def accept(self, order, account):
        self.perm += 1
        self.orders_[order.order_id] = IBOrderRecord(order.order_id, self.perm, "Submitted", account)

    def fill(self, order_id, price=50.0, commission: float | None = 1.0, shares=None):
        o = self.orders_[order_id]
        order = next(b for b, _ in self.placed if b.order_id == order_id)
        self.execs.append(
            IBExecRecord(
                f"0001f4e8.{len(self.execs) + 1}",
                order_id,
                o.account,
                order.symbol,
                "BOT" if order.side is Side.BUY else "SLD",
                shares or order.quantity,
                price,
                commission,
                self.now,
            )
        )
        self.orders_[order_id] = replace(o, status="Filled")

    # IBTradingClient
    def connect(self, host, port, client_id, timeout):
        self.connects += 1
        if self.fail_connect:
            raise self.fail_connect
        self.connected = True

    def disconnect(self):
        self.connected = False

    def is_connected(self):
        return self.connected

    def managed_accounts(self):
        return list(self.accounts)

    def place(self, order, account):
        self.placed.append((order, account))
        self.on_place(order, account)

    def cancel(self, order_ref):
        o = self.orders_.get(order_ref)
        if o is None or o.status not in ("Submitted", "PreSubmitted"):
            return False
        self.orders_[order_ref] = replace(o, status="Cancelled")
        return True

    def orders(self):
        return [*self.orders_.values(), *self.manual]

    def executions(self, account):
        return [e for e in self.execs if e.account == account]

    def positions(self, account):
        return [p for p in self.positions_ if p.account == account]

    def cash(self, account):
        return self.cash_

    def wait(self, timeout):
        pass


def make(fake=None, **kw):
    fake = fake or FakeIB()
    args = {"host": "127.0.0.1", "port": 4002, "client_id": 17, "live_port": 4001, "ack_timeout_s": 0.05,
            "now": lambda: fake.now, **kw}  # fmt: skip
    return fake, IBKRPaperBroker(fake, **args)


def order(oid="o1", qty=10, side=Side.BUY, kind=OrderType.MARKET, **kw):
    return BrokerOrder(oid, "SPY", side, qty, kind, **kw)


# ---- paper-only guards -----------------------------------------------------------------------------


@pytest.mark.parametrize("port", [4001, 7496, 5555])
def test_live_ports_are_refused_before_connecting(port):
    fake = FakeIB()
    with pytest.raises(BrokerError, match="live trading port"):
        IBKRPaperBroker(fake, host="h", port=port, client_id=17, live_port=5555)
    assert fake.connects == 0


@pytest.mark.parametrize("accounts", [["U1234567"], ["DU1234567", "U7654321"], []])
def test_non_paper_logins_are_refused_and_nothing_is_sent(accounts):
    fake, b = make(FakeIB(accounts))
    with pytest.raises(BrokerError, match="not paper accounts"):
        b.submit(order())
    assert fake.placed == [] and not fake.connected  # disconnected again


def test_account_selection_is_explicit():
    with pytest.raises(BrokerError, match="not an IBKR paper account"):
        make(expected_account="U1234567")
    fake, b = make(FakeIB(["DU1", "DU2"]))
    with pytest.raises(BrokerError, match="several paper accounts"):
        b.positions()
    fake, b = make(FakeIB(["DU1"]), expected_account="DU2")
    with pytest.raises(BrokerError, match="not managed by this login"):
        b.positions()
    fake, b = make(FakeIB(["DU1", "DU2"]), expected_account="DU2")
    b.submit(order())
    assert fake.placed[0][1] == "DU2"  # orders always name the verified paper account


def test_unreachable_gateway_is_unavailable_and_reconnect_reverifies():
    fake, b = make()
    fake.fail_connect = ConnectionRefusedError("refused")
    with pytest.raises(BrokerUnavailable, match="not reachable"):
        b.submit(order())
    fake.fail_connect = None
    b.submit(order("o1"))
    fake.connected = False  # gateway restarted (daily restart)
    fake.accounts = ["U999"]  # and a live login appeared: must be refused on reconnect
    with pytest.raises(BrokerError, match="not paper accounts"):
        b.submit(order("o2"))
    assert [o.order_id for o, _ in fake.placed] == ["o1"]


# ---- order flow ------------------------------------------------------------------------------------


def test_submit_events_and_execution_mapping():
    fake, b = make()
    b.submit(order("o1"))
    assert fake.placed[0][0].order_id == "o1"
    fake.fill("o1", price=50.02, commission=1.0)
    events = [e.event for e in b.events()]
    assert isinstance(events[0], OrderAccepted) and events[0].broker_order_id.startswith("ib-perm:")
    fill = events[1]
    assert isinstance(fill, ExecutionReport)
    assert (fill.order_id, fill.quantity, fill.price, fill.commission) == ("o1", 10, 50.02, 1.0)
    assert fill.price_state == "IBKR_PAPER" and fill.side is Side.BUY
    assert b.open_order_ids() == set()


def test_acknowledge_by_sequence_and_events_are_not_duplicated():
    _fake, b = make()
    b.submit(order("o1", kind=OrderType.LIMIT, limit_price=1.0))
    first = b.events()
    b.submit(order("o2", kind=OrderType.LIMIT, limit_price=1.0))
    second = b.events()
    assert [e.seq for e in second] == [1, 2]  # nothing re-emitted for o1
    b.acknowledge(first[-1].seq)
    assert [e.event.order_id for e in b.events()] == ["o2"]


def test_execution_waits_for_its_commission_report():
    fake, b = make(commission_wait_s=10)
    b.submit(order("o1"))
    fake.fill("o1", commission=None)
    assert not any(isinstance(e.event, ExecutionReport) for e in b.events())
    fake.now = T0 + timedelta(seconds=11)
    (fill,) = [e.event for e in b.events() if isinstance(e.event, ExecutionReport)]
    assert fill.commission == 0.0  # reported after the wait (KI-0074)


def test_no_acknowledgement_or_failure_while_placing_is_unknown_never_rejected():
    fake, b = make()
    fake.on_place = lambda o, a: None  # IBKR never confirms
    with pytest.raises(SubmitUnknown, match="no acknowledgement"):
        b.submit(order("o1"))

    def drop(o, a):
        raise ConnectionResetError("socket closed")

    fake.on_place = drop
    with pytest.raises(SubmitUnknown, match="failed midway"):
        b.submit(order("o2"))


def test_duplicate_reference_is_refused():
    fake, b = make()
    b.submit(order("o1"))
    with pytest.raises(BrokerError, match="duplicate"):
        b.submit(order("o1"))
    assert len(fake.placed) == 1


def test_cancel_and_rejection_events():
    fake, b = make()
    b.submit(order("o1", kind=OrderType.LIMIT, limit_price=1.0))
    b.cancel("o1")
    with pytest.raises(BrokerError, match="not working"):
        b.cancel("o1")

    def reject(o, a):
        fake.orders_[o.order_id] = IBOrderRecord(o.order_id, 7, "Inactive", a, "201: order rejected - margin")

    fake.on_place = reject
    b.submit(order("o2"))
    events = {type(e.event): e.event for e in b.events()}
    assert events[OrderCancelled].order_id == "o1"
    assert isinstance(events[OrderRejected], OrderRejected) and "margin" in events[OrderRejected].reason


def test_orders_not_placed_by_quantlab_are_reported_as_open():
    fake, b = make()
    fake.manual.append(IBOrderRecord("", 555, "Submitted", ACCOUNT))
    assert b.open_order_ids() == {"ib-perm:555"}
    assert b.events() == []  # never mapped to QuantLab orders


# ---- through the OMS ---------------------------------------------------------------------------------


@pytest.fixture
def ib_stack(db_engine, tmp_path):
    clock = ManualClock(T0)
    quotes = Quotes(clock)
    limits = tmp_path / "risk_limits.toml"
    limits.write_text(LIMITS)
    fake, broker = make()
    fake.now = clock.now()
    sessions = session_factory(db_engine)
    oms = OMS(sessions, broker, RiskGate(limits, KillSwitch(clock), clock), quotes, account=f"ibkr-{ACCOUNT}",
              starting_cash=1_000_000.0, clock=clock)  # fmt: skip
    return fake, broker, oms, sessions


def test_oms_accepts_the_ibkr_paper_adapter_and_books_confirmed_fills(ib_stack):
    fake, _broker, oms, sessions = ib_stack
    r = oms.submit(OrderRequest("k1", "SPY", Side.BUY, 10))
    assert r.state is S.ACCEPTED  # IBKR confirmed it; no fill yet
    fake.fill(r.order.id, price=50.01, commission=1.0)
    fake.positions_.append(IBPositionRecord(ACCOUNT, "SPY", 10, 50.01))
    fake.cash_ = 1_000_000.0 - 500.1 - 1.0
    oms.process_events()
    with sessions() as s:
        assert oms.state(s, r.order.id) is S.FILLED
        assert oms.book(s).quantity("SPY") == 10
    report = oms.reconcile()
    assert report.clean, report


def test_unreachable_gateway_rejects_the_order_without_sending(ib_stack):
    fake, _broker, oms, _sessions = ib_stack
    fake.fail_connect = ConnectionRefusedError("down")
    r = oms.submit(OrderRequest("k1", "SPY", Side.BUY, 10))
    assert r.state is S.RISK_REJECTED and fake.placed == []  # the gate already fails closed
    assert any(x.startswith("broker reachable: cannot read") for x in r.decision.reasons)


def test_unknown_submission_is_resolved_by_reconciliation(ib_stack):
    fake, _broker, oms, sessions = ib_stack
    fake.on_place = lambda o, a: None  # sent, never acknowledged
    r = oms.submit(OrderRequest("k1", "SPY", Side.BUY, 10))
    assert r.state is S.SUBMIT_UNKNOWN
    fake.accept(fake.placed[0][0], ACCOUNT)  # IBKR did get it
    oms.reconcile()  # IBKR's acceptance (processed first) resolves it; nothing is resubmitted
    with sessions() as s:
        assert oms.state(s, r.order.id) is S.ACCEPTED
    assert len(fake.placed) == 1


def test_a_manual_tws_order_blocks_new_orders(ib_stack):
    fake, _broker, oms, _sessions = ib_stack
    fake.manual.append(IBOrderRecord("", 555, "Submitted", ACCOUNT))
    r = oms.submit(OrderRequest("k1", "SPY", Side.BUY, 10))
    assert r.state is S.RISK_REJECTED and any("unresolved" in x for x in r.decision.reasons)
    assert fake.placed == []


def test_config_requires_an_explicit_paper_account():
    from pydantic import ValidationError

    from quantlab.core.config import PaperSettings

    with pytest.raises(ValidationError, match="DU"):
        PaperSettings(broker="ibkr", ibkr_starting_cash=1e6)
    with pytest.raises(ValidationError, match="DU"):
        PaperSettings(broker="ibkr", ibkr_account="U1234567", ibkr_starting_cash=1e6)
    with pytest.raises(ValidationError, match="ibkr_starting_cash"):
        PaperSettings(broker="ibkr", ibkr_account=ACCOUNT)
    p = PaperSettings(broker="ibkr", ibkr_account=ACCOUNT, ibkr_starting_cash=1e6)
    assert (p.oms_account, p.oms_starting_cash) == (f"ibkr-{ACCOUNT}", 1e6)
    assert PaperSettings().oms_account == "paper"
