"""LEAN → OMS gateway (OMS-007) over a real local socket, on the paper OMS with synthetic SIMULATED quotes."""

import json
import os
import socket
import threading
import time

import pytest

from quantlab.oms.gateway import Gateway, GatewayError, write_run_file
from quantlab.oms.states import OrderState as S

from .test_oms_risk import req, stack  # noqa: F401  (fixture)


class Client:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.buf = b""

    def send(self, **msg):
        self.sock.sendall(json.dumps(msg).encode() + b"\n")

    def recv(self, timeout=5.0):
        self.sock.settimeout(timeout)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return json.loads(line)

    def until(self, pred, timeout=5.0):
        deadline = time.monotonic() + timeout
        seen = []
        while time.monotonic() < deadline:
            try:
                m = self.recv(timeout=max(0.05, deadline - time.monotonic()))
            except TimeoutError:
                break
            seen.append(m)
            if pred(m):
                return m, seen
        raise AssertionError(f"message not received; got {seen}")

    def close(self):
        self.sock.close()


@pytest.fixture
def gw(stack):  # noqa: F811
    g = Gateway(stack["oms"], stack["sessions"], "momentum-1", poll_s=0.05)
    t = threading.Thread(target=g.serve, daemon=True)
    t.start()
    yield g
    g.stop()
    t.join(timeout=5)


def connect(g, token=None, deployment=None, run=None):
    """run=None: a new algorithm start (the gateway issues the run id in its welcome)."""
    c = Client(g.info.port)
    hello = {"type": "hello", "token": token or g.info.token, "deployment": deployment or g.deployment}
    if run is not None:
        hello["run"] = run
    c.send(**hello)
    return c


def test_orders_go_through_the_risk_gate_and_fills_flow_back(stack, gw):  # noqa: F811
    c = connect(gw)
    welcome = c.recv()
    assert welcome["type"] == "welcome" and welcome["run"].startswith("r") and welcome["orphans"] == []
    c.send(type="place", id=1, symbol="spy", quantity=10, order_type="market")
    fill, _ = c.until(lambda m: m["type"] == "fill")
    assert (fill["id"], fill["quantity"], fill["price_state"]) == (1, 10, "SIMULATED")
    c.until(lambda m: m["type"] == "order" and m["state"] == "FILLED")
    with stack["sessions"]() as s:
        order = stack["oms"].orders(s, limit=None)[0][0]
        assert (
            order.client_order_key == f"lean:momentum-1:{welcome['run']}:1"
            and order.source == "lean:momentum-1"
        )
        assert stack["oms"].book(s).quantity("SPY") == 10  # booked from the broker's fill
    c.send(type="place", id=2, symbol="SPY", quantity=-4)  # negative = sell
    fill, _ = c.until(lambda m: m["type"] == "fill" and m["id"] == 2)
    assert fill["quantity"] == -4
    c.close()


def test_risk_rejection_and_kill_switch_reach_the_algorithm(stack, gw):  # noqa: F811
    c = connect(gw)
    c.recv()
    c.send(type="place", id=1, symbol="SPY", quantity=1000)  # over max_order_quantity
    rejected, _ = c.until(lambda m: m["type"] == "order" and m["id"] == 1)
    assert rejected["state"] == "RISK_REJECTED" and "order quantity" in rejected["reason"]
    with stack["sessions"]() as s:
        stack["kill"].engage(s, actor="owner", reason="test")
        s.commit()
    c.send(type="place", id=2, symbol="SPY", quantity=1)
    blocked, _ = c.until(lambda m: m["type"] == "order" and m["id"] == 2)
    assert blocked["state"] == "RISK_REJECTED" and "kill switch" in blocked["reason"]
    c.close()


def test_a_replayed_place_after_reconnect_is_never_sent_twice(stack, gw):  # noqa: F811
    c = connect(gw)
    run = c.recv()["run"]
    c.send(type="place", id=7, symbol="SPY", quantity=5)
    c.until(lambda m: m["type"] == "fill")
    c.close()
    c2 = connect(gw, run=run)  # reconnect within the same run
    assert c2.recv()["type"] == "welcome"
    replay, seen = c2.until(lambda m: m["type"] == "order" and m["id"] == 7)  # state + fill replayed
    assert replay["state"] == "FILLED"
    assert [m["type"] for m in seen if m.get("id") == 7] == ["fill", "order"]  # fill first
    c2.send(type="place", id=7, symbol="SPY", quantity=5)  # the plugin resends after a reconnect
    c2.until(lambda m: m["type"] == "order" and m["id"] == 7 and m["state"] == "FILLED")
    with stack["sessions"]() as s:
        assert stack["oms"].book(s).quantity("SPY") == 5  # one order, one fill
        assert len(stack["oms"].orders(s, limit=None)) == 1
    c2.close()


