"""LEAN → OMS gateway service (OMS-007, DEC-0007 option A, DEC-0031).

A running LEAN algorithm talks to QuantLab through the ``QuantLabBrokerage`` plugin (C#,
``lean/QuantLabBrokerage``), which connects to this service. Every order the algorithm places becomes an
``OMS.submit`` call — the same path as a manual order: central risk gate, kill switch, stored decision,
idempotency, paper broker. The algorithm never talks to a broker.

Protocol: newline-delimited JSON over TCP on a loopback address. The first message must be ``{"type": "hello",
"token": ..., "deployment": ..., "run": ...}`` within 10 s: the token from the gateway's run file (owner-only,
a new random token per gateway start) and a run id the plugin creates once per algorithm start. Then:

* client → gateway: ``place`` {id (LEAN order id), symbol, quantity (signed: < 0 sells), order_type,
  limit_price, stop_price, time_in_force}; ``cancel`` {id}; ``snapshot`` {}.
* gateway → client: ``welcome`` {account, mode}; ``order`` {id, order_id, state, reason}; ``fill`` {id,
  exec_id, quantity (signed), price, commission, ts, price_state}; ``snapshot`` {cash, positions, open};
  ``error`` {message}.

Scope (DEC-0031): orders are keyed ``lean:<deployment>:<run>:<LEAN id>`` — LEAN restarts its order ids at 1 on
every start, so the run id keeps a restarted algorithm's orders apart from earlier ones, while a ``place``
replayed after a reconnect *within* a run is recognised and never sent twice (a replay with different
parameters is refused). A connection only sees, cancels and replays its own run's orders. Its positions are
its deployment's own (from its fills); a sell may not exceed that position, so a strategy cannot sell
positions it does not hold (manual ones, other deployments'). Cash is the shared paper account's. It cannot
touch the kill switch or the limits. Any malformed message is answered with an error; nothing a client sends
ends the gateway.
"""

from __future__ import annotations

import functools
import hmac
import ipaddress
import json
import math
import os
import re
import secrets
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from quantlab.core.audit import AuditLog
from quantlab.core.ids import new_id
from quantlab.core.logging import get_logger
from quantlab.oms import queries
from quantlab.oms.models import FillRecord, Order
from quantlab.oms.service import OMS, OMSError, OrderRequest, OrderType, Side, TimeInForce
from quantlab.oms.states import TERMINAL, OrderState
from quantlab.portfolio.book import Book, Fill

log = get_logger(__name__)

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RUN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,39}$")
MAX_LINE = 64 * 1024
HELLO_TIMEOUT_S = 10.0
REJECTED_STATES = {OrderState.RISK_REJECTED.value, OrderState.REJECTED.value, OrderState.NOT_PLACED.value}


class GatewayError(RuntimeError):
    pass


@dataclass(frozen=True)
class GatewayInfo:
    deployment: str
    host: str
    port: int
    token: str = field(repr=False)  # never in logs or tracebacks

    def as_json(self) -> str:
        return json.dumps(
            {"deployment": self.deployment, "host": self.host, "port": self.port, "token": self.token}
        )


