"""Strategy lifecycle state (STR-004): an append-only, audited history of stage transitions.

Stages: ``research`` → ``backtested`` → ``validated`` → ``paper`` → ``live_candidate``. ``retired`` and a
demotion back to ``research`` are allowed from anywhere. This module only knows the state machine and the
history; whether a promotion's *evidence* is sufficient is decided by ``quantlab.lifecycle`` (it needs runs
and optimizations).

Every event records the strategy's tree hash at the time, so a later code edit is visible as "stale": evidence
for one version never silently carries over to another. ``live_candidate`` is not live trading — activating
live trading is a separate, human-only step with its own gates (LIVE-003, CLAUDE.md §4).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.audit import AuditLog
from quantlab.core.clock import Clock, SystemClock
from quantlab.core.db import Base, UTCDateTime
from quantlab.core.ids import new_id
from quantlab.strategies.registry import Strategy, StrategyError

STAGES = ("research", "backtested", "validated", "paper", "live_candidate", "retired")
FORWARD = {
    "research": "backtested",
    "backtested": "validated",
    "validated": "paper",
    "paper": "live_candidate",
}


class LifecycleError(StrategyError):
    pass


class LifecycleEvent(Base):
    __tablename__ = "strategy_lifecycle_events"
    __table_args__ = (UniqueConstraint("strategy_id", "seq", name="uq_strategy_lifecycle_events_seq"),)

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(ForeignKey("strategies.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    from_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    to_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    tree_hash: Mapped[str] = mapped_column(String(64), nullable=False)  # strategy files at transition time
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)  # JSON: ids + the gate checks that passed
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)

    def json(self, column: str) -> Any:
        return json.loads(getattr(self, column))


@event.listens_for(Session, "before_flush")
def _append_only(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in [*session.deleted, *session.dirty]:
        if isinstance(obj, LifecycleEvent) and (obj in session.deleted or session.is_modified(obj)):
            raise LifecycleError("lifecycle history is append-only")


def allowed(current: str, target: str) -> bool:
    if target not in STAGES:
        return False
    if target == current:
        return False
    if target in ("retired", "research"):
        return True  # retire or demote from anywhere (re-opening a retired strategy goes through research)
    return FORWARD.get(current) == target


class LifecycleLog:
    def __init__(self, clock: Clock | None = None, audit: AuditLog | None = None) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditLog(self._clock)

    def history(self, session: Session, strategy: Strategy) -> list[LifecycleEvent]:
        return list(
            session.execute(
                select(LifecycleEvent)
                .where(LifecycleEvent.strategy_id == strategy.id)
                .order_by(LifecycleEvent.seq)
            ).scalars()
        )

    def current(self, session: Session, strategy: Strategy) -> tuple[str, LifecycleEvent | None]:
        history = self.history(session, strategy)
        return (history[-1].to_stage, history[-1]) if history else ("research", None)

    def record(
        self,
        session: Session,
        strategy: Strategy,
        to_stage: str,
        *,
        tree_hash: str,
        reason: str,
        evidence: dict[str, Any],
        actor: str = "owner",
    ) -> LifecycleEvent:
        if not reason.strip():
            raise LifecycleError("a reason is required for every lifecycle transition")
        if strategy.archived:
            raise LifecycleError(f"strategy {strategy.name!r} is archived")
        stage, last = self.current(session, strategy)
        if not allowed(stage, to_stage):
            raise LifecycleError(f"{strategy.name}: transition {stage} → {to_stage} is not allowed")
        entry = LifecycleEvent(
            id=new_id(),
            strategy_id=strategy.id,
            seq=(last.seq + 1) if last else 1,
            from_stage=stage,
            to_stage=to_stage,
            tree_hash=tree_hash,
            actor=actor,
            reason=reason.strip(),
            evidence=json.dumps(evidence, sort_keys=True, default=str),
            created_at=self._clock.now(),
        )
        session.add(entry)
        session.flush()
        self._audit.record(
            session,
            actor_type="user",
            actor=actor,
            action="strategy.lifecycle",
            target=strategy.name,
            details={"from": stage, "to": to_stage, "tree_hash": tree_hash, "evidence": evidence},
        )
        return entry
