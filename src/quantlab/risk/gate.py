"""Central pre-trade risk gate (RISK-001) and kill switch (RISK-004).

The OMS calls ``RiskGate.check`` for every order before anything reaches a broker; there is no other path (the
OMS is the only caller of ``BrokerAdapter.submit``). Every decision — approved or not — is stored with the
result of each check and the sha256 of the limits file (RISK-003).

Checks, in order (all are evaluated so the decision shows every reason): kill switch · limits file valid ·
broker mode is paper (live is not available, DEC-0010) · symbol allowed · quantity · price reference (limit
price or a fresh quote) · quote state (LIVE required when configured) · quote age · spread · order notional ·
position notional after the order · gross exposure after the order · shorting · open orders · order rate ·
daily loss limit and drawdown limit (then only position-reducing orders) · buying power (cash account:
the order plus working buys must be covered by cash) · rejection cooldown · trading session. Missing
information is a rejection, never a pass.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.brokers.base import OrderType, Side
from quantlab.core.audit import AuditLog
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.ids import new_id
from quantlab.data.calendars import SPECIAL_CALENDARS, session_bounds
from quantlab.data.market_data import NO_TRADE_FLAGS, DataState, Quote
from quantlab.portfolio.book import Book
from quantlab.risk.limits import RiskConfigError, load_limits
from quantlab.risk.models import KillSwitchEvent, RiskDecision

DISENGAGE_CONFIRMATION = "DISENGAGE KILL SWITCH"
ENGAGE_CONFIRMATION = "ENGAGE KILL SWITCH"  # web console only: guards against an accidental click


class RiskError(RuntimeError):
    pass


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    symbol: str
    side: Side
    quantity: float
    order_type: OrderType
    limit_price: float | None = None
    stop_price: float | None = None


@dataclass(frozen=True)
class PendingOrder:
    """A working order's unfilled remainder (counted at worst case by the exposure checks)."""

    symbol: str
    signed_quantity: float  # + buy, - sell
    price: float | None  # its limit/stop price, if any


@dataclass(frozen=True)
class RiskContext:
    broker_mode: str
    book: Book
    marks: dict[str, float]  # latest prices per held symbol
    quote: Quote | None  # quote of the order's symbol
    open_orders: int
    recent_order_times: Sequence[datetime]
    start_of_day_equity: float | None
    pending: Sequence[PendingOrder] = ()
    unresolved_orders: int = 0  # PENDING_SUBMIT / SUBMIT_UNKNOWN: reconcile before trading on
    peak_equity: float | None = None  # high-water mark of recorded equity (drawdown limit)
    recent_rejections: int | None = None  # rejected orders in the last hour (cooldown); None = unknown
    broker_error: str | None = None  # the broker's open orders could not be read: no new orders


@dataclass
class Decision:
    order_id: str
    approved: bool = True
    checks: list[dict[str, Any]] = field(default_factory=list)
    limits_sha256: str | None = None

    def check(self, name: str, ok: bool, detail: str) -> None:
        self.checks.append({"check": name, "ok": ok, "detail": detail})
        if not ok:
            self.approved = False

    @property
    def reasons(self) -> list[str]:
        return [f"{c['check']}: {c['detail']}" for c in self.checks if not c["ok"]]


class KillSwitch:
    """Append-only state; the latest event decides. Engage needs a reason; disengage also a typed phrase."""

    def __init__(self, clock: Clock | None = None, audit: AuditLog | None = None) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditLog(self._clock)

    def state(self, session: Session) -> KillSwitchEvent | None:
        return session.execute(
            select(KillSwitchEvent).order_by(KillSwitchEvent.seq.desc()).limit(1)
        ).scalar_one_or_none()

    def engaged(self, session: Session) -> bool:
        last = self.state(session)
        return bool(last and last.engaged)

    def engage(
        self, session: Session, *, actor: str, reason: str, channel: str = "unspecified"
    ) -> KillSwitchEvent:
        return self._record(session, True, actor, reason, channel)

    def disengage(
        self, session: Session, *, actor: str, reason: str, confirmation: str, channel: str = "unspecified"
    ) -> KillSwitchEvent:
        if confirmation != DISENGAGE_CONFIRMATION:
            raise RiskError(f"to disengage the kill switch type exactly: {DISENGAGE_CONFIRMATION}")
        return self._record(session, False, actor, reason, channel)

    def _record(
        self, session: Session, engaged: bool, actor: str, reason: str, channel: str = "unspecified"
    ) -> KillSwitchEvent:
        if not reason.strip():
            raise RiskError("a reason is required")
        last = self.state(session)
        entry = KillSwitchEvent(
            id=new_id(),
            seq=(last.seq + 1) if last else 1,
            engaged=engaged,
            actor=actor,
            reason=reason.strip(),
            created_at=self._clock.now(),
        )
        session.add(entry)
        session.flush()
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="risk.kill_switch." + ("engage" if engaged else "disengage"),
            details={"reason": reason.strip(), "channel": channel},
        )
        return entry


