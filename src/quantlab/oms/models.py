"""OMS records (OMS-001/003/004): immutable orders, append-only order events and fills.

* ``orders``: the request as received (never updated). ``client_order_key`` is unique → duplicate submissions
  are rejected by the database itself (idempotency).
* ``order_events``: the state history; the current state is the latest event (projection, ARCHITECTURE §5.5).
* ``fills``: broker execution reports; ``(broker, exec_id)`` is unique so a replayed report is ignored. All
  three tables are append-only (ORM guard here + DB triggers in migration 0006).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.db import Base, UTCDateTime


class OrderRecordError(RuntimeError):
    pass


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    client_order_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "paper"
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # paper | live
    symbol: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)
    order_type: Mapped[str] = mapped_column(String(12), nullable=False)
    limit_price: Mapped[float | None] = mapped_column(Float)
    stop_price: Mapped[float | None] = mapped_column(Float)
    time_in_force: Mapped[str] = mapped_column(String(4), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "cli:owner", "strategy:<run>"
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (UniqueConstraint("order_id", "seq", name="uq_order_events_seq"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    def json(self) -> Any:
        return json.loads(self.detail)


class FillRecord(Base):
    __tablename__ = "fills"
    __table_args__ = (UniqueConstraint("broker", "account", "exec_id", name="uq_fills_broker_account_exec"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    exec_id: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(4), nullable=False)
    quantity: Mapped[float] = mapped_column(Float, nullable=False)  # positive
    price: Mapped[float] = mapped_column(Float, nullable=False)
    commission: Mapped[float] = mapped_column(Float, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    price_state: Mapped[str] = mapped_column(
        String(12), nullable=False
    )  # LIVE | SIMULATED | DELAYED | FROZEN


class OmsAccount(Base):
    """Account identity + starting cash, fixed at first use: changing ``paper.starting_cash`` later is refused
    (it would silently inflate equity and every equity-based limit)."""

    __tablename__ = "oms_accounts"

    account: Mapped[str] = mapped_column(String(64), primary_key=True)
    broker: Mapped[str] = mapped_column(String(32), nullable=False)
    starting_cash: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class DailyEquity(Base):
    """Start-of-day equity per account (first order check of the UTC day) for the daily loss limit."""

    __tablename__ = "oms_daily_equity"
    __table_args__ = (UniqueConstraint("account", "day", name="uq_oms_daily_equity_day"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    account: Mapped[str] = mapped_column(String(64), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False)
    equity: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


@event.listens_for(Session, "before_flush")
def _append_only(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in [*session.deleted, *session.dirty]:
        if isinstance(obj, Order | OrderEvent | FillRecord | DailyEquity | OmsAccount) and (
            obj in session.deleted or session.is_modified(obj)
        ):
            raise OrderRecordError("orders, order events and fills are append-only")
