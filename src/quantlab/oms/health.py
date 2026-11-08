"""Health checks for unattended paper trading (OPS-001).

Each check reads state that already exists (database, risk limits file, kill switch, supervisor and
deployment status files, the native LEAN install) and never contacts a broker or sends anything. ``ok`` =
fine, ``warn`` = needs the owner's attention but nothing is unsafe, ``fail`` = something that should be
running is not (or every order is rejected). The overall status is the worst one.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from quantlab.engine.lean.native import MANIFEST
from quantlab.oms.deployment import read_deployments
from quantlab.oms.pausing import paused_deployments
from quantlab.oms.supervisor import read_status
from quantlab.risk.gate import KillSwitch
from quantlab.risk.limits import load_limits

Status = Literal["ok", "warn", "fail"]
_ORDER = {"ok": 0, "warn": 1, "fail": 2}


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass(frozen=True)
class HealthReport:
    status: Status
    checked_at: str
    checks: list[Check]

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "checked_at": self.checked_at,
                "checks": [asdict(c) for c in self.checks]}  # fmt: skip


def run_checks(
    sessions: sessionmaker[Session],
    *,
    home: Path,
    kill_switch: KillSwitch,
    limits_file: Path,
    supervisor_file: Path,
    native_dir: Path,
    launcher: str,
    now: datetime,
) -> HealthReport:
    checks = [
        _guard("database", lambda: _database(sessions)),
        _guard("risk limits", lambda: _limits(limits_file)),
        _guard("kill switch", lambda: _kill(sessions, kill_switch)),
        _guard("supervisor", lambda: _supervisor(supervisor_file, now)),
        _guard("deployments", lambda: _deployments(sessions, home, now)),
        _guard("engine", lambda: _engine(native_dir, launcher)),
    ]
    worst = max((c.status for c in checks), key=_ORDER.__getitem__)
    return HealthReport(worst, now.isoformat(), checks)


def _guard(name: str, check: Callable[[], tuple[Status, str]]) -> Check:
    try:
        status, detail = check()
    except Exception as exc:  # a broken check is a failure, never a crash of the health report
        return Check(name, "fail", f"check failed: {exc}")
    return Check(name, status, detail)


def _database(sessions: sessionmaker[Session]) -> tuple[Status, str]:
    with sessions() as session:
        session.execute(text("SELECT 1"))
    return "ok", "reachable"


def _limits(path: Path) -> tuple[Status, str]:
    try:
        load_limits(path)
    except Exception as exc:  # missing / invalid: the risk gate rejects every order
        return "fail", str(exc)
    return "ok", str(path)


def _kill(sessions: sessionmaker[Session], kill: KillSwitch) -> tuple[Status, str]:
    with sessions() as session:
        last = kill.state(session)
    if last is not None and last.engaged:
        return "warn", f"ENGAGED by {last.actor} at {last.created_at.isoformat()}: {last.reason}"
    return "ok", "not engaged"


def _supervisor(path: Path, now: datetime) -> tuple[Status, str]:
    info = read_status(path, now)
    status = info.get("status") or {}
    if not info["running"]:
        if status:
            return (
                "fail",
                f"not running (last heartbeat {status.get('heartbeat_at')}): start `quantlab paper "
                "supervise`",
            )
        return "warn", "never started: run `quantlab paper supervise` for reconciliation and DAY-order expiry"
    if not info["healthy"]:
        errors = status.get("errors") or []
        last = errors[-1] if errors else status.get("last_reconcile_error") or "no clean reconcile recently"
        return "fail", f"running but unhealthy: {last}"
    return "ok", f"running, last reconcile {status.get('last_reconcile_at')}"


def _deployments(sessions: sessionmaker[Session], home: Path, now: datetime) -> tuple[Status, str]:
    rows = read_deployments(home, now)
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:  # newest run of each deployment
        latest.setdefault(str(row.get("deployment")), row)
    with sessions() as session:
        paused = paused_deployments(session)
    lost = sorted(n for n, r in latest.items() if r.get("state") == "lost")
    if lost:
        return "fail", f"no heartbeat (process gone): {', '.join(lost)}"
    running = sorted(n for n, r in latest.items() if r.get("running"))
    notes = []
    exposed = sorted(n for n in running if latest[n].get("isolated") is False)
    if exposed:
        notes.append(f"not isolated: {', '.join(exposed)}")
    paused_running = sorted(n for n in running if n in paused)
    if paused_running:
        notes.append(f"paused: {', '.join(paused_running)}")
    if notes:
        return "warn", f"{len(running)} running; " + "; ".join(notes)
    return "ok", f"{len(running)} running" if running else "none running"


def _engine(native_dir: Path, launcher: str) -> tuple[Status, str]:
    if (native_dir / MANIFEST).is_file():
        return "ok", f"native LEAN installed at {native_dir}"
    if launcher == "native":
        return (
            "fail",
            "engine.lean.launcher is 'native' but nothing is installed: `quantlab engine native-install`",
        )
    return "ok", f"{launcher} launcher (no native install; paper deployments need one)"
