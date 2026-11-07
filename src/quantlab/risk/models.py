"""Risk records (RISK-003/004): decisions and kill-switch events, append-only (triggers in migration 0006)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.db import Base, UTCDateTime


class RiskRecordError(RuntimeError):
    pass


class RiskDecision(Base):
    __tablename__ = "risk_decisions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    order_id: Mapped[str] = mapped_column(String(26), nullable=False, index=True)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False)
    checks: Mapped[str] = mapped_column(Text, nullable=False)  # JSON [{check, ok, detail}]
    limits_sha256: Mapped[str | None] = mapped_column(String(64))  # None: no valid limits file
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    def json(self, column: str) -> Any:
        return json.loads(getattr(self, column))


class KillSwitchEvent(Base):
    __tablename__ = "kill_switch_events"
    __table_args__ = (UniqueConstraint("seq", name="uq_kill_switch_events_seq"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)  # event order (never the wall clock)
    engaged: Mapped[bool] = mapped_column(Boolean, nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


@event.listens_for(Session, "before_flush")
def _append_only(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in [*session.deleted, *session.dirty]:
        if isinstance(obj, RiskDecision | KillSwitchEvent) and (
            obj in session.deleted or session.is_modified(obj)
        ):
            raise RiskRecordError("risk decisions and kill-switch events are append-only")
