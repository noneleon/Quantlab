"""Read-only OMS queries (orders, states, events, fills, confirmed book), shared by the OMS and the API."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.brokers.base import Side
from quantlab.oms.models import FillRecord, Order, OrderEvent
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


def simulation_warning(states: list[str]) -> str | None:
    non_live = [s for s in states if s != "LIVE"]
    if not non_live:
        return None
    return f"fills based on {'/'.join(non_live)} prices: simulated paper results, not a performance claim"
