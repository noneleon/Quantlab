"""Pause / resume one paper deployment (LIVE-001, DEC-0034).

A paused deployment keeps running (LEAN still gets prices and can cancel its orders), but the gateway refuses
every new order from it. Pausing is a tightening and needs only a reason; resuming lets the strategy trade
again and needs the typed confirmation phrase from a person (CLAUDE.md §4). The state is the latest pause /
resume entry in the append-only audit log, so it survives restarts, is checked on every order and cannot be
changed without a record.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from quantlab.core.audit import AuditEntry, AuditLog

PAUSE = "paper.deploy.pause"
RESUME = "paper.deploy.resume"
RESUME_CONFIRMATION = "RESUME DEPLOYMENT"


class PauseError(RuntimeError):
    pass


@dataclass(frozen=True)
class PauseState:
    paused: bool
    reason: str | None = None
    actor: str | None = None
    since: str | None = None  # ISO timestamp of the pause


def pause_state(session: Session, deployment: str) -> PauseState:
    last = session.execute(
        select(AuditEntry)
        .where(AuditEntry.action.in_((PAUSE, RESUME)), AuditEntry.target == deployment)
        .order_by(AuditEntry.seq.desc())
        .limit(1)
    ).scalar_one_or_none()
    if last is None or last.action == RESUME:
        return PauseState(False)
    reason = json.loads(last.details or "{}").get("reason")
    return PauseState(True, reason, last.actor, last.ts.isoformat())


def paused_deployments(session: Session) -> dict[str, PauseState]:
    """Every deployment whose latest pause / resume entry is a pause."""
    out: dict[str, PauseState] = {}
    rows = session.execute(
        select(AuditEntry).where(AuditEntry.action.in_((PAUSE, RESUME))).order_by(AuditEntry.seq)
    ).scalars()
    for entry in rows:
        if entry.target is None:
            continue
        if entry.action == RESUME:
            out.pop(entry.target, None)
        else:
            reason = json.loads(entry.details or "{}").get("reason")
            out[entry.target] = PauseState(True, reason, entry.actor, entry.ts.isoformat())
    return out


def pause(
    session: Session, deployment: str, *, actor: str, reason: str, channel: str, audit: AuditLog | None = None
) -> None:
    """Record the pause (the caller commits). Pausing a paused deployment records the new reason."""
    if not reason.strip():
        raise PauseError("a reason is required")
    (audit or AuditLog()).record(
        session, actor_type="user", actor=actor, action=PAUSE, target=deployment,
        details={"reason": reason.strip(), "channel": channel},
    )  # fmt: skip


def resume(
    session: Session,
    deployment: str,
    *,
    actor: str,
    reason: str,
    confirmation: str,
    channel: str,
    audit: AuditLog | None = None,
) -> None:
    if confirmation != RESUME_CONFIRMATION:
        raise PauseError(f"to resume a deployment type exactly: {RESUME_CONFIRMATION}")
    if not reason.strip():
        raise PauseError("a reason is required")
    if not pause_state(session, deployment).paused:
        raise PauseError(f"deployment {deployment!r} is not paused")
    (audit or AuditLog()).record(
        session, actor_type="user", actor=actor, action=RESUME, target=deployment,
        details={"reason": reason.strip(), "channel": channel},
    )  # fmt: skip
