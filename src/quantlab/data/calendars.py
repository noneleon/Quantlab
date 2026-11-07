"""Trading calendars (DATA-007).

A dataset names its calendar explicitly:
* an ``exchange_calendars`` code such as ``XNYS`` (NYSE), ``XNAS``, ``XLON`` …,
* ``24/7`` (crypto: every UTC calendar day is a session, closing at 00:00 UTC of the next day),
* ``weekdays`` (approximate forex: Monday-Friday, closing 21:00 UTC — documented approximation),
* ``none`` (no session checks; gaps cannot be detected).

Defaults per asset class/market are in ``default_calendar``. Backtests additionally use LEAN's own
market-hours database; differences between the two are a known risk (KNOWN_ISSUES).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from typing import Any

import pandas as pd

from quantlab.data.schema import AssetClass, Resolution

SPECIAL_CALENDARS = ("24/7", "weekdays", "none")
CALENDAR_START = "1970-01-02"


class CalendarError(ValueError):
    pass


def default_calendar(asset_class: AssetClass, market: str) -> str:
    if asset_class is AssetClass.CRYPTO:
        return "24/7"
    if asset_class is AssetClass.FOREX:
        return "weekdays"
    if market.lower() == "usa":
        return "XNYS"
    return "none"


@lru_cache(maxsize=16)
def _exchange_calendar(code: str) -> Any:
    import exchange_calendars

    try:
        # exchange_calendars covers only ~20 years by default; historical datasets need more (CHANGE-0013).
        return exchange_calendars.get_calendar(code, start=CALENDAR_START)
    except ValueError:
        try:  # calendar cannot reach back to CALENDAR_START: use its own earliest supported range
            return exchange_calendars.get_calendar(code)
        except Exception as exc:
            raise CalendarError(f"unknown calendar {code!r}") from exc
    except Exception as exc:  # exchange_calendars raises its own InvalidCalendarName
        raise CalendarError(f"unknown calendar {code!r}") from exc


def check_calendar(code: str) -> str:
    if code not in SPECIAL_CALENDARS:
        _exchange_calendar(code)
    return code


def sessions(code: str, start: date, end: date) -> list[date]:
    """Trading sessions between start and end (inclusive). Empty for 'none'."""
    if end < start or code == "none":
        return []
    if code == "24/7":
        return [start + timedelta(days=i) for i in range((end - start).days + 1)]
    if code == "weekdays":
        days = (start + timedelta(days=i) for i in range((end - start).days + 1))
        return [d for d in days if d.weekday() < 5]
    cal = _exchange_calendar(code)
    first, last = max(start, cal.first_session.date()), min(end, cal.last_session.date())
    if last < first:
        return []
    return [ts.date() for ts in cal.sessions_in_range(pd.Timestamp(first), pd.Timestamp(last))]


def is_session(code: str, day: date) -> bool:
    return code == "none" or bool(sessions(code, day, day))


def session_close_utc(code: str, day: date) -> datetime:
    """UTC close of the session on ``day`` — the canonical end time of a daily bar."""
    if code == "24/7" or code == "none":
        return datetime.combine(day + timedelta(days=1), time(0), tzinfo=UTC)
    if code == "weekdays":
        return datetime.combine(day, time(21), tzinfo=UTC)
    cal = _exchange_calendar(code)
    try:
        close = cal.session_close(pd.Timestamp(day))
    except Exception as exc:
        raise CalendarError(f"{day.isoformat()} is not a {code} session") from exc
    result: datetime = close.to_pydatetime().astimezone(UTC)
    return result


def session_dates(ts: pd.Series, calendar: str, resolution: Resolution = Resolution.DAILY) -> pd.Series:
    """Session (exchange-local) date of each bar from its canonical end time.

    Daily bars end at the session close (``session_close_utc``); intraday bars are dated by their *start* in
    exchange time (UTC for ``24/7``/``weekdays``/``none``).
    """
    utc = pd.to_datetime(ts, utc=True)
    if resolution is Resolution.DAILY:
        if calendar in ("24/7", "none"):
            return (utc - pd.Timedelta(days=1)).dt.date  # daily bar ends 00:00 UTC of the following day
        if calendar == "weekdays":
            return utc.dt.date
        return utc.dt.tz_convert(_exchange_calendar(calendar).tz).dt.date
    start = utc - pd.Timedelta(seconds=resolution.seconds)
    if calendar in SPECIAL_CALENDARS:
        return start.dt.date
    return start.dt.tz_convert(_exchange_calendar(calendar).tz).dt.date
