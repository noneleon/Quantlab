"""Paper trading supervisor (LIVE-001): keeps an OMS account in step with its broker while it runs.

Each tick: broker events are applied. Periodically: reconciliation, and equity samples marked with current
quotes (the drawdown limit's high-water mark). At the session open: today's start-of-day equity. After the
session close: DAY orders expire at brokers that do not expire them themselves (the local paper broker).

Safety (DEC-0030): the supervisor never submits orders and never disengages the kill switch. It *engages* the
kill switch — which can only reduce risk — when reconciliation finds a position difference (configurable).
Only one supervisor runs per account (OS-level lock). Errors are recorded in the status file and the loop
continues; a stopped supervisor is visible because its heartbeat goes stale.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from quantlab.core.clock import Clock, SystemClock
from quantlab.core.filelock import exclusive
from quantlab.core.logging import get_logger
from quantlab.data.calendars import SPECIAL_CALENDARS, session_bounds
from quantlab.oms.service import OMS
from quantlab.risk.gate import KillSwitch

log = get_logger(__name__)


@dataclass
class SupervisorStatus:
    account: str
    started_at: str
    interval_s: float
    heartbeat_at: str | None = None
    ticks: int = 0
    events_applied: int = 0
    last_reconcile_at: str | None = None
    last_reconcile_clean: bool | None = None
    last_reconcile_summary: dict[str, Any] = field(default_factory=dict)
    last_equity: float | None = None
    last_equity_at: str | None = None
    start_of_day_recorded: str | None = None  # session date
    day_orders_expired: str | None = None  # session date
    kill_switch_engaged_by_supervisor: str | None = None
    errors: list[str] = field(default_factory=list)  # most recent last (bounded)


class Supervisor:
    def __init__(
        self,
        oms: OMS,
        sessions: sessionmaker[Session],
        kill_switch: KillSwitch,
        status_file: Path,
        *,
        interval_s: float = 5.0,
        reconcile_every_s: float = 60.0,
        equity_every_s: float = 60.0,
        calendar: str = "XNYS",
        engage_on_mismatch: bool = True,
        clock: Clock | None = None,
    ) -> None:
        self._oms = oms
        self._sessions = sessions
        self._kill = kill_switch
        self._file = status_file
        self._interval = interval_s
        self._reconcile_every = reconcile_every_s
        self._equity_every = equity_every_s
        self._calendar = calendar
        self._engage = engage_on_mismatch
        self._clock = clock or SystemClock()
        self._last_reconcile: datetime | None = None
        self._last_equity: datetime | None = None
        self.status = SupervisorStatus(oms.account, self._clock.now().isoformat(), interval_s)

    # ---- session calendar ---------------------------------------------------------------------------
    def _session(self, now: datetime) -> tuple[date, datetime, datetime] | None:
        """Today's regular session (open, close) in UTC, or None on a closed day / without a calendar."""
        if self._calendar in ("none", "24/7") or self._calendar in SPECIAL_CALENDARS:
            return None
        sessions = session_bounds(self._calendar, now.date(), now.date())
        return sessions[0] if sessions else None

    # ---- one tick --------------------------------------------------------------------------------------
    def tick(self) -> SupervisorStatus:
        now = self._clock.now()
        st = self.status
        st.ticks += 1
        self._step("events", lambda: self._events())
        if self._due(self._last_reconcile, self._reconcile_every, now):
            self._last_reconcile = now
            self._step("reconcile", lambda: self._reconcile(now))
        session = self._session(now)
        in_session = session is not None and session[1] <= now < session[2]
        if self._calendar in ("none", "24/7"):
            in_session = True
        if in_session and self._due(self._last_equity, self._equity_every, now):
            self._last_equity = now
            day = (session[0] if session else now.date()).isoformat()
            first = st.start_of_day_recorded != day
            self._step("equity", lambda: self._equity(now, day, first))
        if session is not None and now >= session[2] and st.day_orders_expired != session[0].isoformat():
            self._step("expire", lambda: self._expire(session[0].isoformat()))
        st.heartbeat_at = now.isoformat()
        self._write()
        return st

    def _events(self) -> None:
        self.status.events_applied += self._oms.process_events()

    def _reconcile(self, now: datetime) -> None:
        report = self._oms.reconcile()
        st = self.status
        st.last_reconcile_at = now.isoformat()
        st.last_reconcile_clean = report.clean
        st.last_reconcile_summary = {
            "position_mismatches": report.position_mismatches,
            "open_order_mismatches": report.open_order_mismatches,
            "resolved": report.resolved,
            "cash_difference": report.cash_difference,
            "fills_added": report.fills_added,
        }
        if report.position_mismatches and self._engage:
            with self._sessions() as session:
                if not self._kill.engaged(session):
                    reason = (
                        f"supervisor: reconciliation found position differences {report.position_mismatches}"
                    )
                    self._kill.engage(session, actor="supervisor", reason=reason, channel="supervisor")
                    session.commit()
                    st.kill_switch_engaged_by_supervisor = now.isoformat()
                    log.warning("supervisor.kill_switch_engaged", account=self._oms.account)

    def _equity(self, now: datetime, day: str, first: bool) -> None:
        equity = self._oms.record_equity(source="supervisor", start_of_day=first)
        if equity is None:
            raise RuntimeError("equity not recorded: a held symbol has no quote")
        self.status.last_equity, self.status.last_equity_at = equity, now.isoformat()
        if first:
            self.status.start_of_day_recorded = day

    def _expire(self, day: str) -> None:
        self._oms.expire_day_orders()
        self.status.day_orders_expired = day

    def _step(self, name: str, fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # recorded, never fatal: the next tick tries again
            message = f"{self._clock.now().isoformat()} {name}: {exc!r}"
            log.warning("supervisor.step_failed", step=name, error=repr(exc))
            self.status.errors = [*self.status.errors, message][-20:]

    @staticmethod
    def _due(last: datetime | None, every: float, now: datetime) -> bool:
        return last is None or (now - last).total_seconds() >= every

    def _write(self) -> None:
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self.status), indent=1, default=str), encoding="utf-8")
        tmp.replace(self._file)

    # ---- loop ------------------------------------------------------------------------------------------
    def run(self, *, max_ticks: int | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        """Run until interrupted (or ``max_ticks``); one supervisor per account (lock held throughout)."""
        with exclusive(self._file.with_suffix(".lock")):
            while max_ticks is None or self.status.ticks < max_ticks:
                self.tick()
                if max_ticks is not None and self.status.ticks >= max_ticks:
                    break
                sleep(self._interval)


def read_status(path: Path, now: datetime) -> dict[str, Any]:
    """Status for the API/CLI: ``running`` is derived from the heartbeat age (3 intervals)."""
    if not path.is_file():
        return {"running": False, "status": None}
    data = json.loads(path.read_text(encoding="utf-8"))
    beat = data.get("heartbeat_at")
    age = (now - datetime.fromisoformat(beat)).total_seconds() if beat else None
    running = age is not None and age <= 3 * float(data.get("interval_s", 5.0)) + 5
    return {"running": running, "heartbeat_age_s": age, "status": data}