def test_cancel_only_reaches_this_deployments_orders(stack, gw):  # noqa: F811
    from quantlab.brokers.base import OrderType

    manual = stack["oms"].submit(req("manual", order_type=OrderType.LIMIT, limit_price=10.0))
    c = connect(gw)
    c.recv()
    c.send(type="place", id=3, symbol="SPY", quantity=2, order_type="limit", limit_price=10.0)
    c.until(lambda m: m["type"] == "order" and m["id"] == 3 and m["state"] == "ACCEPTED")
    c.send(type="cancel", id=3)
    c.until(lambda m: m["type"] == "order" and m["id"] == 3 and m["state"] == "CANCELLED")
    c.send(type="cancel", id=99)
    err, _ = c.until(lambda m: m["type"] == "error")
    assert "no order 99" in err["message"]
    with stack["sessions"]() as s:
        assert stack["oms"].state(s, manual.order.id) is S.ACCEPTED  # untouched
    c.close()


@pytest.mark.parametrize("bad", ["token", "deployment"])
def test_wrong_token_or_deployment_is_refused(stack, gw, bad):  # noqa: F811
    c = connect(
        gw, token="nope" if bad == "token" else None, deployment="other" if bad == "deployment" else None
    )
    assert c.recv() == {"type": "error", "message": "authentication failed"}
    c.send(type="place", id=1, symbol="SPY", quantity=1)
    with pytest.raises((ConnectionError, TimeoutError, OSError)):
        c.recv(timeout=1)
    with stack["sessions"]() as s:
        assert stack["oms"].orders(s, limit=None) == []
    c.close()


def test_malformed_messages_are_errors_not_orders(stack, gw):  # noqa: F811
    c = connect(gw)
    c.recv()
    c.sock.sendall(b"{not json\n")
    assert c.until(lambda m: m["type"] == "error")[0]["message"].startswith("invalid JSON")
    for msg in ({"type": "place", "id": 0, "symbol": "SPY", "quantity": 1},
                {"type": "place", "id": True, "symbol": "SPY", "quantity": 1},
                {"type": "place", "id": 5, "symbol": "SPY", "quantity": 0},
                {"type": "place", "id": 6, "symbol": "SPY", "quantity": 1, "order_type": "iceberg"},
                {"type": "place", "id": 7, "symbol": "SPY", "quantity": "5"},
                {"type": "place", "id": 8, "symbol": "SPY", "quantity": 1, "order_type": "limit",
                 "limit_price": -1},
                {"type": "place", "id": 9, "symbol": "SPY;DROP", "quantity": 1},
                {"type": "disengage"}):  # fmt: skip
        c.send(**msg)
        c.until(lambda m: m["type"] == "error")
    for raw in (b'{"type":"place","id":10,"symbol":"SPY","quantity":' + b"9" * 400 + b"}\n",
                b'{"type":"place","id":11,"symbol":"SPY","quantity":1,"order_type":"limit","limit_price":NaN}\n',
                b"[" * 60000 + b"\n"):  # fmt: skip
        c.sock.sendall(raw)
        c.until(lambda m: m["type"] == "error")
    c.send(type="snapshot")  # the session (and the gateway) are still alive
    c.until(lambda m: m["type"] == "snapshot")
    with stack["sessions"]() as s:
        assert stack["oms"].orders(s, limit=None) == []
    c.close()


def test_snapshot_reports_cash_positions_and_open_orders(stack, gw):  # noqa: F811
    c = connect(gw)
    c.recv()
    c.send(type="place", id=1, symbol="SPY", quantity=10)
    c.until(lambda m: m["type"] == "fill")
    c.send(type="place", id=2, symbol="SPY", quantity=1, order_type="limit", limit_price=10.0)
    c.until(lambda m: m["type"] == "order" and m["id"] == 2 and m["state"] == "ACCEPTED")
    c.send(type="snapshot")
    snap, _ = c.until(lambda m: m["type"] == "snapshot")
    assert snap["positions"] == [{"symbol": "SPY", "quantity": 10.0, "average_price": pytest.approx(50.01)}]
    assert snap["open"] == [2] and snap["cash"] < 20_000
    c.close()


