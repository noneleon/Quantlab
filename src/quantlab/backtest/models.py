"""Backtest run records (BT-003).

A run row may change only while it is non-terminal (``running``). Once terminal it is immutable,
and rows are never deleted. Enforced twice: an ORM flush guard here, and database triggers in
migration 0002. Re-running creates a *new* row linked by ``rerun_of``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, String, Text, event, inspect
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.db import Base, UTCDateTime

TERMINAL_STATUSES = frozenset(
    {"succeeded", "algorithm_error", "failed", "timed_out", "cancelled", "engine_error"}
)
STATUSES = TERMINAL_STATUSES | {"running"}


class BacktestRecordError(RuntimeError):
    pass


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), nullable=False, index=True)
    strategy_version_id: Mapped[str] = mapped_column(ForeignKey("strategy_versions.id"), nullable=False)
    rerun_of: Mapped[str | None] = mapped_column(ForeignKey("backtest_runs.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    parameters: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    data_provenance: Mapped[str] = mapped_column(String(20), nullable=False)
    data_version: Mapped[str | None] = mapped_column(String(64))
    engine_info: Mapped[str] = mapped_column(Text, nullable=False)  # JSON
    inputs_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result_hash: Mapped[str | None] = mapped_column(String(64))
    engine_status: Mapped[str | None] = mapped_column(String(40))
    runtime_error: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    engine_statistics: Mapped[str | None] = mapped_column(Text)  # JSON {name: text}
    metrics: Mapped[str | None] = mapped_column(Text)  # JSON EquityMetrics
    cross_checks: Mapped[str | None] = mapped_column(Text)  # JSON [CrossCheck]
    manifest: Mapped[str] = mapped_column(Text, nullable=False)  # JSON reproducibility manifest
    run_dir: Mapped[str] = mapped_column(Text, nullable=False)

    def json(self, column: str) -> Any:
        raw = getattr(self, column)
        return None if raw is None else json.loads(raw)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@event.listens_for(Session, "before_flush")
def _guard_backtest_runs(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in session.deleted:
        if isinstance(obj, BacktestRun):
            raise BacktestRecordError("backtest runs are never deleted")
    for obj in session.dirty:
        if not isinstance(obj, BacktestRun) or not session.is_modified(obj):
            continue
        history = inspect(obj).attrs.status.history
        original = (history.deleted or history.unchanged or [None])[0]
        if original in TERMINAL_STATUSES:
            raise BacktestRecordError(f"backtest run {obj.id} is {original} and immutable")
    for obj in session.new:
        if isinstance(obj, BacktestRun) and obj.status not in STATUSES:
            raise BacktestRecordError(f"invalid backtest status {obj.status!r}")
