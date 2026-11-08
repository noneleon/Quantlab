from datetime import UTC, date, datetime

import pytest

from quantlab.data.calendars import (
    CalendarError,
    check_calendar,
    default_calendar,
    session_close_utc,
    sessions,
)
from quantlab.data.schema import AssetClass


def test_nyse_sessions_skip_holidays_and_weekends():
    got = sessions("XNYS", date(2024, 7, 1), date(2024, 7, 9))
    assert [d.day for d in got] == [1, 2, 3, 5, 8, 9]


def test_nyse_close_handles_dst_and_early_close():
    assert session_close_utc("XNYS", date(2024, 7, 1)) == datetime(2024, 7, 1, 20, tzinfo=UTC)  # EDT
    assert session_close_utc("XNYS", date(2024, 1, 2)) == datetime(2024, 1, 2, 21, tzinfo=UTC)  # EST
    assert session_close_utc("XNYS", date(2024, 7, 3)) == datetime(2024, 7, 3, 17, tzinfo=UTC)  # early close


def test_special_calendars():
    assert len(sessions("24/7", date(2024, 7, 1), date(2024, 7, 7))) == 7
    assert len(sessions("weekdays", date(2024, 7, 1), date(2024, 7, 7))) == 5
    assert sessions("none", date(2024, 7, 1), date(2024, 7, 7)) == []
    assert session_close_utc("24/7", date(2024, 7, 1)) == datetime(2024, 7, 2, tzinfo=UTC)


def test_defaults_and_errors():
    assert default_calendar(AssetClass.EQUITY, "usa") == "XNYS"
    assert default_calendar(AssetClass.CRYPTO, "binance") == "24/7"
    assert default_calendar(AssetClass.EQUITY, "mars") == "none"
    with pytest.raises(CalendarError):
        check_calendar("NOPE")
    with pytest.raises(CalendarError):
        session_close_utc("XNYS", date(2024, 7, 4))


def test_historical_sessions_before_default_calendar_window():
    """Regression (CHANGE-0013): exchange_calendars defaults to ~20 years; 1998 data must still validate."""
    jan_1998 = sessions("XNYS", date(1998, 1, 1), date(1998, 1, 31))
    assert len(jan_1998) == 20 and jan_1998[0] == date(1998, 1, 2)
    assert session_close_utc("XNYS", date(1998, 1, 2)) == datetime(1998, 1, 2, 21, tzinfo=UTC)