def test_construction_guards(stack, tmp_path):  # noqa: F811
    with pytest.raises(GatewayError, match="deployment names"):
        Gateway(stack["oms"], stack["sessions"], "Bad Name")
    for host in ("0.0.0.0", "localhost", "192.168.1.5"):
        with pytest.raises(GatewayError, match="loopback"):
            Gateway(stack["oms"], stack["sessions"], "ok", host=host)
    g = Gateway(stack["oms"], stack["sessions"], "ok")
    run = tmp_path / "run" / "gateway-ok.json"
    write_run_file(run, g.info)
    assert json.loads(run.read_text())["port"] == g.info.port
    if os.name != "nt":
        assert (run.stat().st_mode & 0o777) == 0o600  # the token is readable by the owner only
    g.stop()


# ---- gateway safety review (blocker + majors) ---------------------------------------------------


def test_a_restarted_algorithm_reusing_lean_ids_gets_new_orders(stack, gw):  # noqa: F811
    """BLOCKER: LEAN numbers orders from 1 on every start; a new run must never inherit the old order 1."""
    c = connect(gw)
    c.recv()
    c.send(type="place", id=1, symbol="SPY", quantity=5)
    c.until(lambda m: m["type"] == "order" and m["id"] == 1 and m["state"] == "FILLED")
    c.close()
    c2 = connect(gw)  # the algorithm restarted: a new run
    c2.recv()
    c2.send(type="snapshot")
    snap, seen = c2.until(lambda m: m["type"] == "snapshot")
    assert not [m for m in seen if m["type"] in ("fill", "order")]  # nothing from run-a is replayed as id 1
    assert snap["positions"][0]["quantity"] == 5.0  # but the deployment's position is known
    c2.send(type="place", id=1, symbol="SPY", quantity=3)
    fill, _ = c2.until(lambda m: m["type"] == "fill" and m["id"] == 1)
    assert fill["quantity"] == 3
    with stack["sessions"]() as s:
        assert len(stack["oms"].orders(s, limit=None)) == 2 and stack["oms"].book(s).quantity("SPY") == 8
    c2.close()


def test_a_replay_with_different_parameters_is_refused(stack, gw):  # noqa: F811
    c = connect(gw)
    c.recv()
    c.send(type="place", id=1, symbol="SPY", quantity=5)
    c.until(lambda m: m["type"] == "order" and m["state"] == "FILLED")
    c.send(type="place", id=1, symbol="SPY", quantity=50)
    err, _ = c.until(lambda m: m["type"] == "error")
    assert "already used in this run" in err["message"]
    c.close()


def test_a_deployment_cannot_sell_positions_it_does_not_hold(stack, gw):  # noqa: F811
    """MAJOR: a manual (or other deployment's) position is not the strategy's to sell."""
    assert stack["oms"].submit(req("manual", qty=20)).state is S.FILLED
    c = connect(gw)
    c.recv()
    c.send(type="snapshot")
    snap, _ = c.until(lambda m: m["type"] == "snapshot")
    assert snap["positions"] == []  # the manual position is not shown to the strategy
    c.send(type="place", id=1, symbol="SPY", quantity=-5)
    err, _ = c.until(lambda m: m["type"] == "error")
    assert "never sell positions they do not hold" in err["message"]
    c.send(type="place", id=2, symbol="SPY", quantity=4)
    c.until(lambda m: m["type"] == "order" and m["id"] == 2 and m["state"] == "FILLED")
    c.send(type="place", id=3, symbol="SPY", quantity=-4)  # its own position: allowed
    c.until(lambda m: m["type"] == "order" and m["id"] == 3 and m["state"] == "FILLED")
    c.close()


def test_slow_unauthenticated_clients_cannot_hold_the_slot(stack, monkeypatch):  # noqa: F811
    import quantlab.oms.gateway as gateway

    monkeypatch.setattr(gateway, "HELLO_TIMEOUT_S", 0.5)
    g = Gateway(stack["oms"], stack["sessions"], "slow", poll_s=0.05)
    t = threading.Thread(target=g.serve, daemon=True)
    t.start()
    try:
        slow = Client(g.info.port)
        for _ in range(4):  # trickles bytes, never a full hello
            slow.sock.sendall(b" ")
            time.sleep(0.2)
        assert slow.recv(timeout=2) == {"type": "error", "message": "authentication failed"}
        ok = connect(g)
        assert ok.recv()["type"] == "welcome"
        ok.close()
    finally:
        g.stop()
        t.join(timeout=5)