def write_run_file(path: Path, info: GatewayInfo) -> None:
    """Owner-only run file the LEAN launcher reads to configure the plugin (never logged).

    On Windows the POSIX mode is not applied; the file inherits the QUANTLAB_HOME folder's permissions
    (KI-0077).
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)  # never reuse a file that may have looser permissions
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(info.as_json())
    tmp.replace(path)


class Gateway:
    """One gateway per deployment; serves one plugin connection at a time."""

    def __init__(
        self,
        oms: OMS,
        sessions: sessionmaker[Session],
        deployment: str,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        poll_s: float = 0.5,
        token: str | None = None,
    ) -> None:
        if not NAME_RE.match(deployment):
            raise GatewayError("deployment names are 1-32 characters: lowercase letters, digits and '-'")
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise GatewayError("the gateway binds to a loopback address (127.0.0.1 or ::1) only")
        self._oms = oms
        self._sessions = sessions
        self.deployment = deployment
        self._poll = poll_s
        self._token = token or secrets.token_urlsafe(32)
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        self._server = socket.create_server((host, port), family=family)
        self._server.settimeout(0.2)
        self.info = GatewayInfo(deployment, host, self._server.getsockname()[1], self._token)
        self._stop = threading.Event()
        self._run: str | None = None
        self._issued: set[str] = set()  # run ids this gateway handed out
        self._refused: dict[tuple[str, int], str] = {}  # (run, LEAN id) -> why: a replay stays refused
        self._audit = AuditLog()

    @property
    def _deployment_prefix(self) -> str:
        return f"lean:{self.deployment}:"

    @property
    def _prefix(self) -> str:
        return f"{self._deployment_prefix}{self._run}:"

    # ---- lifecycle --------------------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    def serve(self, *, max_connections: int | None = None) -> None:
        served = 0
        try:
            while not self._stop.is_set() and (max_connections is None or served < max_connections):
                try:
                    conn, _addr = self._server.accept()
                except TimeoutError:
                    continue
                served += 1
                with conn:
                    try:
                        self._session(conn)
                    except Exception as exc:  # nothing a client does may end the gateway
                        log.warning("gateway.connection_ended", deployment=self.deployment, error=repr(exc))
        finally:
            self._server.close()

    # ---- one plugin connection -----------------------------------------------------------------------
    def _session(self, conn: socket.socket) -> None:
        reader = _LineReader(conn)
        send = _sender(conn)
        hello = self._hello(reader)
        if hello is None:
            log.warning("gateway.refused", deployment=self.deployment)
            send({"type": "error", "message": "authentication failed"})
            return
        self._run = hello
        with self._sessions() as session:
            orphans = [o.id for o, st in self._orphans(session)]
        send(
            {
                "type": "welcome",
                "account": self._oms.account,
                "mode": "paper",
                "deployment": self.deployment,
                "run": self._run,
                "orphans": orphans,  # earlier runs' unfinished orders: no new orders until resolved
            }
        )
        log.info("gateway.connected", deployment=self.deployment, run=self._run)
        sent_states: dict[str, str] = {}
        sent_fills: set[str] = set()
        self._guarded(send, lambda: self._push(send, sent_states, sent_fills))  # replay after (re)connect
        while not self._stop.is_set():
            try:
                msg = reader.next_message(timeout_s=self._poll)
            except ConnectionError:
                log.info("gateway.disconnected", deployment=self.deployment)
                return
            except GatewayError as exc:
                send({"type": "error", "message": str(exc)})
                msg = None
            if msg is not None:
                self._guarded(send, functools.partial(self._handle, msg, send), msg)
            self._guarded(send, self._oms.process_events, report=False)
            self._guarded(send, lambda: self._push(send, sent_states, sent_fills), report=False)

    def _hello(self, reader: _LineReader) -> str | None:
        """The run id if the client authenticated within HELLO_TIMEOUT_S overall, else None."""
        deadline = time.monotonic() + HELLO_TIMEOUT_S
        msg = None
        while msg is None and time.monotonic() < deadline:
            try:
                msg = reader.next_message(timeout_s=max(0.05, deadline - time.monotonic()), limit=4096)
            except (GatewayError, ConnectionError):
                return None
        if not isinstance(msg, dict) or msg.get("type") != "hello":
            return None
        if not hmac.compare_digest(str(msg.get("token", "")).encode(), self._token.encode()):
            return None
        if msg.get("deployment") != self.deployment:
            return None
        run = msg.get("run")
        if run is None:  # a new algorithm start: the gateway issues the run id (never reused)
            run = "r" + new_id().lower()
            self._issued.add(run)
            return run
        if not isinstance(run, str) or not RUN_RE.match(run):
            return None
        if run in self._issued:
            return run  # a reconnect within the run
        with self._sessions() as session:  # issued by an earlier gateway process: known by its orders
            known = session.execute(
                select(Order.id)
                .where(Order.client_order_key.startswith(f"{self._deployment_prefix}{run}:", autoescape=True))
                .limit(1)
            ).first()
        if known is None:
            return None
        self._issued.add(run)
        return run

    def _guarded(
        self,
        send: Callable[[dict[str, Any]], None],
        fn: Callable[[], object],
        msg: Any = None,
        *,
        report: bool = True,
    ) -> None:
        try:
            fn()
        except OSError:
            raise  # the connection itself is gone: end this session
        except Exception as exc:  # contained per message / per push; the risk gate fails closed on its own
            log.warning("gateway.step_failed", deployment=self.deployment, error=repr(exc))
            if report:
                ident = msg.get("id") if isinstance(msg, dict) else None
                # "not_placed": refused before anything was stored; "unknown": the order may exist (reconcile)
                definite = isinstance(exc, GatewayError | OMSError | ValueError | TypeError | KeyError)
                send({"type": "error", "id": ident, "message": _short(exc),
                      "reply": msg.get("type") if isinstance(msg, dict) else None,
                      "outcome": "not_placed" if definite else "unknown"})  # fmt: skip

    def _handle(self, msg: Any, send: Callable[[dict[str, Any]], None]) -> None:
        kind = msg.get("type") if isinstance(msg, dict) else None
        if kind == "place":
            self._place(msg, send)
        elif kind == "cancel":
            self._cancel(msg, send)
        elif kind == "snapshot":
            send(self._snapshot())
        elif kind == "cancel_orphans":
            self._cancel_orphans(send)
        else:
            raise GatewayError(f"unknown message type {kind!r}")

    def _place(self, msg: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
        lean_id = _lean_id(msg)
        assert self._run is not None
        if (self._run, lean_id) in self._refused:
            raise GatewayError(f"order id {lean_id} was refused earlier: {self._refused[self._run, lean_id]}")
        try:
            self._place_checked(msg, lean_id, send)
        except GatewayError as exc:  # remembered and audited: a replay after a reconnect stays refused
            self._refused[self._run, lean_id] = str(exc)
            with self._sessions() as session:
                self._audit.record(
                    session, actor_type="system", actor=f"lean:{self.deployment}",
                    action="gateway.order_refused", target=self._oms.account,
                    details={"run": self._run, "id": lean_id, "reason": str(exc)},
                )  # fmt: skip
                session.commit()
            raise

    def _place_checked(
        self, msg: dict[str, Any], lean_id: int, send: Callable[[dict[str, Any]], None]
    ) -> None:
        quantity = _number(msg.get("quantity"), "quantity")
        if quantity == 0:
            raise GatewayError("quantity must not be 0")
        req = OrderRequest(
            client_order_key=f"{self._prefix}{lean_id}",
            symbol=_symbol(msg.get("symbol")),
            side=Side.BUY if quantity > 0 else Side.SELL,
            quantity=abs(quantity),
            order_type=OrderType(str(msg.get("order_type", "market")).lower()),
            limit_price=_price(msg.get("limit_price"), "limit_price"),
            stop_price=_price(msg.get("stop_price"), "stop_price"),
            time_in_force=TimeInForce(str(msg.get("time_in_force", "day")).lower()),
            source=f"lean:{self.deployment}",
        )
        with self._sessions() as session:
            existing = session.execute(
                select(Order).where(Order.client_order_key == req.client_order_key)
            ).scalar_one_or_none()
            if existing is None:
                orphans = self._orphans(session)
                if orphans:
                    raise GatewayError(
                        f"{len(orphans)} order(s) of an earlier run are still open "
                        f"({', '.join(o.id for o, _ in orphans)}): send cancel_orphans or resolve them first"
                    )
            if existing is not None:  # a replay within this run: same order, or refused
                same = (existing.symbol, existing.side, existing.quantity, existing.order_type,
                        existing.limit_price, existing.stop_price, existing.time_in_force) == (
                        req.symbol, req.side.value, req.quantity, req.order_type.value, req.limit_price,
                        req.stop_price, req.time_in_force.value)  # fmt: skip
                if not same:
                    raise GatewayError(
                        f"order id {lean_id} was already used in this run with other parameters"
                    )
            elif req.side is Side.SELL:
                self._check_own_position(session, req)
        result = self._oms.submit(req)  # risk gate first, always
        reasons = result.decision.reasons if result.decision is not None else []
        send(_order_message(lean_id, result.order.id, result.state.value, "; ".join(reasons), reply="place"))

    def _check_own_position(self, session: Session, req: OrderRequest) -> None:
        """A deployment sells only what it holds: the smaller of its own net position and the account's,
        minus its own unfinished sells (working, pending or unknown)."""
        own = self._own_positions(session).get(req.symbol, 0.0)
        account = self._oms.book(session).quantity(req.symbol)
        held = min(own, max(account, 0.0))
        working_sells = sum(
            o.quantity - queries.filled_quantity(session, o.id)
            for o, st in self._deployment_orders(session)
            if o.symbol == req.symbol and o.side == Side.SELL.value and st not in TERMINAL
        )
        if req.quantity > held - working_sells + 1e-9:
            raise GatewayError(
                f"sell {req.quantity} {req.symbol} exceeds this deployment's position {held} "
                f"(working sells {working_sells}); deployments never sell positions they do not hold"
            )

    def _cancel(self, msg: dict[str, Any], send: Callable[[dict[str, Any]], None]) -> None:
        lean_id = _lean_id(msg)
        with self._sessions() as session:
            order = session.execute(
                select(Order).where(Order.client_order_key == f"{self._prefix}{lean_id}")
            ).scalar_one_or_none()
            order_id = order.id if order is not None else None
        if order_id is None:  # other runs', deployments' and manual orders are not visible here
            raise GatewayError(f"no order {lean_id} in this run of deployment {self.deployment}")
        state = self._oms.cancel(order_id)
        send(_order_message(lean_id, order_id, state.value, "", reply="cancel"))

    def _deployment_orders(
        self, session: Session, prefix: str | None = None
    ) -> list[tuple[Order, OrderState]]:
        """Orders the gateway placed for this deployment (by source *and* key; SQL LIKE ignores case)."""
        rows = session.execute(
            select(Order).where(
                Order.account == self._oms.account,
                Order.source == f"lean:{self.deployment}",
                Order.client_order_key.startswith(prefix or self._deployment_prefix, autoescape=True),
            )
        ).scalars()
        out = []
        for o in rows:
            if not o.client_order_key.startswith(prefix or self._deployment_prefix):  # exact case
                continue
            state = queries.current_state(session, o.id)
            if state is not None:
                out.append((o, state))
        return out

    def _mine(self, session: Session) -> list[tuple[Order, OrderState, int]]:
        """This run's orders with their LEAN ids (unparsable keys are skipped, never fatal)."""
        out = []
        for o, st in self._deployment_orders(session, self._prefix):
            lean = _lean_of(o, self._prefix)
            if lean is not None:
                out.append((o, st, lean))
        return out

    def _orphans(self, session: Session) -> list[tuple[Order, OrderState]]:
        """Unfinished orders of this deployment's earlier runs (a restarted algorithm does not know them)."""
        return [
            (o, st)
            for o, st in self._deployment_orders(session)
            if not o.client_order_key.startswith(self._prefix) and st not in TERMINAL
        ]

    def _cancel_orphans(self, send: Callable[[dict[str, Any]], None]) -> None:
        with self._sessions() as session:
            orphans = [(o.id, st) for o, st in self._orphans(session)]
        results: dict[str, str] = {}
        for order_id, st in orphans:
            if st in (OrderState.SUBMITTED, OrderState.ACCEPTED, OrderState.PARTIALLY_FILLED):
                try:
                    results[order_id] = self._oms.cancel(order_id).value
                except OMSError as exc:
                    results[order_id] = f"not cancelled: {exc}"
            else:  # pending / unknown outcome: only reconciliation can resolve it
                results[order_id] = f"{st.value}: resolve with `quantlab paper reconcile`"
        with self._sessions() as session:
            remaining = [o.id for o, _ in self._orphans(session)]
        send({"type": "orphans", "results": results, "remaining": remaining})

    def _own_book(self, session: Session) -> Book:
        """This deployment's positions from its own fills, average cost included (its cash means nothing)."""
        ids = {o.id for o, _ in self._deployment_orders(session)}
        rows: list[FillRecord] = []
        if ids:
            rows = list(
                session.execute(
                    select(FillRecord)
                    .where(FillRecord.order_id.in_(ids))
                    .order_by(FillRecord.executed_at, FillRecord.id)
                ).scalars()
            )
        return Book.from_fills(
            0.0,
            (
                Fill(f.symbol, f.quantity if f.side == Side.BUY.value else -f.quantity, f.price, 0.0)
                for f in rows
            ),
        )

    def _own_positions(self, session: Session) -> dict[str, float]:
        book = self._own_book(session)
        return {s: p.quantity for s, p in book.positions.items() if abs(p.quantity) > 1e-9}

    def _push(
        self, send: Callable[[dict[str, Any]], None], sent_states: dict[str, str], sent_fills: set[str]
    ) -> None:
        out: list[dict[str, Any]] = []
        with self._sessions() as session:
            mine = self._mine(session)
            lean_of = {o.id: lean for o, _st, lean in mine}
            if lean_of:
                fills = session.execute(
                    select(FillRecord)
                    .where(FillRecord.order_id.in_(lean_of))
                    .order_by(FillRecord.executed_at, FillRecord.id)
                ).scalars()
                for f in fills:  # fills before states, so a FILLED state never arrives ahead of its fill
                    if f.exec_id in sent_fills:
                        continue
                    out.append(
                        {
                            "type": "fill",
                            "id": lean_of[f.order_id],
                            "order_id": f.order_id,
                            "exec_id": f.exec_id,
                            "quantity": f.quantity if f.side == Side.BUY.value else -f.quantity,
                            "price": f.price,
                            "commission": f.commission,
                            "ts": f.executed_at.isoformat(),
                            "price_state": f.price_state,
                        }
                    )
                    sent_fills.add(f.exec_id)
            for o, st, lean in mine:
                if sent_states.get(o.id) == st.value:
                    continue
                reason = ""
                if st.value in REJECTED_STATES:
                    last = queries.last_event(session, o.id)
                    reason = _reason(last.json() if last is not None else {})
                out.append(_order_message(lean, o.id, st.value, reason))
                sent_states[o.id] = st.value
        for message in out:
            send(message)

    def _snapshot(self) -> dict[str, Any]:
        with self._sessions() as session:
            cash = self._oms.book(session).cash
            own = self._own_book(session)
            open_ids = [lean for _o, st, lean in self._mine(session) if st not in TERMINAL]
            orphans = [o.id for o, _ in self._orphans(session)]
        return {
            "type": "snapshot",
            "cash": cash,  # the shared paper account's cash
            "positions": [
                {"symbol": s, "quantity": p.quantity, "average_price": p.average_price}
                for s, p in sorted(own.positions.items())
                if abs(p.quantity) > 1e-9
            ],
            "open": open_ids,
            "orphans": orphans,
        }


