"""IBKR **paper** broker adapter (BRK-003/004) behind the ``BrokerAdapter`` protocol.

Safety (DEC-0029):

* Paper only. The connection is refused on the configured live port and on IBKR's standard live ports, and
  after connecting every managed account must be an IBKR paper account (ids starting with ``DU``); otherwise
  the adapter disconnects and raises before anything is sent. Orders always name the verified paper account
  explicitly.
* Every order carries the QuantLab order id as ``orderRef``; that is how events, executions and open orders
  are mapped back. Orders without a QuantLab reference (e.g. placed by hand in TWS) are reported as open
  orders ``ib-perm:<permId>``, which the OMS does not track, so the risk gate blocks new orders until they are
  resolved.
* Outcomes the adapter cannot confirm are never guessed: a failure before the order is handed to the client is
  ``BrokerUnavailable`` (not placed); a failure while placing, or no acknowledgement in time, is
  ``SubmitUnknown`` (resolved by reconciliation).
* Executions are reported once their commission report has arrived (or, after ``commission_wait_s``, with the
  commission left at 0 and recorded as ``commission_known=False`` in the log; KI-0074). Fill prices are IBKR's
  simulated paper fills: ``price_state = "IBKR_PAPER"``.

All IBKR I/O goes through the small ``IBTradingClient`` protocol (``brokers/ib_async_trading.py`` implements
it with ``ib_async``), so the behaviour here is tested with a fake client and no network.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from quantlab.brokers.base import (
    BrokerError,
    BrokerOrder,
    BrokerPosition,
    BrokerSnapshot,
    BrokerUnavailable,
    ExecutionReport,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    SequencedEvent,
    Side,
    SubmitUnknown,
)
from quantlab.core.logging import get_logger

log = get_logger(__name__)

PAPER_ACCOUNT_PREFIX = "DU"  # IBKR paper (simulated trading) account ids
STANDARD_LIVE_PORTS = frozenset({7496, 4001})  # TWS live, IB Gateway live
PRICE_STATE = "IBKR_PAPER"
ACCEPTED_STATUSES = frozenset({"PreSubmitted", "Submitted"})
CANCELLED_STATUSES = frozenset({"Cancelled", "ApiCancelled"})
PENDING_STATUSES = frozenset({"", "PendingSubmit", "ApiPending"})


@dataclass(frozen=True)
class IBOrderRecord:
    order_ref: str  # QuantLab order id ("" for orders not placed by QuantLab)
    perm_id: int
    status: str  # ib_async OrderStatus.status
    account: str
    reason: str = ""  # why an order is Inactive / rejected, when known (IBKR error, warnings excluded)
    ever_working: bool = False  # reached PreSubmitted/Submitted at some point (else a cancel is a rejection)


@dataclass(frozen=True)
class IBExecRecord:
    exec_id: str
    order_ref: str
    account: str
    symbol: str
    side: str  # "BOT" | "SLD"
    shares: float
    price: float
    commission: float | None  # None until the commission report arrives
    time: datetime


@dataclass(frozen=True)
class IBPositionRecord:
    account: str
    symbol: str
    quantity: float
    average_cost: float


class IBTradingClient(Protocol):
    def connect(self, host: str, port: int, client_id: int, timeout: float) -> None: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def managed_accounts(self) -> list[str]: ...

    def place(self, order: BrokerOrder, account: str) -> None:
        """Hand the order to IBKR (orderRef = order.order_id, account set explicitly)."""
        ...

    def cancel(self, order_ref: str) -> bool:
        """Request cancellation; False if no open order with this reference is known."""
        ...

    def modify(self, order: BrokerOrder) -> bool:
        """Send new terms for the open order with reference ``order.order_id``; False if none is known."""
        ...

    def orders(self) -> list[IBOrderRecord]:
        """Every order known to the connection (open orders of all clients plus this session's)."""
        ...

    def executions(self, account: str) -> list[IBExecRecord]: ...

    def positions(self, account: str) -> list[IBPositionRecord]: ...

    def cash(self, account: str) -> float | None:
        """Total cash in the account's base currency, or None if unknown."""
        ...

    def wait(self, timeout: float) -> None: ...


class IBKRPaperBroker:
    name = "ibkr"
    mode = "paper"

    def __init__(
        self,
        client: IBTradingClient,
        *,
        host: str,
        port: int,
        client_id: int,
        live_port: int,
        expected_account: str | None = None,
        connect_timeout_s: float = 10.0,
        ack_timeout_s: float = 5.0,
        commission_wait_s: float = 10.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if port == live_port or port in STANDARD_LIVE_PORTS:
            raise BrokerError(
                f"port {port} is an IBKR live trading port; QuantLab connects to paper ports only"
            )
        if expected_account is not None and not expected_account.startswith(PAPER_ACCOUNT_PREFIX):
            raise BrokerError(f"{expected_account} is not an IBKR paper account (paper ids start with DU)")
        self._client = client
        self._host, self._port, self._client_id = host, port, client_id
        self._expected = expected_account
        self._connect_timeout = connect_timeout_s
        self._ack_timeout = ack_timeout_s
        self._commission_wait = commission_wait_s
        self._now = now
        self._account: str | None = None
        self._emitted: set[tuple[str, str]] = set()
        self._pending: list[SequencedEvent] = []
        self._seq = 0

    # ---- connection (BRK-004) ---------------------------------------------------------------------
    @property
    def account(self) -> str | None:
        return self._account

    def _ensure(self) -> str:
        """Connected, verified paper account — or BrokerUnavailable / BrokerError, with nothing sent."""
        if self._account is not None and self._client.is_connected():
            return self._account
        try:
            if not self._client.is_connected():
                self._client.connect(self._host, self._port, self._client_id, self._connect_timeout)
            accounts = self._client.managed_accounts()
        except Exception as exc:
            self._account = None
            raise BrokerUnavailable(f"IBKR not reachable at {self._host}:{self._port}: {exc}") from exc
        not_paper = [a for a in accounts if not a.startswith(PAPER_ACCOUNT_PREFIX)]
        if not accounts or not_paper:
            self._client.disconnect()
            self._account = None
            raise BrokerError(
                f"refusing IBKR connection: accounts {not_paper or accounts} are not paper accounts "
                "(live trading is not available)"
            )
        if self._expected is not None and self._expected not in accounts:
            self._client.disconnect()
            self._account = None
            raise BrokerError(
                f"IBKR paper account {self._expected} is not managed by this login ({accounts})"
            )
        if self._expected is None and len(accounts) > 1:
            self._client.disconnect()
            self._account = None
            raise BrokerError(f"several paper accounts {accounts}: set paper.ibkr_account to choose one")
        self._account = self._expected or accounts[0]
        return self._account

    def close(self) -> None:
        self._client.disconnect()
        self._account = None

    # ---- BrokerAdapter ------------------------------------------------------------------------------
    def submit(self, order: BrokerOrder) -> None:
        account = self._ensure()
        if any(o.order_ref == order.order_id for o in self._client.orders()) or any(
            e.order_ref == order.order_id for e in self._client.executions(account)
        ):
            raise BrokerError(f"duplicate order id {order.order_id}")
        try:
            self._client.place(order, account)
        except BrokerError:  # raised by the client *before* handing the order over (e.g. unknown symbol)
            raise
        except Exception as exc:  # it may have reached IBKR: never assume "not placed"
            raise SubmitUnknown(f"IBKR: placing {order.order_id} failed midway: {exc!r}") from exc
        deadline = time.monotonic() + self._ack_timeout
        while True:
            mine = [o for o in self._client.orders() if o.order_ref == order.order_id]
            if mine and mine[0].status not in PENDING_STATUSES:
                return  # accepted, filled, cancelled or rejected: reported through events()
            if time.monotonic() >= deadline:
                raise SubmitUnknown(
                    f"IBKR: no acknowledgement for {order.order_id} within {self._ack_timeout}s"
                )
            self._client.wait(0.1)

    def cancel(self, order_id: str) -> None:
        self._ensure()
        if not self._client.cancel(order_id):
            raise BrokerError(f"order {order_id} is not working at IBKR")

    def modify(self, order: BrokerOrder) -> None:
        self._ensure()
        try:
            found = self._client.modify(order)
        except Exception as exc:  # it may have reached IBKR
            raise SubmitUnknown(f"IBKR: modifying {order.order_id} failed midway: {exc!r}") from exc
        if not found:
            raise BrokerError(f"order {order.order_id} is not working at IBKR")

    def events(self) -> list[SequencedEvent]:
        account = self._ensure()
        self._client.wait(0.2)
        orders = [o for o in self._client.orders() if o.order_ref and o.account in ("", account)]
        reports, waiting = self._collect(account)
        for o in orders:  # 1. acceptances
            if o.status in ACCEPTED_STATUSES | {"Filled"} or (o.ever_working and o.status != "Inactive"):
                self._emit(
                    ("accepted", o.order_ref), OrderAccepted(o.order_ref, f"ib-perm:{o.perm_id}", self._now())
                )
        for report in reports:  # 2. fills, before any final status of their order
            self._emit(("exec", report.exec_id), report)
        for o in orders:  # 3. final states (held back while a fill of the order still waits for commission)
            if o.order_ref in waiting:
                continue
            rejected = o.status == "Inactive" or (
                o.status in CANCELLED_STATUSES and o.reason and not o.ever_working
            )
            if rejected:  # IBKR reports many rejections as "Cancelled" with an error
                self._emit(
                    ("rejected", o.order_ref),
                    OrderRejected(o.order_ref, o.reason or "inactive at IBKR", self._now()),
                )
            elif o.status in CANCELLED_STATUSES:
                self._emit(("cancelled", o.order_ref), OrderCancelled(o.order_ref, self._now()))
        return list(self._pending)

    def acknowledge(self, through_seq: int) -> None:
        self._pending = [e for e in self._pending if e.seq > through_seq]

    def positions(self) -> list[BrokerPosition]:
        account = self._ensure()
        return [
            BrokerPosition(p.symbol, p.quantity, p.average_cost)
            for p in sorted(self._client.positions(account), key=lambda p: p.symbol)
            if p.account == account and p.quantity != 0
        ]

    def cash(self) -> float:
        value = self._client.cash(self._ensure())
        if value is None:
            raise BrokerUnavailable("IBKR account cash is not available yet")
        return value

    def open_order_ids(self) -> set[str]:
        account = self._ensure()
        return {
            o.order_ref or f"ib-perm:{o.perm_id}"
            for o in self._client.orders()
            if o.account in ("", account) and o.status not in CANCELLED_STATUSES | {"Filled", "Inactive"}
        }

    def executions(self) -> list[ExecutionReport]:
        return self._collect(self._ensure())[0]

    def snapshot(self) -> BrokerSnapshot:
        # IBKR has no atomic multi-read: wait for pending updates first, then read in a fixed order.
        self._ensure()
        self._client.wait(0.2)
        return BrokerSnapshot(
            open_order_ids=frozenset(self.open_order_ids()),
            executions=tuple(self.executions()),
            positions=tuple(self.positions()),
            cash=self.cash(),
        )

    # ---- internals ------------------------------------------------------------------------------------
    def _emit(self, key: tuple[str, str], event: object) -> None:
        if key in self._emitted:
            return
        self._emitted.add(key)
        self._seq += 1
        self._pending.append(SequencedEvent(self._seq, event))  # type: ignore[arg-type]

    def _collect(self, account: str) -> tuple[list[ExecutionReport], set[str]]:
        """Reportable executions, and the order ids with an execution still waiting for its commission."""
        out = []
        waiting: set[str] = set()
        now = self._now()
        for e in sorted(self._client.executions(account), key=lambda x: (x.time, x.exec_id)):
            if not e.order_ref or e.account != account:
                continue  # not placed by QuantLab: shows up as a position mismatch in reconciliation
            commission = e.commission
            if commission is None:
                if (now - e.time).total_seconds() < self._commission_wait:
                    waiting.add(e.order_ref)
                    continue  # wait for the commission report
                log.warning(
                    "ibkr.commission_unknown", exec_id=e.exec_id, order_id=e.order_ref, commission_known=False
                )
                commission = 0.0
            if e.side not in ("BOT", "SLD"):
                log.warning("ibkr.execution_side_unknown", exec_id=e.exec_id, side=e.side)
                continue  # never guess a direction: shows up as a position mismatch in reconciliation
            out.append(
                ExecutionReport(
                    order_id=e.order_ref,
                    exec_id=e.exec_id,
                    symbol=e.symbol,
                    side=Side.BUY if e.side == "BOT" else Side.SELL,
                    quantity=float(e.shares),
                    price=float(e.price),
                    commission=float(commission),
                    ts=e.time,
                    price_state=PRICE_STATE,
                )
            )
        return out, waiting
