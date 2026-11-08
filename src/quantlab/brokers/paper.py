"""Simulated paper broker (BRK-002) — a local stand-in for a broker account, never connected to a real market.

* Prices come from a quote source (``RealtimeProvider.snapshot`` or any ``symbol -> Quote`` callable). Every
  fill records the data state of the quote it used; with SIMULATED quotes the fills are simulated end to end.
* Market orders fill at the ask (buy) / bid (sell), falling back to last, plus ``slippage_bps``; limit orders
  fill when the quote crosses the limit; stop / stop-limit orders trigger on the last price. Commission =
  max(per-share * qty, minimum). No partial fills, no market impact (documented limitation, KI-0061).
* Cash account semantics: buys beyond available cash and sells beyond the held quantity (shorting) are
  rejected unless ``allow_short`` is set.
* State (cash, positions, working orders, executions) is persisted atomically to a JSON file, so a restart
  behaves like a real broker: the OMS must reconcile, and nothing is lost or invented.
* ``fail_next`` injects failure modes for tests: ``"reject"``, ``"unavailable"``, ``"unknown"`` (the order may
  or may not have been placed — ``"unknown_placed"`` places it, ``"unknown"`` does not).
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantlab.brokers.base import (
    BrokerError,
    BrokerEvent,
    BrokerOrder,
    BrokerPosition,
    BrokerSnapshot,
    BrokerUnavailable,
    ExecutionReport,
    OrderAccepted,
    OrderCancelled,
    OrderExpired,
    OrderRejected,
    OrderType,
    SequencedEvent,
    Side,
    SubmitUnknown,
    TimeInForce,
)
from quantlab.core.filelock import lock_file, unlock_file
from quantlab.core.ids import new_id
from quantlab.data.market_data import NO_TRADE_FLAGS, DataState, Quote

QuoteSource = Callable[[str], Quote | None]
LOCK_TIMEOUT_S = 10.0
PREFETCH_MAX_AGE_S = 2.0  # quotes fetched before a longer wait for the lock are not used to fill


class PaperBroker:
    name = "paper"
    mode = "paper"

    def __init__(
        self,
        state_file: Path,
        quotes: QuoteSource,
        *,
        starting_cash: float = 100_000.0,
        commission_per_share: float = 0.005,
        min_commission: float = 1.0,
        slippage_bps: float = 1.0,
        allow_short: bool = False,
        max_quote_age_s: float = 60.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._file = state_file
        self._quotes = quotes
        self._commission_per_share = commission_per_share
        self._min_commission = min_commission
        self._slippage = slippage_bps / 10_000.0
        self._allow_short = allow_short
        self._max_quote_age = max_quote_age_s
        self._now = now
        self._triggered = False
        self.fail_next: str | None = None
        self._prefetched: dict[str, Quote | None] = {}
        self._fetched_at = 0.0
        self._state: dict[str, Any] = {}
        with self._locked():  # creating the initial state is a write like any other
            if not self._state:
                self._state = {
                    "cash": starting_cash,
                    "positions": {},
                    "working": {},
                    "executions": [],
                    "seen": [],
                    "seq": 0,
                }
                self._save()

    # ---- persistence ------------------------------------------------------------------------------
    def _save(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self._file)

    def _next_id(self, prefix: str) -> str:
        self._state["seq"] += 1
        return f"{prefix}-{self._state['seq']:08d}"

    # ---- BrokerAdapter ------------------------------------------------------------------------------
    def _submit_unlocked(self, order: BrokerOrder) -> None:
        mode, self.fail_next = self.fail_next, None
        if mode == "unavailable":
            raise BrokerUnavailable("paper broker: simulated disconnect")
        if mode == "unknown":
            raise SubmitUnknown("paper broker: simulated timeout (order not placed)")
        seen = self._state.setdefault("seen", [])
        if order.order_id in seen:
            raise BrokerError(f"duplicate order id {order.order_id}")
        seen.append(order.order_id)
        self._save()
        reason = None
        if mode == "reject" or order.quantity <= 0 or not math.isfinite(order.quantity):
            reason = "rejected by paper broker"
        elif order.order_type in (OrderType.LIMIT, OrderType.STOP_LIMIT) and not order.limit_price:
            reason = "limit price required"
        elif order.order_type in (OrderType.STOP, OrderType.STOP_LIMIT) and not order.stop_price:
            reason = "stop price required"
        elif order.order_type is OrderType.TRAILING_STOP and not _valid_trail(order):
            reason = "trailing stop needs a positive trail amount (percent below 100)"
        if reason is not None:
            self._emit(OrderRejected(order.order_id, reason, self._now()))
            self._save()
            return
        record = {**asdict(order), "triggered": False, "broker_order_id": self._next_id("P")}
        self._state["working"][order.order_id] = record
        self._emit(OrderAccepted(order.order_id, record["broker_order_id"], self._now()))
        self._save()
        if mode == "unknown_placed":
            raise SubmitUnknown("paper broker: simulated timeout after the order was placed")
        self._match()

    def _modify_unlocked(self, order: BrokerOrder) -> None:
        """OMS-006: new terms for a working order (the paper broker never fills partially: quantity = all)."""
        w = self._state["working"].get(order.order_id)
        if w is None:
            raise BrokerError(f"order {order.order_id} is not working")
        if (w["symbol"], w["side"], w["order_type"]) != (
            order.symbol,
            order.side.value,
            order.order_type.value,
        ):
            raise BrokerError("a modification cannot change symbol, side or order type")
        if order.quantity <= 0 or not math.isfinite(order.quantity):
            raise BrokerError("quantity must be positive")
        kind = order.order_type
        if kind in (OrderType.LIMIT, OrderType.STOP_LIMIT) and not order.limit_price:
            raise BrokerError("limit price required")
        if kind in (OrderType.STOP, OrderType.STOP_LIMIT) and not order.stop_price and not w["triggered"]:
            raise BrokerError("stop price required")
        if kind is OrderType.TRAILING_STOP and not _valid_trail(order):
            raise BrokerError("trailing stop needs a positive trail amount (percent below 100)")
        w.update(quantity=order.quantity, limit_price=order.limit_price, trail_amount=order.trail_amount)
        if kind is not OrderType.TRAILING_STOP:
            w["stop_price"] = order.stop_price
        self._save()
        self._match()

    def _cancel_unlocked(self, order_id: str) -> None:
        if order_id not in self._state["working"]:
            raise BrokerError(f"order {order_id} is not working")
        del self._state["working"][order_id]
        self._emit(OrderCancelled(order_id, self._now()))
        self._save()

    def _events_unlocked(self) -> list[SequencedEvent]:
        self._match()
        return [SequencedEvent(int(e["event_seq"]), _decode(e)) for e in self._pending()]

    def _acknowledge_unlocked(self, through_seq: int) -> None:
        """Drop events up to and including ``through_seq``: never one the caller has not seen, even when
        another process acknowledged (or emitted) events meanwhile."""
        self._state["pending_events"] = [e for e in self._pending() if int(e["event_seq"]) > through_seq]
        self._save()

    def _pending(self) -> list[dict[str, Any]]:
        pending: list[dict[str, Any]] = self._state.setdefault("pending_events", [])
        for e in pending:  # state files written before events carried a sequence number
            if "event_seq" not in e:
                e["event_seq"] = self._next_event_seq()
        return pending

    def _next_event_seq(self) -> int:
        self._state["event_seq"] = int(self._state.get("event_seq", 0)) + 1
        return int(self._state["event_seq"])

    def _emit(self, ev: BrokerEvent) -> None:
        """Events are persisted with the broker state until acknowledged, so a crash never loses them."""
        pending = self._pending()
        pending.append({**_encode(ev), "event_seq": self._next_event_seq()})

    def _positions_unlocked(self) -> list[BrokerPosition]:
        return [
            BrokerPosition(sym, p["quantity"], p["average_price"])
            for sym, p in sorted(self._state["positions"].items())
            if p["quantity"] != 0
        ]

    def _cash_unlocked(self) -> float:
        return float(self._state["cash"])

    def _open_order_ids_unlocked(self) -> set[str]:
        return set(self._state["working"])

    def _executions_unlocked(self) -> list[ExecutionReport]:
        return [_report(e) for e in self._state["executions"]]

    # ---- session ------------------------------------------------------------------------------------
    def _expire_day_orders_unlocked(self, order_ids: set[str] | None = None) -> None:
        """End of session: DAY orders expire (only ``order_ids`` when given: the closed session's orders)."""
        for order_id, w in list(self._state["working"].items()):
            if w["time_in_force"] == TimeInForce.DAY and (order_ids is None or order_id in order_ids):
                del self._state["working"][order_id]
                self._emit(OrderExpired(order_id, self._now()))
        self._save()

    # ---- process-safe public API -----------------------------------------------------------------------
    # Every call (reads included) reloads the state under an OS-level exclusive lock. Quotes are fetched
    # *before* the lock is taken, so a slow quote source never holds other processes up.
    def submit(self, order: BrokerOrder) -> None:
        quotes = self._fetch_quotes([order.symbol])
        with self._locked(quotes):
            self._submit_unlocked(order)

    def cancel(self, order_id: str) -> None:
        with self._locked():
            self._cancel_unlocked(order_id)

    def modify(self, order: BrokerOrder) -> None:
        quotes = self._fetch_quotes([order.symbol])
        with self._locked(quotes):
            self._modify_unlocked(order)

    def events(self) -> list[SequencedEvent]:
        quotes = self._fetch_quotes()
        with self._locked(quotes):
            return self._events_unlocked()

    def acknowledge(self, through_seq: int) -> None:
        with self._locked():
            self._acknowledge_unlocked(through_seq)

    def expire_day_orders(self, order_ids: set[str] | None = None) -> None:
        with self._locked():
            self._expire_day_orders_unlocked(order_ids)

    def positions(self) -> list[BrokerPosition]:
        with self._locked():
            return self._positions_unlocked()

    def cash(self) -> float:
        with self._locked():
            return self._cash_unlocked()

    def open_order_ids(self) -> set[str]:
        with self._locked():
            return self._open_order_ids_unlocked()

    def executions(self) -> list[ExecutionReport]:
        with self._locked():
            return self._executions_unlocked()

    def snapshot(self) -> BrokerSnapshot:
        """Open orders, executions, positions and cash read together, under one lock (reconciliation)."""
        with self._locked():
            return BrokerSnapshot(
                open_order_ids=frozenset(self._open_order_ids_unlocked()),
                executions=tuple(self._executions_unlocked()),
                positions=tuple(self._positions_unlocked()),
                cash=self._cash_unlocked(),
            )

    def _fetch_quotes(self, extra: Iterable[str] = ()) -> dict[str, Quote | None]:
        with self._locked():
            symbols = {w["symbol"] for w in self._state["working"].values()} | set(extra)
        quotes = {sym: self._safe_quote(sym) for sym in sorted(symbols)}
        self._fetched_at = time.monotonic()
        return quotes

    def _safe_quote(self, symbol: str) -> Quote | None:
        """A failing quote source means "no quote" (no fill), never a failed cancel or event read."""
        try:
            return self._quotes(symbol)
        except Exception:
            return None

    def _reload(self) -> None:
        if self._file.is_file():
            self._state = json.loads(self._file.read_text(encoding="utf-8"))

    @contextmanager
    def _locked(self, quotes: dict[str, Quote | None] | None = None) -> Iterator[None]:
        """OS-level exclusive lock (``fcntl.flock`` / ``msvcrt.locking``) on a never-deleted lock file.

        The operating system releases the lock when the holder exits or crashes, so a lock is never broken by
        age and two writers can never overlap (DEC-0027). The state is reloaded once the lock is held.
        """
        lock = self._file.with_suffix(".lock")
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open("a+b") as fh:
            _acquire(fh, lock)
            try:
                fresh = time.monotonic() - self._fetched_at <= PREFETCH_MAX_AGE_S
                self._prefetched = (quotes or {}) if fresh else {}  # stale: no fills now, next call refetches
                self._reload()
                yield
            finally:
                self._prefetched = {}
                _release(fh)

    # ---- matching -------------------------------------------------------------------------------------
    def _match(self) -> None:
        self._triggered = False
        changed = False
        for order_id, w in list(self._state["working"].items()):
            quote = self._prefetched.get(w["symbol"])
            if quote is None or not self._fillable(quote):
                continue  # no fill on a missing, old or flagged quote (stale, halted, crossed, ...)
            price = self._fill_price(w, quote)
            if price is None:
                continue
            side = Side(w["side"])
            qty = float(w["quantity"])
            commission = max(self._min_commission, self._commission_per_share * qty)
            held = self._state["positions"].get(w["symbol"], {"quantity": 0.0, "average_price": 0.0})
            if side is Side.BUY and qty * price + commission > self._state["cash"] + 1e-9:
                del self._state["working"][order_id]
                self._emit(OrderRejected(order_id, "insufficient cash (paper cash account)", self._now()))
                changed = True
                continue
            if side is Side.SELL and not self._allow_short and qty > held["quantity"] + 1e-9:
                del self._state["working"][order_id]
                self._emit(OrderRejected(order_id, "short selling not allowed", self._now()))
                changed = True
                continue
            self._apply_fill(w, side, qty, price, commission, held, quote)
            del self._state["working"][order_id]
            changed = True
        if changed or self._triggered:
            self._save()

    def _fillable(self, q: Quote) -> bool:
        """No rejecting flag (as the risk gate); a real-time quote is at most ``max_quote_age_s`` old.

        SIMULATED replays carry historical timestamps by design (the gate's ``max_quote_age_s`` governs
        them). A source's own staleness (e.g. an IBKR ticker that stopped updating) arrives as ``stale``.
        """
        if set(q.flags) & NO_TRADE_FLAGS:
            return False
        if q.state is DataState.SIMULATED:
            return True
        age = (self._now() - q.received_ts).total_seconds()
        return -2.0 <= age <= self._max_quote_age

    def _trail(self, w: dict[str, Any], side: Side, last: float) -> bool:
        """Move a trailing stop with the best price since it was placed; True once the last crosses it."""
        ref = w.get("trail_ref")
        best = (max if side is Side.SELL else min)(ref, last) if ref is not None else last
        amount = float(w["trail_amount"])
        distance = best * amount / 100.0 if w.get("trail_percent") else amount
        stop = best - distance if side is Side.SELL else best + distance
        if best != ref or stop != w.get("stop_price"):
            w["trail_ref"], w["stop_price"] = best, stop
            self._triggered = True  # saved with the next match: the reference survives restarts
        hit = last <= stop if side is Side.SELL else last >= stop
        if hit:
            w["triggered"] = True
        return hit

    def _fill_price(self, w: dict[str, Any], q: Quote) -> float | None:
        side = Side(w["side"])
        touch = (q.ask if side is Side.BUY else q.bid) or q.last
        last = q.last or q.mid
        if touch is None:
            return None
        kind = OrderType(w["order_type"])
        if kind is OrderType.TRAILING_STOP and not w["triggered"]:
            if last is None:
                return None
            if not self._trail(w, side, last):
                return None
        if kind in (OrderType.STOP, OrderType.STOP_LIMIT) and not w["triggered"]:
            if last is None:
                return None
            hit = last >= w["stop_price"] if side is Side.BUY else last <= w["stop_price"]
            if not hit:
                return None
            w["triggered"] = True
            self._triggered = True
        if kind in (OrderType.MARKET, OrderType.STOP, OrderType.TRAILING_STOP):
            return touch * (1 + self._slippage) if side is Side.BUY else touch * (1 - self._slippage)
        limit = float(w["limit_price"])
        if side is Side.BUY:
            return min(limit, touch) if touch <= limit else None
        return max(limit, touch) if touch >= limit else None

    def _apply_fill(
        self,
        w: dict[str, Any],
        side: Side,
        qty: float,
        price: float,
        commission: float,
        held: dict[str, float],
        quote: Quote,
    ) -> None:
        signed = qty if side is Side.BUY else -qty
        new_qty = held["quantity"] + signed
        if held["quantity"] == 0 or (held["quantity"] > 0) == (signed > 0):
            avg = (
                (held["average_price"] * abs(held["quantity"]) + price * qty) / abs(new_qty)
                if new_qty
                else 0.0
            )
        else:
            avg = held["average_price"] if new_qty and (new_qty > 0) == (held["quantity"] > 0) else price
        self._state["positions"][w["symbol"]] = {
            "quantity": new_qty,
            "average_price": avg if new_qty else 0.0,
        }
        self._state["cash"] -= signed * price + commission
        report = {
            "order_id": w["order_id"],
            "exec_id": f"E-{new_id()}",  # globally unique, also across account resets
            "symbol": w["symbol"],
            "side": side.value,
            "quantity": qty,
            "price": price,
            "commission": commission,
            "ts": self._now().isoformat(),
            "price_state": quote.state.value,
        }
        self._state["executions"].append(report)
        self._emit(_report(report))


def _acquire(fh: Any, lock: Path) -> None:
    if not lock_file(fh, LOCK_TIMEOUT_S):
        raise BrokerUnavailable(f"paper broker state is locked by another process ({lock})")


def _release(fh: Any) -> None:
    unlock_file(fh)


def _report(e: dict[str, Any]) -> ExecutionReport:
    return ExecutionReport(
        order_id=e["order_id"],
        exec_id=e["exec_id"],
        symbol=e["symbol"],
        side=Side(e["side"]),
        quantity=float(e["quantity"]),
        price=float(e["price"]),
        commission=float(e["commission"]),
        ts=datetime.fromisoformat(e["ts"]),
        price_state=str(e.get("price_state", e.get("quote_state", "SIMULATED"))),
    )


_EVENT_TYPES: dict[str, type] = {
    t.__name__: t for t in (OrderAccepted, OrderRejected, ExecutionReport, OrderCancelled, OrderExpired)
}


def _encode(ev: BrokerEvent) -> dict[str, Any]:
    data = asdict(ev)
    data["ts"] = ev.ts.isoformat()
    if isinstance(ev, ExecutionReport):
        data["side"] = ev.side.value
    return {"type": type(ev).__name__, **data}


def _decode(d: dict[str, Any]) -> BrokerEvent:
    data = dict(d)
    data.pop("event_seq", None)
    cls = _EVENT_TYPES[data.pop("type")]
    data["ts"] = datetime.fromisoformat(data["ts"])
    if cls is ExecutionReport:
        data["side"] = Side(data["side"])
    event: BrokerEvent = cls(**data)
    return event


def _valid_trail(order: BrokerOrder) -> bool:
    amount = order.trail_amount
    if amount is None or not math.isfinite(amount) or amount <= 0:
        return False
    return not (order.trail_percent and amount >= 100)