# ---- helpers ------------------------------------------------------------------------------------------
class _LineReader:
    def __init__(self, conn: socket.socket) -> None:
        self._conn = conn
        self._buf = b""
        self._discarding = False

    def next_message(self, timeout_s: float, limit: int = MAX_LINE) -> Any | None:
        """One JSON message, None if nothing arrived within the timeout; ConnectionError when closed."""
        self._conn.settimeout(timeout_s)
        while b"\n" not in self._buf:
            if len(self._buf) > limit:  # drop the rest of this line too, up to its newline
                self._buf, self._discarding = b"", True
                raise GatewayError("message too long")
            try:
                chunk = self._conn.recv(4096)
            except TimeoutError:
                return None
            if not chunk:
                raise ConnectionError("plugin disconnected")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        if self._discarding:
            self._discarding = False
            return None
        if not line.strip():
            return None
        try:
            return json.loads(line, parse_constant=_no_constants)
        except (ValueError, RecursionError) as exc:
            raise GatewayError(f"invalid JSON: {_short(exc)}") from None


def _no_constants(name: str) -> Any:
    raise ValueError(f"{name} is not allowed")


def _sender(conn: socket.socket) -> Callable[[dict[str, Any]], None]:
    def send(message: dict[str, Any]) -> None:
        conn.sendall(json.dumps(message, default=str).encode() + b"\n")

    return send


