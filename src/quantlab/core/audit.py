"""Append-only, hash-chained audit log (CORE-010).

Every sensitive operation (mode switches, risk-limit changes, kill switch, credential changes,
deletions/archival, AI tool calls) is recorded here. Guarantees:

* **Append-only** — enforced twice: database triggers reject UPDATE/DELETE (migration 0001), and an
  ORM flush guard rejects modified/deleted entries before SQL is emitted.
* **Tamper-evident** — each entry stores ``sha256(prev_hash + canonical entry)``; ``verify_chain``
  finds the first entry whose hash or link does not match.
* **No secrets** — ``details`` passes through the same redaction as logging before it is stored.

Appends are serialised with a process lock (QuantLab is a single backend process, DEC-0002).
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import CheckConstraint, Integer, String, Text, event, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from quantlab.core.clock import Clock, SystemClock, ensure_utc
from quantlab.core.db import Base, UTCDateTime
from quantlab.core.ids import new_id
from quantlab.core.logging import redact

ActorType = Literal["user", "ai", "system"]
GENESIS_HASH = "0" * 64


class AuditLogError(RuntimeError):
    pass


class AuditEntry(Base):
    __tablename__ = "audit_log"
    __table_args__ = (CheckConstraint("actor_type IN ('user','ai','system')", name="actor_type"),)

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(26), unique=True, nullable=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor: Mapped[str] = mapped_column(String(200), nullable=False)
    action: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    target: Mapped[str | None] = mapped_column(String(500))
    details: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)


def _canonical(entry_id: str, ts: datetime, actor_type: str, actor: str, action: str,
               target: str | None, details: str) -> str:  # fmt: skip
    return json.dumps(
        {
            "id": entry_id,
            "ts": ensure_utc(ts).isoformat(timespec="microseconds"),
            "actor_type": actor_type,
            "actor": actor,
            "action": action,
            "target": target,
            "details": details,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def compute_hash(prev_hash: str, canonical: str) -> str:
    return hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()


@event.listens_for(Session, "before_flush")
def _reject_audit_mutation(session: Session, _ctx: Any, _instances: Any) -> None:
    for obj in session.dirty:
        if isinstance(obj, AuditEntry) and session.is_modified(obj):
            raise AuditLogError("audit_log is append-only: entries cannot be modified")
    for obj in session.deleted:
        if isinstance(obj, AuditEntry):
            raise AuditLogError("audit_log is append-only: entries cannot be deleted")


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    entries: int
    first_bad_id: str | None = None
    reason: str | None = None


class AuditLog:
    _lock = threading.Lock()

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def record(
        self,
        session: Session,
        *,
        actor_type: ActorType,
        actor: str,
        action: str,
        target: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditEntry:
        """Append an entry and flush it. The caller owns the transaction (commit/rollback)."""
        if actor_type not in ("user", "ai", "system"):
            raise ValueError(f"invalid actor_type: {actor_type!r}")
        if not actor or not action:
            raise ValueError("actor and action are required")
        details_json = json.dumps(redact(details or {}), sort_keys=True, default=str, separators=(",", ":"))
        with self._lock:
            last = session.execute(
                select(AuditEntry.hash).order_by(AuditEntry.seq.desc()).limit(1)
            ).scalar_one_or_none()
            prev = last or GENESIS_HASH
            entry_id = new_id()
            ts = ensure_utc(self._clock.now())
            canonical = _canonical(entry_id, ts, actor_type, actor, action, target, details_json)
            entry = AuditEntry(
                id=entry_id,
                ts=ts,
                actor_type=actor_type,
                actor=actor,
                action=action,
                target=target,
                details=details_json,
                prev_hash=prev,
                hash=compute_hash(prev, canonical),
            )
            session.add(entry)
            session.flush()
        return entry

    @staticmethod
    def verify_chain(session: Session) -> ChainCheck:
        prev = GENESIS_HASH
        count = 0
        for entry in session.execute(select(AuditEntry).order_by(AuditEntry.seq)).scalars():
            count += 1
            if entry.prev_hash != prev:
                return ChainCheck(False, count, entry.id, "broken link to previous entry")
            canonical = _canonical(
                entry.id, entry.ts, entry.actor_type, entry.actor, entry.action, entry.target, entry.details
            )
            if compute_hash(prev, canonical) != entry.hash:
                return ChainCheck(False, count, entry.id, "content hash mismatch")
            prev = entry.hash
        return ChainCheck(True, count)
