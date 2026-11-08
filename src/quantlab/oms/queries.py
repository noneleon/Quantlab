"""Read-only OMS queries (orders, states, events, fills, confirmed book), shared by the OMS and the API."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.brokers.base import Side
from quantlab.oms.models import FillRecord, Order, OrderAmendment, OrderEvent
from quantlab.oms.states import OrderState
from quantlab.portfolio.book import Book, Fill


def last_event(session: Session, order_id: str) -> OrderEvent | None:
    return session.execute(
        select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.seq.desc()).limit(1)
    ).scalar_one_or_none()


def current_state(session: Session, order_id: str) -> OrderState | None:
    last = last_event(session, order_id)
    return OrderState(last.state) if last else None


def orders_with_state(
    session: Session, account: str, limit: int | None = 200
) -> list[tuple[Order, OrderState]]:
    """Newest first; ``limit=None`` returns every order (risk and reconciliation must see all of them)."""
    query = select(Order).where(Order.account == account).order_by(Order.created_at.desc(), Order.id.desc())
    rows = session.execute(query if limit is None else query.limit(limit)).scalars()
    out = []
    for o in rows:
        state = current_state(session, o.id)
        if state is not None:
            out.append((o, state))
    return out


def order_events(session: Session, order_id: str) -> list[OrderEvent]:
    return list(
        session.execute(
            select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.seq)
        ).scalars()
    )


def fills(session: Session, account: str) -> list[FillRecord]:
    return list(
        session.execute(
            select(FillRecord)
            .where(FillRecord.account == account)
            .order_by(FillRecord.executed_at, FillRecord.id)
        ).scalars()
    )


@dataclass(frozen=True)
class Terms:
    """An order's current terms: the original ones overlaid by its last applied amendment (OMS-006)."""

    quantity: float
    limit_price: float | None
    stop_price: float | None
    trail_amount: float | None


def terms(session: Session, order: Order) -> Terms:
    last = session.execute(
        select(OrderAmendment)
        .where(OrderAmendment.order_id == order.id, OrderAmendment.status == "applied")
        .order_by(OrderAmendment.created_at.desc(), OrderAmendment.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None:
        return Terms(order.quantity, order.limit_price, order.stop_price, order.trail_amount)
    return Terms(last.quantity, last.limit_price, last.stop_price, last.trail_amount)


def amendments(session: Session, order_id: str) -> list[OrderAmendment]:
    return list(
        session.execute(
            select(OrderAmendment)
            .where(OrderAmendment.order_id == order_id)
            .order_by(OrderAmendment.created_at, OrderAmendment.id)
        ).scalars()
    )


def filled_quantity(session: Session, order_id: str) -> float:
    return sum(
        f.quantity
        for f in session.execute(select(FillRecord).where(FillRecord.order_id == order_id)).scalars()
    )


def confirmed_book(session: Session, account: str, starting_cash: float) -> Book:
    """DEC-0008: the book is built from broker execution reports only."""
    return Book.from_fills(
        starting_cash,
        (
            Fill(f.symbol, f.quantity if f.side == Side.BUY else -f.quantity, f.price, f.commission)
            for f in fills(session, account)
        ),
    )


def fill_price_states(session: Session, account: str) -> list[str]:
    """Data states of the prices behind the account's fills (anything but LIVE = simulated/non-real P&L)."""
    return sorted({f.price_state for f in fills(session, account)})


def simulation_warning(states: list[str], *, paper: bool = True) -> str | None:
    """Paper results are always simulated executions (no partial fills, impact or queue position); non-LIVE
    prices make them doubly so. Real-broker fills on LIVE prices carry no warning."""
    non_live = [s for s in states if s not in ("LIVE", "IBKR_PAPER")]
    parts = []
    if paper and "IBKR_PAPER" in states:
        parts.append("IBKR paper account: fills simulated by IBKR")
    if paper and (non_live or "IBKR_PAPER" not in states):
        parts.append(
            "paper trading: simulated executions (local paper broker: no partial fills, market impact or "
            "queue position)"
        )
    if non_live:
        parts.append(f"fills priced from {'/'.join(non_live)} quotes")
    return "; ".join(parts) + " — not a performance claim" if parts else None


def account_starting_cash(session: Session, account: str) -> float | None:
    from quantlab.oms.models import OmsAccount

    row = session.get(OmsAccount, account)
    return row.starting_cash if row is not None else None