def _short(exc: BaseException) -> str:
    return str(exc)[:300] or type(exc).__name__


def _lean_id(msg: dict[str, Any]) -> int:
    value = msg.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or not 0 < value < 2**31:
        raise GatewayError("id must be the positive integer LEAN order id")
    return value


def _number(value: Any, name: str) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise GatewayError(f"{name} must be a number")
    try:
        number = float(value)
    except OverflowError:
        raise GatewayError(f"{name} is out of range") from None
    if not math.isfinite(number):
        raise GatewayError(f"{name} must be finite")
    return number


def _price(value: Any, name: str) -> float | None:
    if value is None:
        return None
    price = _number(value, name)
    if price <= 0:
        raise GatewayError(f"{name} must be > 0")
    return price


def _symbol(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.\-]{1,16}", value):
        raise GatewayError("symbol must be 1-16 letters, digits, '.' or '-'")
    return value.upper()


def _lean_of(order: Order, prefix: str) -> int | None:
    rest = order.client_order_key[len(prefix) :] if order.client_order_key.startswith(prefix) else ""
    return int(rest) if rest.isdigit() else None


def _order_message(
    lean_id: int, order_id: str, state: str, reason: str, reply: str | None = None
) -> dict[str, Any]:
    """``reply`` marks the direct answer to a place/cancel request (pushed updates have none)."""
    msg = {"type": "order", "id": lean_id, "order_id": order_id, "state": state, "reason": reason}
    if reply is not None:
        msg["reply"] = reply
    return msg


def _reason(detail: dict[str, Any]) -> str:
    for key in ("reasons", "reason", "error"):
        value = detail.get(key)
        if value:
            return "; ".join(value) if isinstance(value, list) else str(value)
    return ""
