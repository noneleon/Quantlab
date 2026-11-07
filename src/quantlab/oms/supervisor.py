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
from datetime import date, datetime, timedelta
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
Session_ = tuple[date, datetime, datetime]  # (session date, open UTC, close UTC)


@dataclass
class SupervisorStatus:
    account: str
    started_at: str
    interval_s: float
    reconcile_every_s: float = 60.0
    heartbeat_at: str | None = None
    healthy: bool = False  # last tick without errors and a successful reconcile within 2 intervals
    ticks: int = 0
    events_applied: int = 0
    last_reconcile_at: str | None = None  # last *successful* reconcile
    last_reconcile_clean: bool | None = None  # None after a failed reconcile
    last_reconcile_error: str | None = None
    last_reconcile_summary: dict[str, Any] = field(default_factory=dict)
    last_equity: float | None = None
    last_equity_at: str | None = None
    start_of_day_recorded: str | None = None  # session date
    day_orders_expired: str | None = None  # session date (persisted across restarts)
    kill_switch_engaged_by_supervisor: str | None = None
    last_error_at: str | None = None
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
        equity_quote_max_age_s: float = 60.0,
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
        self._quote_age = equity_quote_max_age_s
        self._clock = clock or SystemClock()
        self._last_reconcile: datetime | None = None
        self._last_equity: datetime | None = None
        now = self._clock.now()
        if calendar not in ("none", "24/7") and calendar not in SPECIAL_CALENDARS:
            session_bounds(calendar, now.date(), now.date())  # an unknown calendar fails here, at start-up
        self.status = SupervisorStatus(oms.account, now.isoformat(), interval_s, reconcile_every_s)
        previous = self._previous()  # after a restart: never expire or record twice for the same session
        self.status.day_orders_expired = previous.get("day_orders_expired")
        self.status.start_of_day_recorded = previous.get("start_of_day_recorded")

    def _previous(self) -> dict[str, Any]:
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) and data.get("account") == self._oms.account else {}
        except (OSError, ValueError):
            return {}

    # ---- session calendar ---------------------------------------------------------------------------
    def _sessions_around(self, now: datetime) -> list[tuple[date, datetime, datetime]]:
        """Regular sessions of the last week up to today (UTC), for the open and the latest close."""
        if self._calendar in ("none", "24/7") or self._calendar in SPECIAL_CALENDARS:
            return []
        return session_bounds(self._calendar, now.date() - timedelta(days=7), now.date() + timedelta(days=1))

    def _in_session(self, now: datetime, sessions: list[Session_]) -> date | None:
        """The session date if ``now`` is in a regular session (no calendar: always; weekdays: Mon-Fri)."""
        if self._calendar in ("none", "24/7"):
            return now.date()
        if self._calendar == "weekdays" or self._calendar in SPECIAL_CALENDARS:
            return now.date() if now.weekday() < 5 or self._calendar != "weekdays" else None
        for day, open_, close in sessions:
            if open_ <= now < close:
                return day
        return None

    # ---- one tick --------------------------------------------------------------------------------------
    def tick(self) -> SupervisorStatus:
        now = self._clock.now()
        st = self.status
        st.ticks += 1
        errors_before = len(st.errors)
        self._step("events", self._events)
        if self._due(self._last_reconcile, self._reconcile_every, now):
            self._last_reconcile = now
            self._step("reconcile", lambda: self._reconcile(now), on_error=self._reconcile_failed)
        sessions: list[tuple[date, datetime, datetime]] = []
        self._step("calendar", lambda: sessions.extend(self._sessions_around(now)))
        day = self._in_session(now, sessions)
        if day is not None and self._due(self._last_equity, self._equity_every, now):
            self._last_equity = now
            first = st.start_of_day_recorded != day.isoformat()
            self._step("equity", lambda: self._equity(now, day.isoformat(), first))
        closed = [(d, close) for d, _o, close in sessions if close <= now]  # latest close that has passed
        if closed and st.day_orders_expired != closed[-1][0].isoformat():
            last_day, last_close = closed[-1]
            if st.day_orders_expired is None or st.day_orders_expired < last_day.isoformat():
                self._step("expire", lambda: self._expire(last_day.isoformat(), last_close))
        st.heartbeat_at = now.isoformat()
        recent_reconcile = (
            st.last_reconcile_at is not None
            and (now - datetime.fromisoformat(st.last_reconcile_at)).total_seconds()
            <= 2 * self._reconcile_every
        )
        st.healthy = len(st.errors) == errors_before and recent_reconcile and st.last_reconcile_clean is True
        self._write()
        return st

    def _events(self) -> None:
        self.status.events_applied += self._oms.process_events()

    def _reconcile(self, now: datetime) -> None:
        report = self._oms.reconcile()
        st = self.status
        st.last_reconcile_at = now.isoformat()
        st.last_reconcile_clean = report.clean
        st.last_reconcile_error = None
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

    def _reconcile_failed(self, exc: Exception) -> None:
        self.status.last_reconcile_clean = None  # a failed reconcile is never shown as "clean"
        self.status.last_reconcile_error = repr(exc)

    def _equity(self, now: datetime, day: str, first: bool) -> None:
        equity = self._oms.record_equity(
            source="supervisor", start_of_day=first, max_quote_age_s=self._quote_age
        )
        if equity is None:
            raise RuntimeError("equity not recorded: a held symbol has no usable quote")
        self.status.last_equity, self.status.last_equity_at = equity, now.isoformat()
        if first:
            self.status.start_of_day_recorded = day

    def _expire(self, day: str, closed_at: datetime) -> None:
        self._oms.expire_day_orders(closed_at=closed_at)  # only DAY orders created before that close
        self.status.day_orders_expired = day

    def _step(
        self, name: str, fn: Callable[[], object], on_error: Callable[[Exception], None] | None = None
    ) -> None:
        try:
            fn()
        except Exception as exc:  # recorded, never fatal: the next tick tries again
            now = self._clock.now().isoformat()
            log.warning("supervisor.step_failed", step=name, error=repr(exc))
            self.status.errors = [*self.status.errors, f"{now} {name}: {exc!r}"][-20:]
            self.status.last_error_at = now
            if on_error is not None:
                on_error(exc)

    @staticmethod
    def _due(last: datetime | None, every: float, now: datetime) -> bool:
        return last is None or (now - last).total_seconds() >= every

    def _write(self) -> None:
        """Atomic status write; a failure (a reader holding the file on Windows) is retried, never fatal."""
        tmp = self._file.with_name(self._file.name + ".tmp")
        data = json.dumps(asdict(self.status), indent=1, default=str)
        for attempt in range(5):
            try:
                self._file.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(data, encoding="utf-8")
                tmp.replace(self._file)
                return
            except OSError as exc:
                if attempt == 4:
                    log.warning("supervisor.status_write_failed", error=repr(exc))
                    return
                time.sleep(0.05)

    # ---- loop ------------------------------------------------------------------------------------------
    def run(self, *, max_ticks: int | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        """Run until interrupted (or ``max_ticks``); one supervisor per account (lock held throughout)."""
        with exclusive(self._file.with_name(self._file.name + ".lock")):
            while max_ticks is None or self.status.ticks < max_ticks:
                self.tick()
                if max_ticks is not None and self.status.ticks >= max_ticks:
                    break
                sleep(self._interval)


def read_status(path: Path, now: datetime) -> dict[str, Any]:
    """Status for the API/CLI: ``running`` is derived from the heartbeat age (3 intervals)."""
    if not path.is_file():
        return {"running": False, "healthy": False, "status": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        beat = data.get("heartbeat_at")
        age = (now - datetime.fromisoformat(beat)).total_seconds() if beat else None
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return {"running": False, "healthy": False, "status": None, "error": f"unreadable status file: {exc}"}
    # a heartbeat in the future (clock skew) is not proof of life
    running = age is not None and 0 <= age <= 3 * float(data.get("interval_s", 5.0)) + 5
    return {
        "running": running,
        "healthy": running and bool(data.get("healthy")),
        "heartbeat_age_s": age,
        "status": data,
    }
