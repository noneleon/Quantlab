"""Settlement of sale proceeds in a cash account (RISK-006).

US equities settle T+1 (since 2024-05-28): the proceeds of a sale become *settled cash* on the next
trading day. Buying with unsettled proceeds and selling that purchase before the proceeds settle is a
good-faith violation (free-riding when the buy was never paid for); brokers restrict cash accounts that do
it. The risk gate therefore lets a cash account buy only with settled cash (conservative: it also blocks
buys that would merely be "good faith" if held). The trade date is the US/Eastern date of the execution.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from quantlab.data.calendars import sessions

EXCHANGE_TZ = ZoneInfo("America/New_York")


def settlement_calendar(trading_calendar: str) -> str:
    """Business days without holidays when the risk limits use no exchange calendar ("none", "24/7")."""
    return "weekdays" if trading_calendar in ("none", "24/7", "weekdays") else trading_calendar


def settlement_date(executed_at: datetime, days: int, trading_calendar: str) -> date:
    trade_date = executed_at.astimezone(EXCHANGE_TZ).date()
    if days <= 0:
        return trade_date
    following = sessions(settlement_calendar(trading_calendar), trade_date + timedelta(days=1),
                         trade_date + timedelta(days=10 + 3 * days))  # fmt: skip
    if len(following) < days:
        raise ValueError(f"no {days} trading day(s) after {trade_date} in calendar {trading_calendar!r}")
    return following[days - 1]


def unsettled_proceeds(
    sales: Sequence[tuple[datetime, float]], now: datetime, days: int, trading_calendar: str
) -> float:
    """Net proceeds of sales not settled by ``now`` (settled cash is available on the settle date)."""
    today = now.astimezone(EXCHANGE_TZ).date()
    return sum(
        proceeds
        for executed_at, proceeds in sales
        if proceeds > 0 and settlement_date(executed_at, days, trading_calendar) > today
    )
