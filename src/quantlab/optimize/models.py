"""Optimization records (OPT-001/003): one ``optimizations`` row per study, append-only
``optimization_trials``.

Trials point at ordinary backtest runs (immutable, reproducible). A study may change only while ``running``;
nothing is ever deleted (ORM guard here + database triggers in migration 0004).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import Float, ForeignKey, Integer, String, Text, event, inspect
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.db import Base, UTCDateTime

OPT_TERMINAL = frozenset({"succeeded", "failed"})


class OptimizationRecordError(RuntimeError):
    pass


class Optimization(Base):
    __tablename__ = "optimizations"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # sweep | walk_forward
    strategy_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    spec: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    summary: Mapped[str | None] = mapped_column(Text)  # JSON
    error: Mapped[str | None] = mapped_column(Text)

    def json(self, column: str) -> Any:
        raw = getattr(self, column)
        return None if raw is None else json.loads(raw)


class OptimizationTrial(Base):
    __tablename__ = "optimization_trials"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    optimization_id: Mapped[str] = mapped_column(ForeignKey("optimizations.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    window: Mapped[int | None] = mapped_column(Integer)  # walk-forward window index
    segment: Mapped[str] = mapped_column(String(8), nullable=False)  # ALL | IS | OOS
    parameters: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    run_id: Mapped[str | None] = mapped_column(ForeignKey("backtest_runs.id"))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # ok | rejected | no_objective | run_failed
    objective: Mapped[float | None] = mapped_column(Float)
    metrics: Mapped[str] = mapped_column(Text, nullable=False)  # JSON (objective inputs)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    def json(self, column: str) -> Any:
        return json.loads(getattr(self, column))


@event.listens_for(Session, "before_flush")
def _guard(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in session.deleted:
        if isinstance(obj, Optimization | OptimizationTrial):
            raise OptimizationRecordError("optimization records are never deleted")
    for obj in session.dirty:
        if isinstance(obj, OptimizationTrial) and session.is_modified(obj):
            raise OptimizationRecordError("optimization trials are append-only")
        if isinstance(obj, Optimization) and session.is_modified(obj):
            history = inspect(obj).attrs.status.history
            original = (history.deleted or history.unchanged or [None])[0]
            if original in OPT_TERMINAL:
                raise OptimizationRecordError(f"optimization {obj.id} is {original} and immutable")