def test_lean_keys_are_reserved_for_the_gateway(stack):  # noqa: F811
    from quantlab.oms.service import OMSError

    with pytest.raises(OMSError, match="reserved"):
        stack["oms"].submit(req("lean:momentum-1:run-1:1"))


# ---- second gateway review (CHANGE-0035) -----------------------------------------------------------


def test_run_ids_are_issued_by_the_gateway_and_unknown_ones_refused(stack, gw):  # noqa: F811
    c = connect(gw, run="made-up-run")
    assert c.recv() == {"type": "error", "message": "authentication failed"}
    c.close()
    a = connect(gw)
    run_a = a.recv()["run"]
    a.close()
    b = connect(gw)
    assert b.recv()["run"] != run_a  # a new start never reuses a run id
    b.close()


def test_orders_of_an_earlier_run_block_new_orders_until_cancelled(stack, gw):  # noqa: F811
    """MAJOR: a restarted algorithm must not stack new orders on top of ones it no longer knows."""
    c = connect(gw)
    c.recv()
    c.send(
        type="place",
        id=1,
        symbol="SPY",
        quantity=2,
        order_type="limit",
        limit_price=10.0,
        time_in_force="gtc",
    )
    first, _ = c.until(lambda m: m["type"] == "order" and m["state"] == "ACCEPTED")
    c.close()
    c2 = connect(gw)  # crash + restart
    welcome = c2.recv()
    assert welcome["orphans"] == [first["order_id"]]
    c2.send(type="place", id=1, symbol="SPY", quantity=2)
    err, _ = c2.until(lambda m: m["type"] == "error")
    assert "earlier run are still open" in err["message"] and err["outcome"] == "not_placed"
    c2.send(type="cancel_orphans")
    res, _ = c2.until(lambda m: m["type"] == "orphans")
    assert res["results"] == {first["order_id"]: "CANCELLED"} and res["remaining"] == []
    c2.send(type="place", id=2, symbol="SPY", quantity=2)
    c2.until(lambda m: m["type"] == "order" and m["id"] == 2 and m["state"] == "FILLED")
    c2.close()


def test_a_refused_order_stays_refused_and_is_audited(stack, gw):  # noqa: F811
    from quantlab.core.audit import AuditEntry

    c = connect(gw)
    run = c.recv()["run"]
    c.send(type="place", id=1, symbol="SPY", quantity=-5)  # sell cap: holds nothing
    c.until(lambda m: m["type"] == "error")
    c.send(type="place", id=2, symbol="SPY", quantity=5)
    c.until(lambda m: m["type"] == "order" and m["id"] == 2 and m["state"] == "FILLED")
    c.close()
    c2 = connect(gw, run=run)
    c2.recv()
    c2.send(type="place", id=1, symbol="SPY", quantity=-5)  # stale replay: it now holds 5, still refused
    err, _ = c2.until(lambda m: m["type"] == "error")
    assert "was refused earlier" in err["message"]
    with stack["sessions"]() as s:
        assert s.query(AuditEntry).filter_by(action="gateway.order_refused").count() == 1
    c2.close()


def test_case_variants_of_lean_keys_are_reserved_and_never_counted(stack, gw):  # noqa: F811
    from quantlab.oms.service import OMSError

    with pytest.raises(OMSError, match="reserved"):
        stack["oms"].submit(req("LEAN:momentum-1:x:1"))
    c = connect(gw)
    c.recv()
    c.send(type="snapshot")
    snap, _ = c.until(lambda m: m["type"] == "snapshot")
    assert snap["positions"] == []
    c.close()


def test_a_replay_differing_only_in_time_in_force_is_refused(stack, gw):  # noqa: F811
    c = connect(gw)
    c.recv()
    c.send(type="place", id=1, symbol="SPY", quantity=1, order_type="limit", limit_price=10.0)
    c.until(lambda m: m["type"] == "order" and m["state"] == "ACCEPTED")
    c.send(
        type="place",
        id=1,
        symbol="SPY",
        quantity=1,
        order_type="limit",
        limit_price=10.0,
        time_in_force="gtc",
    )
    err, _ = c.until(lambda m: m["type"] == "error")
    assert "other parameters" in err["message"]
    c.close()
