"""``IBTradingClient`` implemented with ``ib_async`` (optional extra ``ibkr``) — BRK-003.

A thin translation layer: every decision (paper-account verification, duplicate checks, acknowledgement
timeouts, event mapping) lives in ``quantlab.brokers.ibkr.IBKRPaperBroker`` and is tested with a fake client.
This class has **not yet been run against a real IB Gateway / TWS paper login** (KI-0075).

Unlike the read-only market-data connection (DATA-019, its own client id), this connection must be able to
place orders, so it is opened with ``readonly=False`` — only ever on the paper port, and the adapter refuses
the session unless every managed account is a paper account.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from quantlab.brokers.base import BrokerError, BrokerOrder, OrderType, Side
from quantlab.brokers.ibkr import IBExecRecord, IBOrderRecord, IBPositionRecord

ib_async: Any
try:  # optional dependency
    import ib_async
except ImportError:  # pragma: no cover - exercised only without the extra
    ib_async = None

_ORDER_TYPES = {
    OrderType.MARKET: "MKT",
    OrderType.LIMIT: "LMT",
    OrderType.STOP: "STP",
    OrderType.STOP_LIMIT: "STP LMT",
    OrderType.TRAILING_STOP: "TRAIL",
}


def _set_prices(ib_order: Any, order: BrokerOrder) -> None:
    if order.limit_price is not None:
        ib_order.lmtPrice = order.limit_price
    if order.order_type is OrderType.TRAILING_STOP:  # TRAIL: auxPrice = amount, or trailingPercent
        if order.trail_percent:
            ib_order.trailingPercent = order.trail_amount
        else:
            ib_order.auxPrice = order.trail_amount
    elif order.stop_price is not None:
        ib_order.auxPrice = order.stop_price


def _utc(t: Any) -> datetime:
    if not isinstance(t, datetime):
        return datetime.now(UTC)
    return t.replace(tzinfo=UTC) if t.tzinfo is None else t.astimezone(UTC)


class IbAsyncTradingClient:  # pragma: no cover - needs a running IB Gateway / TWS (KI-0075)
    def __init__(self) -> None:
        if ib_async is None:
            raise BrokerError("the IBKR broker needs the 'ibkr' extra: `uv sync --extra ibkr`")
        self._lib = ib_async
        self._ib = ib_async.IB()
        self._contracts: dict[str, Any] = {}
        self._reasons: dict[int, str] = {}  # orderId -> last error text (rejections)
        self._ib.errorEvent += self._on_error

    def _on_error(self, req_id: int, code: int, text: str, *_: Any) -> None:
        if req_id > 0 and not (code == 399 or 2100 <= code < 2200):  # 399 / 21xx are warnings
            self._reasons[int(req_id)] = f"{code}: {text}"

    def connect(self, host: str, port: int, client_id: int, timeout: float) -> None:
        self._ib.connect(host, port, clientId=client_id, timeout=timeout, readonly=False)
        self._ib.reqAllOpenOrders()

    def disconnect(self) -> None:
        if self._ib.isConnected():
            self._ib.disconnect()

    def is_connected(self) -> bool:
        return bool(self._ib.isConnected())

    def managed_accounts(self) -> list[str]:
        return [str(a) for a in self._ib.managedAccounts()]

    def _contract(self, symbol: str) -> Any:
        if symbol not in self._contracts:
            qualified = self._ib.qualifyContracts(self._lib.Stock(symbol, "SMART", "USD"))
            if not qualified:
                raise BrokerError(f"IBKR: no security definition for {symbol!r}")
            self._contracts[symbol] = qualified[0]
        return self._contracts[symbol]

    def place(self, order: BrokerOrder, account: str) -> None:
        ib_order = self._lib.Order(
            action="BUY" if order.side is Side.BUY else "SELL",
            totalQuantity=order.quantity,
            orderType=_ORDER_TYPES[order.order_type],
            tif=order.time_in_force.value.upper(),
            account=account,
            orderRef=order.order_id,
            transmit=True,
        )
        _set_prices(ib_order, order)
        self._ib.placeOrder(self._contract(order.symbol), ib_order)

    def modify(self, order: BrokerOrder) -> bool:
        """IB modifies an order when it is placed again with the same order id (OMS-006)."""
        for trade in self._ib.openTrades():
            if trade.order.orderRef == order.order_id:
                trade.order.totalQuantity = order.quantity
                _set_prices(trade.order, order)
                self._ib.placeOrder(trade.contract, trade.order)
                return True
        return False

    def cancel(self, order_ref: str) -> bool:
        for trade in self._ib.openTrades():
            if trade.order.orderRef == order_ref:
                self._ib.cancelOrder(trade.order)
                return True
        return False

    def orders(self) -> list[IBOrderRecord]:
        self._ib.reqAllOpenOrders()  # refresh: also orders placed later by other clients / by hand in TWS
        return [
            IBOrderRecord(
                order_ref=str(t.order.orderRef or ""),
                perm_id=int(t.order.permId or t.orderStatus.permId or 0),
                status=str(t.orderStatus.status),
                account=str(t.order.account or ""),
                reason=self._reasons.get(int(t.order.orderId), ""),
                ever_working=any(e.status in ("PreSubmitted", "Submitted") for e in t.log),
            )
            for t in self._ib.trades()
        ]

    def executions(self, account: str) -> list[IBExecRecord]:
        fills = self._ib.reqExecutions(self._lib.ExecutionFilter(acctCode=account))
        out = []
        for f in fills:
            if f.contract.secType != "STK":
                continue  # QuantLab trades US stocks only; other instruments are not mapped by symbol
            e, c = f.execution, f.commissionReport
            commission = c.commission if c is not None and c.execId else None
            if commission is not None and not math.isfinite(commission):
                commission = None
            out.append(
                IBExecRecord(
                    exec_id=str(e.execId),
                    order_ref=str(e.orderRef or ""),
                    account=str(e.acctNumber),
                    symbol=str(f.contract.symbol),
                    side=str(e.side),
                    shares=float(e.shares),
                    price=float(e.price),
                    commission=commission,
                    time=_utc(e.time),
                )
            )
        return out

    def positions(self, account: str) -> list[IBPositionRecord]:
        return [
            IBPositionRecord(str(p.account), str(p.contract.symbol), float(p.position), float(p.avgCost))
            for p in self._ib.positions(account)
            if p.contract.secType == "STK"
        ]

    def cash(self, account: str) -> float | None:
        for v in self._ib.accountValues(account):
            if v.tag == "TotalCashValue" and v.currency == "BASE":
                return float(v.value)
        return None

    def wait(self, timeout: float) -> None:
        self._ib.waitOnUpdate(timeout=timeout)
