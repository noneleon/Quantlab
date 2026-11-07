"""Order management service (OMS-002..006, BRK-005) — the only code that calls ``BrokerAdapter.submit``.

Flow for every order: idempotency check → ``NEW`` → central risk gate → ``RISK_REJECTED`` or
``PENDING_SUBMIT`` (committed *before* the broker is called) → broker → ``SUBMITTED`` / ``REJECTED`` /
``SUBMIT_UNKNOWN`` → broker events (accepted, fills, cancels, expiries) → terminal state.

Safety properties:
* A repeated ``client_order_key`` returns the existing order and sends nothing (DB unique constraint).
* ``SUBMIT_UNKNOWN`` (and a crash after ``PENDING_SUBMIT``) is resolved only by ``reconcile`` from what the
  broker reports; orders are never resubmitted automatically (DEC-0008).
* Positions and cash come only from broker execution reports (``fills``), never from submissions. A fill that
  arrives in an unexpected state (late fill after a cancel) is still recorded — it is the truth — and reported
  as an anomaly.
* ``reconcile`` compares the confirmed book with the broker's positions and cash and *reports* differences; it
  never rewrites positions.
* Only paper brokers are accepted (live trading requires LIVE-003, DEC-0010).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from quantlab.brokers.base import (
    BrokerAdapter,
    BrokerError,
    BrokerOrder,
    BrokerUnavailable,
    ExecutionReport,
    OrderAccepted,
    OrderCancelled,
    OrderExpired,
    OrderRejected,
    OrderType,
    Side,
    SubmitUnknown,
    TimeInForce,
)
from quantlab.brokers.ibkr import IBKRPaperBroker
from quantlab.brokers.paper import PaperBroker, QuoteSource
from quantlab.core.audit import AuditEntry, AuditLog, encode_details
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.ids import new_id
from quantlab.core.logging import get_logger
from quantlab.data.market_data import DataState, Quote
from quantlab.oms import queries
from quantlab.oms.models import DailyEquity, EquitySample, FillRecord, OmsAccount, Order, OrderEvent
from quantlab.oms.states import TERMINAL, WORKING, OrderState, can_transition
from quantlab.portfolio.book import Book
from quantlab.risk.gate import Decision, OrderIntent, PendingOrder, RiskContext, RiskGate

log = get_logger(__name__)
S = OrderState
# Only these adapter classes are accepted, whatever mode they claim (DEC-0027); the IBKR adapter verifies
# on every connection that all managed accounts are paper accounts (DEC-0029).
PAPER_ADAPTERS: tuple[type, ...] = (PaperBroker, IBKRPaperBroker)
PENDING_SUBMIT_GRACE_S = 60.0  # a younger PENDING_SUBMIT may still be in flight in another process


@dataclass(frozen=True)
class CancelAllResult:
    results: dict[str, str]  # order id -> state after the cancel attempt (or why it was not cancelled)
    still_working: dict[str, str]  # order id -> state, for everything that may still be working

    @property
    def complete(self) -> bool:
        return not self.still_working


CANCELLABLE = frozenset({S.SUBMITTED, S.ACCEPTED, S.PARTIALLY_FILLED})
PENDING_CANCEL_GRACE_S = 60.0  # likewise a younger PENDING_CANCEL (its broker call may not have happened yet)


class OMSError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderRequest:
    client_order_key: str
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    stop_price: float | None = None
    time_in_force: TimeInForce = TimeInForce.DAY
    source: str = "cli:owner"


@dataclass(frozen=True)
class SubmitResult:
    order: Order
    state: OrderState
    duplicate: bool
    decision: Decision | None


@dataclass
class ReconciliationReport:
    fills_added: int = 0
    resolved: list[dict[str, Any]] = field(default_factory=list)
    position_mismatches: list[dict[str, Any]] = field(default_factory=list)
    cash_difference: float | None = None
    open_order_mismatches: list[dict[str, Any]] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return not (
            self.position_mismatches
            or self.open_order_mismatches
            or self.anomalies
            or (self.cash_difference is not None and abs(self.cash_difference) > 0.01)
        )


class OMS:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        broker: BrokerAdapter,
        gate: RiskGate,
        quotes: QuoteSource,
        *,
        account: str,
        starting_cash: float,
        clock: Clock | None = None,
    ) -> None:
        # Trust the adapter class, not its self-declared mode: only known paper adapters (DEC-0027).
        if not isinstance(broker, PAPER_ADAPTERS) or broker.mode != "paper":
            raise OMSError(
                "only paper brokers are available; live trading requires the LIVE-003 safety gates"
            )
        self._sessions = session_factory
        self._broker = broker
        self._gate = gate
        self._quotes = quotes
        self.account = account
        self._starting_cash = starting_cash
        self._clock = clock or SystemClock()
        self.anomalies: list[str] = []
        self._audit = AuditLog(self._clock)

    # ---- queries ---------------------------------------------------------------------------------
    def state(self, session: Session, order_id: str) -> OrderState:
        state = queries.current_state(session, order_id)
        if state is None:
            raise OMSError(f"unknown order {order_id}")
        return state

    def orders(self, session: Session, limit: int | None = 200) -> list[tuple[Order, OrderState]]:
        return queries.orders_with_state(session, self.account, limit)

    def events(self, session: Session, order_id: str) -> list[OrderEvent]:
        return queries.order_events(session, order_id)

    def fills(self, session: Session) -> list[FillRecord]:
        return queries.fills(session, self.account)

    def book(self, session: Session) -> Book:
        return queries.confirmed_book(session, self.account, self._account_cash(session))

    def _account_cash(self, session: Session) -> float:
        """Starting cash is fixed at the account's first use; a later config change is refused."""
        row = session.get(OmsAccount, self.account)
        if row is None:
            session.add(
                OmsAccount(
                    account=self.account,
                    broker=self._broker.name,
                    starting_cash=self._starting_cash,
                    created_at=self._clock.now(),
                )
            )
            session.flush()
            return self._starting_cash
        if abs(row.starting_cash - self._starting_cash) > 1e-9:
            raise OMSError(
                f"account {self.account!r} started with {row.starting_cash}; the configured starting cash "
                f"{self._starting_cash} differs. Use a new paper account name instead of changing it."
            )
        return row.starting_cash

    def marks(self, symbols: list[str]) -> dict[str, float]:
        out = {}
        for sym in symbols:
            q = self._quotes(sym)
            price = (q.mid or q.last) if q else None
            if price:
                out[sym] = price
        return out

    # ---- commands --------------------------------------------------------------------------------
    def submit(self, req: OrderRequest) -> SubmitResult:
        if not req.client_order_key.strip():
            raise OMSError("client_order_key is required (idempotency)")
        if req.client_order_key.lower().startswith("lean:") != req.source.startswith("lean:"):
            raise OMSError("keys starting with 'lean:' are reserved for the LEAN gateway (OMS-007)")
        with self._sessions() as session:
            existing = session.execute(
                select(Order).where(Order.client_order_key == req.client_order_key)
            ).scalar_one_or_none()
            if existing is not None:
                state = self.state(session, existing.id)
                session.expunge(existing)
                return SubmitResult(existing, state, True, None)
            order = Order(
                id=new_id(),
                client_order_key=req.client_order_key,
                account=self.account,
                broker=self._broker.name,
                mode=self._broker.mode,
                symbol=req.symbol.upper(),
                side=req.side.value,
                quantity=float(req.quantity),
                order_type=req.order_type.value,
                limit_price=req.limit_price,
                stop_price=req.stop_price,
                time_in_force=req.time_in_force.value,
                source=req.source,
                created_at=self._clock.now(),
            )
            session.add(order)
            try:
                session.flush()
            except IntegrityError:  # concurrent duplicate key
                session.rollback()
                existing = session.execute(
                    select(Order).where(Order.client_order_key == req.client_order_key)
                ).scalar_one()
                state = self.state(session, existing.id)
                session.expunge(existing)
                return SubmitResult(existing, state, True, None)
            self._event(session, order.id, S.NEW, {"request": req.source})
            decision = self._gate.check(session, self._intent(order), self._context(session, order))
            if not decision.approved:
                self._event(session, order.id, S.RISK_REJECTED, {"reasons": decision.reasons})
                session.commit()
                session.refresh(order)
                session.expunge(order)
                log.info("oms.risk_rejected", order_id=order.id, reasons=decision.reasons)
                return SubmitResult(order, S.RISK_REJECTED, False, decision)
            self._event(session, order.id, S.PENDING_SUBMIT, {})
            session.commit()  # durable before the broker is contacted
            session.refresh(order)
            session.expunge(order)
            broker_order = self._broker_order(order)
        try:
            self._broker.submit(broker_order)
            outcome, detail = S.SUBMITTED, {}
        except SubmitUnknown as exc:
            outcome, detail = (
                S.SUBMIT_UNKNOWN,
                {"error": str(exc), "note": "resolve with reconcile; never resubmitted"},
            )
        except (BrokerUnavailable, BrokerError) as exc:
            outcome, detail = S.REJECTED, {"error": str(exc), "by": "broker adapter"}
        except (
            Exception
        ) as exc:  # unexpected adapter failure: the outcome is unknown, never assume "not placed"
            outcome, detail = S.SUBMIT_UNKNOWN, {"error": repr(exc), "note": "resolve with reconcile"}
        with self._sessions() as session:
            self._try_event(
                session, order.id, outcome, detail
            )  # another process may have reconciled it meanwhile
            session.commit()
        self.process_events()
        with self._sessions() as session:
            return SubmitResult(order, self.state(session, order.id), False, decision)

    def cancel(self, order_id: str) -> OrderState:
        with self._sessions() as session:
            current = self.state(session, order_id)
            if current not in CANCELLABLE:
                raise OMSError(f"order {order_id} is {current}; it cannot be cancelled now")
            self._event(session, order_id, S.PENDING_CANCEL, {})
            session.commit()
        try:
            self._broker.cancel(order_id)
        except BrokerError as exc:
            restore = S.PARTIALLY_FILLED if current is S.PARTIALLY_FILLED else S.ACCEPTED
            with self._sessions() as session:  # reconcile in another process may have resolved it already
                self._try_event(session, order_id, restore, {"cancel_refused": str(exc)})
                session.commit()
        except (
            Exception
        ) as exc:  # outcome unknown: stays PENDING_CANCEL; reconcile resolves it from broker facts
            with self._sessions() as session:
                self._anomaly(session, f"order {order_id}: cancel outcome unknown ({exc!r}); run reconcile")
                session.commit()
        self.process_events()
        with self._sessions() as session:
            return self.state(session, order_id)

    def cancel_all(self, *, reason: str) -> CancelAllResult:
        """Cancel every working order of the account (kill switch, RISK-004). Never sends new orders.

        One failing cancel does not stop the others. Afterwards every order that may still be working (refused
        cancels, in-flight submissions, unknown outcomes, orders only the broker knows) is listed in
        ``still_working`` — the caller must not report success while it is non-empty.
        """
        with self._sessions() as session:
            ids = [o.id for o, st in self.orders(session, limit=None) if st in CANCELLABLE]
            self._audit.record(
                session, actor_type="system", actor="oms", action="oms.cancel_all", target=self.account,
                details={"reason": reason, "orders": ids},
            )  # fmt: skip
            session.commit()
        out: dict[str, str] = {}
        for oid in ids:
            try:
                out[oid] = self.cancel(oid).value
            except OMSError as exc:  # e.g. it filled meanwhile
                out[oid] = f"not cancelled: {exc}"
            except Exception as exc:  # never stop: every other order still gets its cancel
                out[oid] = f"cancel outcome unknown: {exc!r}"
        with self._sessions() as session:
            open_states = [(o.id, st) for o, st in self.orders(session, limit=None) if st not in TERMINAL]
            still = {oid: st.value for oid, st in open_states}
            try:
                for oid in self._broker.open_order_ids():
                    still.setdefault(oid, "working at broker")
            except BrokerError as exc:
                still["(broker)"] = f"open orders unknown: {exc}"
            self._audit.record(
                session, actor_type="system", actor="oms", action="oms.cancel_all.result",
                target=self.account, details={"results": out, "still_working": still},
            )  # fmt: skip
            session.commit()
        return CancelAllResult(out, still)

    def record_equity(
        self, *, source: str, start_of_day: bool = False, max_quote_age_s: float = 60.0
    ) -> float | None:
        """Mark the confirmed book with *checked* quotes and store the equity sample (supervisor, LIVE-001).

        Samples are permanent and feed the drawdown high-water mark (and, with ``start_of_day``, today's
        start-of-day equity), so a held symbol whose quote is missing, flagged (stale/crossed/...), crossed,
        DELAYED/FROZEN or older than ``max_quote_age_s`` means nothing is recorded (returns None). The quote
        states used are stored with the sample (e.g. ``supervisor:SIMULATED``).
        """
        with self._sessions() as session:
            book = self.book(session)
            held = sorted(s for s, p in book.positions.items() if abs(p.quantity) > 1e-9)
            now = self._clock.now()
            marks: dict[str, float] = {}
            states: set[str] = set()
            for sym in held:
                q = self._quotes(sym)
                price = self._checked_mark(q, now, max_quote_age_s)
                if q is None or price is None:
                    return None
                marks[sym] = price
                states.add(q.state.value)
            equity = book.snapshot(marks)["equity"]
            if equity is None:
                return None
            label = f"{source}:{'/'.join(sorted(states)) or 'CASH'}"[:32]
            session.add(
                EquitySample(id=new_id(), account=self.account, equity=equity, source=label, created_at=now)
            )
            if start_of_day:
                self._start_of_day(session, now, equity)
            session.commit()
            return float(equity)

    @staticmethod
    def _checked_mark(q: Quote | None, now: datetime, max_age_s: float) -> float | None:
        if q is None or q.state in (DataState.DELAYED, DataState.FROZEN):
            return None
        if set(q.flags) & {"stale", "clock_skew", "crossed", "no_price"}:
            return None
        if q.bid is not None and q.ask is not None and q.bid > q.ask:
            return None
        age = (now - (q.exchange_ts or q.received_ts)).total_seconds()
        if not -2.0 <= age <= max_age_s:
            return None
        price = q.mid or q.last
        return price if price is not None and price > 0 else None

    def expire_day_orders(self, *, closed_at: datetime) -> list[str]:
        """A session closed at ``closed_at``: DAY orders created before it expire, at brokers that do not
        expire orders themselves (the local paper broker). Later orders belong to the next session."""
        expire = getattr(self._broker, "expire_day_orders", None)
        if expire is None:
            return []
        with self._sessions() as session:
            ids = [
                o.id
                for o, st in self.orders(session, limit=None)
                if st in WORKING
                and o.time_in_force == TimeInForce.DAY.value
                and (o.created_at if o.created_at.tzinfo else o.created_at.replace(tzinfo=UTC)) < closed_at
            ]
        if ids:
            expire(set(ids))
            self.process_events()
        return ids

    def process_events(self) -> int:
        """Apply broker events, commit, then acknowledge them.

        A crash before the acknowledgement replays the events; replayed fills are ignored by exec id.
        """
        events = self._broker.events()
        with self._sessions() as session:
            for item in events:
                self._apply(session, item.event, source="broker event")
            session.commit()
        if events:
            self._broker.acknowledge(max(item.seq for item in events))
        return len(events)

    def reconcile(self) -> ReconciliationReport:
        """Fills from the broker, resolution of unknown submissions, positions/cash/open-order comparison."""
        report = ReconciliationReport()
        self.process_events()
        snap = self._broker.snapshot()  # one consistent read of the broker's facts
        open_ids = set(snap.open_order_ids)
        with self._sessions() as session:
            before = len(self.fills(session))
            for ex in snap.executions:  # _apply compares every execution (detects colliding ids)
                self._apply(session, ex, source="reconciliation")
            report.fills_added = len(self.fills(session)) - before
            now = self._clock.now()
            for order, state in self.orders(session, limit=None):
                last = queries.last_event(session, order.id)
                since = last.created_at if last is not None else order.created_at
                age = (now - (since if since.tzinfo else since.replace(tzinfo=UTC))).total_seconds()
                if (
                    state is S.PENDING_SUBMIT and age < PENDING_SUBMIT_GRACE_S
                ):  # from its PENDING_SUBMIT event
                    report.open_order_mismatches.append(
                        {"order_id": order.id, "state": state.value, "broker": "submission may be in flight"}
                    )
                    continue
                if state is S.PENDING_SUBMIT:  # crashed between commit and broker call
                    self._event(
                        session,
                        order.id,
                        S.SUBMIT_UNKNOWN,
                        {"note": "found PENDING_SUBMIT at reconciliation"},
                    )
                    state = S.SUBMIT_UNKNOWN
                if state is S.SUBMIT_UNKNOWN:
                    filled = self._filled_quantity(session, order.id)
                    if order.id in open_ids:
                        target = S.PARTIALLY_FILLED if filled else S.ACCEPTED
                    elif filled >= order.quantity - 1e-9:
                        target = S.FILLED
                    elif filled:
                        target = S.CANCELLED
                    else:
                        target = S.NOT_PLACED
                    self._event(session, order.id, target, {"resolved_by": "reconciliation"})
                    report.resolved.append({"order_id": order.id, "state": target.value})
                elif state is S.PENDING_CANCEL:
                    filled = self._filled_quantity(session, order.id)
                    if order.id in open_ids and age < PENDING_CANCEL_GRACE_S:
                        report.open_order_mismatches.append(
                            {"order_id": order.id, "state": state.value, "broker": "cancel may be in flight"}
                        )
                        continue
                    if order.id in open_ids:
                        target = S.PARTIALLY_FILLED if filled else S.ACCEPTED
                    else:
                        target = S.FILLED if filled >= order.quantity - 1e-9 else S.CANCELLED
                    self._event(
                        session, order.id, target, {"resolved_by": "reconciliation", "was": "PENDING_CANCEL"}
                    )
                    report.resolved.append({"order_id": order.id, "state": target.value})
                elif state in WORKING and order.id not in open_ids:
                    report.open_order_mismatches.append(
                        {"order_id": order.id, "state": state.value, "broker": "not working at broker"}
                    )
                elif state in TERMINAL and order.id in open_ids:  # invisible to the risk gate: investigate
                    report.open_order_mismatches.append(
                        {"order_id": order.id, "state": state.value, "broker": "still working at broker"}
                    )
            for oid in sorted(open_ids):
                if session.get(Order, oid) is None:
                    report.open_order_mismatches.append(
                        {"order_id": oid, "state": None, "broker": "unknown to OMS"}
                    )
            book = self.book(session)
            broker_pos = {p.symbol: p.quantity for p in snap.positions}
            ours = {s: p.quantity for s, p in book.positions.items() if abs(p.quantity) > 1e-9}
            for sym in sorted(set(broker_pos) | set(ours)):
                a, b = ours.get(sym, 0.0), broker_pos.get(sym, 0.0)
                if abs(a - b) > 1e-9:
                    report.position_mismatches.append({"symbol": sym, "oms": a, "broker": b})
            report.cash_difference = book.cash - snap.cash
            session.commit()
        report.anomalies = list(self.anomalies)
        return report

    # ---- internals --------------------------------------------------------------------------------
    def _last_event(self, session: Session, order_id: str) -> OrderEvent | None:
        return queries.last_event(session, order_id)

    def _event(self, session: Session, order_id: str, state: OrderState, detail: dict[str, Any]) -> None:
        last = self._last_event(session, order_id)
        if last is not None and not can_transition(OrderState(last.state), state):
            raise OMSError(f"order {order_id}: transition {last.state} → {state} is not allowed")
        session.add(
            OrderEvent(
                id=new_id(),
                order_id=order_id,
                seq=(last.seq + 1) if last else 1,
                state=state.value,
                detail=json.dumps(detail, sort_keys=True, default=str),
                created_at=self._clock.now(),
            )
        )
        session.flush()

    def _try_event(
        self,
        session: Session,
        order_id: str,
        state: OrderState,
        detail: dict[str, Any],
        *,
        from_fill: bool = False,
    ) -> None:
        """Apply a broker-reported state if the machine allows it; otherwise record an anomaly.

        A replayed *status* event (crash before acknowledge) whose state the order already passed through is
        ignored. A state driven by a newly recorded fill is never a replay (fills are deduplicated by exec id
        first), so it is always applied or reported.
        """
        current = self.state(session, order_id)
        if current == state and state is not S.PARTIALLY_FILLED:
            return
        if can_transition(current, state):
            self._event(session, order_id, state, detail)
            return
        seen = {e.state for e in queries.order_events(session, order_id)}
        if not from_fill and state.value in seen:
            return
        ref = f" [exec {detail['exec_id']}]" if "exec_id" in detail else ""
        self._anomaly(
            session, f"order {order_id}: broker reported {state} while {current} (state unchanged){ref}"
        )

    def _anomaly(self, session: Session, message: str) -> None:
        """Anomalies are persisted in the audit log (and returned by reconcile), never only kept in memory."""
        if message not in self.anomalies:
            self.anomalies.append(message)
        already = session.execute(
            select(AuditEntry.seq)
            .where(AuditEntry.action == "oms.anomaly", AuditEntry.target == self.account)
            .where(AuditEntry.details == encode_details({"message": message}))
            .limit(1)
        ).scalar_one_or_none()
        if already is not None:  # reconcile re-applies everything: record each anomaly once
            return
        log.warning("oms.anomaly", message=message)
        self._audit.record(
            session, actor_type="system", actor="oms", action="oms.anomaly", target=self.account,
            details={"message": message},
        )  # fmt: skip

    def _filled_quantity(self, session: Session, order_id: str) -> float:
        return queries.filled_quantity(session, order_id)

    def _apply(self, session: Session, ev: Any, *, source: str) -> bool:
        order = session.get(Order, ev.order_id)
        if order is None:
            self._anomaly(session, f"{source}: event for unknown order {ev.order_id} ({type(ev).__name__})")
            return False
        if isinstance(ev, ExecutionReport):
            exists = session.execute(
                select(FillRecord).where(
                    FillRecord.broker == order.broker,
                    FillRecord.account == order.account,
                    FillRecord.exec_id == ev.exec_id,
                )
            ).scalar_one_or_none()
            if exists is not None:
                same = (exists.order_id, exists.quantity, exists.price) == (order.id, ev.quantity, ev.price)
                if not same:  # never silently drop a different fill that reuses an id
                    self._anomaly(
                        session,
                        f"{source}: exec id {ev.exec_id} already recorded for order {exists.order_id} with "
                        "different "
                        f"values; the new report for order {order.id} was NOT recorded — investigate",
                    )
                return False
            session.add(
                FillRecord(
                    id=new_id(),
                    order_id=order.id,
                    broker=order.broker,
                    account=order.account,
                    exec_id=ev.exec_id,
                    symbol=ev.symbol,
                    side=ev.side.value,
                    quantity=ev.quantity,
                    price=ev.price,
                    commission=ev.commission,
                    executed_at=ev.ts,
                    recorded_at=self._clock.now(),
                    price_state=ev.price_state,
                )
            )
            session.flush()
            filled = self._filled_quantity(session, order.id)
            target = S.FILLED if filled >= order.quantity - 1e-9 else S.PARTIALLY_FILLED
            if filled > order.quantity + 1e-9:
                self._anomaly(session, f"order {order.id}: filled {filled} > ordered {order.quantity}")
            self._try_event(
                session,
                order.id,
                target,
                {"exec_id": ev.exec_id, "filled": filled, "source": source},
                from_fill=True,
            )
            return True
        if isinstance(ev, OrderAccepted):
            self._try_event(session, order.id, S.ACCEPTED, {"broker_order_id": ev.broker_order_id})
        elif isinstance(ev, OrderRejected):
            self._try_event(session, order.id, S.REJECTED, {"reason": ev.reason})
        elif isinstance(ev, OrderCancelled):
            self._try_event(session, order.id, S.CANCELLED, {})
        elif isinstance(ev, OrderExpired):
            self._try_event(session, order.id, S.EXPIRED, {})
        return True

    @staticmethod
    def _intent(order: Order) -> OrderIntent:
        return OrderIntent(
            order.id,
            order.symbol,
            Side(order.side),
            order.quantity,
            OrderType(order.order_type),
            order.limit_price,
            order.stop_price,
        )

    @staticmethod
    def _broker_order(order: Order) -> BrokerOrder:
        return BrokerOrder(
            order.id,
            order.symbol,
            Side(order.side),
            order.quantity,
            OrderType(order.order_type),
            order.limit_price,
            order.stop_price,
            TimeInForce(order.time_in_force),
        )

    def _context(self, session: Session, order: Order) -> RiskContext:
        book = self.book(session)
        held = [s for s, p in book.positions.items() if abs(p.quantity) > 1e-9]
        now = self._clock.now()
        recent = list(
            session.execute(
                select(Order.created_at).where(
                    Order.account == self.account,
                    Order.created_at >= now - timedelta(minutes=1),
                    Order.id != order.id,
                )
            ).scalars()
        )
        everything = self.orders(session, limit=None)
        working_symbols = {o.symbol for o, st in everything if o.id != order.id and st in WORKING}
        marks = self.marks(sorted(set(held) | working_symbols))  # price working orders on unheld symbols too
        open_count = sum(
            1 for o, st in everything if o.id != order.id and (st in WORKING or st is S.PENDING_SUBMIT)
        )
        unresolved = sum(
            1 for o, st in everything if o.id != order.id and st in (S.PENDING_SUBMIT, S.SUBMIT_UNKNOWN)
        )
        # Orders the broker still works but the OMS considers finished (or never knew) are invisible to the
        # exposure checks: block new orders until reconcile/a person resolves them. Unknown = blocked too.
        tracked = {o.id for o, st in everything if st in WORKING or st is S.PENDING_SUBMIT}
        broker_error = None
        try:
            unresolved += len(self._broker.open_order_ids() - tracked - {order.id})
        except Exception as exc:  # any failure to read the broker blocks new orders (fails closed)
            broker_error = f"cannot read the broker's open orders: {exc}"
        pending = [
            PendingOrder(
                o.symbol,
                (o.quantity - self._filled_quantity(session, o.id)) * (1 if o.side == Side.BUY else -1),
                o.limit_price or o.stop_price,
            )
            for o, st in everything
            if o.id != order.id and st in WORKING
        ]
        equity = book.snapshot(marks)["equity"]
        hour_ago = now - timedelta(hours=1)
        rejections = sum(
            1
            for o, st in everything
            if o.id != order.id
            and st in (S.RISK_REJECTED, S.REJECTED)
            and (o.created_at if o.created_at.tzinfo else o.created_at.replace(tzinfo=UTC)) >= hour_ago
        )
        peaks = [
            session.execute(select(func.max(DailyEquity.equity)).where(DailyEquity.account == self.account)),
            session.execute(
                select(func.max(EquitySample.equity)).where(EquitySample.account == self.account)
            ),
        ]
        recorded = [x for x in (r.scalar_one_or_none() for r in peaks) if x is not None]
        peak = max(recorded) if recorded else None
        return RiskContext(
            broker_mode=self._broker.mode,
            book=book,
            marks=marks,
            quote=self._quotes(order.symbol),
            open_orders=open_count,
            recent_order_times=[t if t.tzinfo else t.replace(tzinfo=UTC) for t in recent],
            start_of_day_equity=self._start_of_day(session, now, equity),
            pending=pending,
            unresolved_orders=unresolved,
            peak_equity=peak,
            recent_rejections=rejections,
            broker_error=broker_error,
        )

    def _start_of_day(self, session: Session, now: datetime, equity: float | None) -> float | None:
        day = now.date().isoformat()
        row = session.execute(
            select(DailyEquity).where(DailyEquity.account == self.account, DailyEquity.day == day)
        ).scalar_one_or_none()
        if row is not None:
            return row.equity
        if equity is None:
            return None
        session.add(DailyEquity(id=new_id(), account=self.account, day=day, equity=equity, created_at=now))
        session.flush()
        return equity


__all__ = [
    "OMS",
    "TERMINAL",
    "OMSError",
    "OrderRequest",
    "OrderType",  # re-exported so transport layers never import broker modules
    "ReconciliationReport",
    "Side",
    "SubmitResult",
    "TimeInForce",
]