class RiskGate:
    def __init__(
        self, limits_file: Path, kill_switch: KillSwitch | None = None, clock: Clock | None = None
    ) -> None:
        self._limits_file = limits_file
        self._clock = clock or SystemClock()
        self._kill = kill_switch or KillSwitch(self._clock)

    def check(self, session: Session, intent: OrderIntent, ctx: RiskContext) -> Decision:
        d = Decision(intent.order_id)
        engaged = self._kill.engaged(session)
        d.check("kill switch", not engaged, "engaged: no new orders" if engaged else "not engaged")
        try:
            loaded = load_limits(self._limits_file)
        except RiskConfigError as exc:
            d.check("risk limits", False, str(exc))
            self._store(session, d)
            return d
        lim = loaded.limits
        d.limits_sha256 = loaded.sha256
        d.check("risk limits", True, f"{loaded.path.name} sha256 {loaded.sha256[:12]}")
        d.check(
            "broker mode", ctx.broker_mode == "paper", f"{ctx.broker_mode} (live trading is not available)"
        )
        if ctx.broker_error is not None:
            d.check("broker reachable", False, ctx.broker_error)
        d.check(
            "unresolved submissions",
            ctx.unresolved_orders == 0,
            f"{ctx.unresolved_orders} order(s) with unknown outcome (run reconcile first)",
        )
        if lim.allowed_symbols is not None:
            d.check("symbol allowed", intent.symbol in lim.allowed_symbols, intent.symbol)
        qty_ok = 0 < intent.quantity <= lim.max_order_quantity
        d.check("order quantity", qty_ok, f"{intent.quantity} (max {lim.max_order_quantity})")

        q = ctx.quote
        now = self._clock.now()
        touch: float | None = None
        if q is None:
            d.check("quote", False, f"no quote for {intent.symbol}")
        else:
            live_ok = q.state is DataState.LIVE or not lim.require_live_quotes
            d.check("quote state", live_ok, f"{q.state.value}" + ("" if live_ok else " (LIVE required)"))
            stamp = q.exchange_ts or q.received_ts
            age = (now - stamp).total_seconds()
            d.check(
                "quote age",
                -2.0 <= age <= lim.max_quote_age_s,
                f"{age:.1f}s (max {lim.max_quote_age_s}s; future-dated quotes are refused)",
            )
            bad = sorted(set(q.flags) & NO_TRADE_FLAGS)
            d.check("quote flags", not bad, ", ".join(bad) if bad else "none")
            if q.bid and q.ask:
                spread = (q.ask - q.bid) / ((q.ask + q.bid) / 2) * 10_000
                spread_ok = 0 <= spread <= lim.max_spread_bps
                d.check("spread", spread_ok, f"{spread:.1f} bps (max {lim.max_spread_bps})")
            else:
                d.check("spread", False, "bid or ask missing: spread unknown")
            touch = (q.ask if intent.side is Side.BUY else q.bid) or q.last
        # Value the order at the most conservative price it could plausibly fill at (DEC-0027): the highest
        # of the touch, the limit and the stop: a far-away limit or stop cannot shrink the checked notional.
        reference = None
        if touch is not None and touch > 0:
            reference = max(
                p for p in (touch, intent.limit_price, intent.stop_price) if p is not None and p > 0
            )
        if reference is None:
            d.check("price reference", False, "no price to value the order")
            self._store(session, d)
            return d
        notional = intent.quantity * reference
        d.check(
            "order notional",
            notional <= lim.max_order_notional,
            f"{notional:.2f} (max {lim.max_order_notional})",
        )

        signed = intent.quantity if intent.side is Side.BUY else -intent.quantity
        held = ctx.book.quantity(intent.symbol)
        # Worst case: every same-direction working order on this symbol fills too, each valued at the
        # higher of its own limit/stop price and this order's reference price.
        same = [
            p for p in ctx.pending if p.symbol == intent.symbol and (p.signed_quantity > 0) == (signed > 0)
        ]
        same_side = sum(p.signed_quantity for p in same)
        after = held + same_side + signed
        plain_after = held + signed
        # "Reduces the position" only exempts an order when no other order on the symbol is working, on either
        # side: an opposite-side working order could flip the position first (with allow_short).
        working_here = any(p.symbol == intent.symbol for p in ctx.pending)
        reduces = (
            not working_here
            and abs(plain_after) < abs(held)
            and (plain_after == 0 or (plain_after > 0) == (held > 0))
        )
        pos_value = abs(plain_after) * reference + sum(
            abs(p.signed_quantity) * max(p.price or 0.0, reference) for p in same
        )
        d.check(
            "position notional",
            pos_value <= lim.max_position_notional or reduces,
            f"{pos_value:.2f} after the order incl. working orders (max {lim.max_position_notional})",
        )
        d.check("short selling", after >= -1e-9 or lim.allow_short, f"position after the order {after}")

        # Equity (and so the daily loss check) is valued at *market* marks only — never at the order's
        # conservative reference price, which would inflate equity for a held position.
        assert q is not None
        market: float = q.mid or q.last or reference
        marks = {**ctx.marks, intent.symbol: market}
        snap = ctx.book.snapshot(marks)
        equity = snap["equity"]
        if equity is None or equity <= 0:
            d.check("equity", False, f"cannot value the account (unmarked {snap['unmarked_symbols']})")
        else:
            pending_other = 0.0
            for p in ctx.pending:
                if p.symbol == intent.symbol:
                    continue
                price = max(x for x in (p.price, ctx.marks.get(p.symbol), 0.0) if x is not None)
                if price <= 0:
                    d.check("working orders", False, f"cannot value working order on {p.symbol}")
                pending_other += abs(p.signed_quantity) * price
            held_value = abs(held) * market
            gross_after = (snap["gross_exposure"] or 0.0) - held_value + pos_value + pending_other
            frac = gross_after / equity
            d.check(
                "gross exposure",
                frac <= lim.max_gross_exposure_fraction or reduces,
                f"{frac:.3f} of equity after the order incl. working orders "
                f"(max {lim.max_gross_exposure_fraction})",
            )
            if ctx.start_of_day_equity:
                loss = 1 - equity / ctx.start_of_day_equity
                breached = loss >= lim.daily_loss_limit_fraction
                d.check(
                    "daily loss limit",
                    not breached or reduces,
                    f"{loss:.4f} below start of day (limit {lim.daily_loss_limit_fraction})"
                    + ("; only position-reducing orders allowed" if breached else ""),
                )
            else:
                d.check("daily loss limit", False, "start-of-day equity unknown")
            peak = max(x for x in (ctx.peak_equity, ctx.start_of_day_equity, equity) if x is not None)
            dd = 1 - equity / peak
            dd_breached = dd >= lim.max_drawdown_fraction
            d.check(
                "drawdown limit",
                not dd_breached or reduces,
                f"{dd:.4f} below the high-water mark {peak:.2f} (limit {lim.max_drawdown_fraction})"
                + ("; only position-reducing orders allowed" if dd_breached else ""),
            )
        if not lim.allow_margin and intent.side is Side.BUY:
            # Cash account: this order and every working buy (at the higher of its price and its market
            # mark) must be covered by cash. Sells never need buying power.
            reserved = 0.0
            for p in ctx.pending:
                if p.signed_quantity <= 0:
                    continue
                mark = reference if p.symbol == intent.symbol else ctx.marks.get(p.symbol)
                price = max(x for x in (p.price, mark, 0.0) if x is not None)
                if price <= 0:
                    d.check("buying power", False, f"cannot value working buy on {p.symbol}")
                reserved += p.signed_quantity * price
            need = notional + reserved
            d.check(
                "buying power",
                need <= ctx.book.cash + 1e-9,
                f"{need:.2f} needed incl. {reserved:.2f} reserved by working buys; cash {ctx.book.cash:.2f}",
            )
        if ctx.recent_rejections is None:
            d.check("rejection cooldown", False, "recent rejections unknown")
        else:
            cooling = ctx.recent_rejections >= lim.max_rejections_per_hour
            d.check(  # reducing a position never adds risk: the owner can always get out during a cooldown
                "rejection cooldown",
                not cooling or reduces,
                f"{ctx.recent_rejections} rejected in the last hour (max {lim.max_rejections_per_hour})"
                + ("; only position-reducing orders allowed" if cooling else ""),
            )
        d.check(
            "open orders",
            ctx.open_orders < lim.max_open_orders,
            f"{ctx.open_orders} open (max {lim.max_open_orders})",
        )
        minute_ago = now - timedelta(minutes=1)
        rate = sum(1 for t in ctx.recent_order_times if t >= minute_ago)
        d.check(
            "order rate",
            rate < lim.max_orders_per_minute,
            f"{rate} in the last minute (max {lim.max_orders_per_minute})",
        )
        cal = lim.trading_calendar
        if cal == "weekdays":
            d.check("trading session", now.weekday() < 5, f"weekdays calendar: {now:%A}")
        elif cal not in ("none", "24/7") and cal not in SPECIAL_CALENDARS:
            today = now.date()
            open_now = any(o <= now < c for _, o, c in session_bounds(cal, today, today))
            d.check(
                "trading session",
                open_now,
                f"{cal} {'open' if open_now else 'closed'} at {now:%Y-%m-%d %H:%M}Z",
            )
        self._store(session, d)
        return d

    def _store(self, session: Session, d: Decision) -> None:
        session.add(
            RiskDecision(
                id=new_id(),
                order_id=d.order_id,
                approved=d.approved,
                checks=_json(d.checks),
                limits_sha256=d.limits_sha256,
                created_at=self._clock.now(),
            )
        )
        session.flush()


def _json(obj: Any) -> str:
    import json

    return json.dumps(obj, sort_keys=True, default=str)
