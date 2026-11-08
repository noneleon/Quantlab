import pandas as pd

from quantlab.data.providers.base import BarRequest
from quantlab.data.providers.csv_file import CsvFileProvider
from quantlab.data.schema import Resolution
from quantlab.data.validation import validate_bars
from tests.unit.data.conftest import SESSIONS, csv_text, daily_rows


def frame(tmp_path, rows):
    path = tmp_path / "f.csv"
    path.write_text(csv_text(rows))
    return (
        CsvFileProvider(path, calendar="XNYS", symbol="SPY")
        .fetch_bars(BarRequest((), Resolution.DAILY))
        .frame
    )


def checks(report, severity):
    return {i.check for i in report.issues if i.severity == severity}


def test_clean_data_passes_with_full_completeness(tmp_path):
    df, report = validate_bars(frame(tmp_path, daily_rows()), Resolution.DAILY, "XNYS")
    assert report.ok and report.issues == [] and report.completeness == {"SPY": 1.0} and len(df) == 6


def test_missing_session_is_a_warning_with_completeness(tmp_path):
    rows = daily_rows([d for d in SESSIONS if d != "2024-07-05"])
    _, report = validate_bars(frame(tmp_path, rows), Resolution.DAILY, "XNYS")
    assert report.ok and checks(report, "warning") == {"missing_sessions"}
    issue = report.warnings[0]
    assert issue.examples == ("2024-07-05",) and report.completeness["SPY"] == round(5 / 6, 6)


def test_errors_block(tmp_path):
    rows = daily_rows()
    rows[1] = "2024-07-02,100,99,98,101,0,10"  # high < close
    rows[2] = "2024-07-03,-1,2,0.5,1,0,10"  # negative price
    rows[3] = "2024-07-05,1,2,0.5,1.5,0,-5"  # negative volume
    _, report = validate_bars(frame(tmp_path, rows), Resolution.DAILY, "XNYS")
    assert not report.ok
    assert {"ohlc_consistency", "price_values", "volume_negative"} <= checks(report, "error")


def test_duplicates_exact_removed_conflicting_rejected(tmp_path):
    rows = daily_rows()
    exact, _ = validate_bars(frame(tmp_path, [*rows, rows[0]]), Resolution.DAILY, "XNYS")
    _, report = validate_bars(frame(tmp_path, [*rows, rows[0]]), Resolution.DAILY, "XNYS")
    assert report.ok and "duplicate_exact" in checks(report, "warning") and len(exact) == 6
    conflicting = [*rows, rows[0].replace(",101.00,", ",101.50,")]
    _, report = validate_bars(frame(tmp_path, conflicting), Resolution.DAILY, "XNYS")
    assert "duplicate_conflict" in checks(report, "error")


def test_jump_and_stale_warnings(tmp_path):
    rows = [f"{d},100,101,99,100,0,10" for d in SESSIONS]  # 6 identical closes
    rows[-1] = f"{SESSIONS[-1]},300,310,290,300,0,10"
    _, report = validate_bars(frame(tmp_path, rows), Resolution.DAILY, "XNYS")
    assert report.ok and {"price_jump", "stale_prices"} <= checks(report, "warning")


def test_non_session_bar_is_error():
    df = pd.DataFrame(
        {
            "symbol": ["X"],
            "ts": [pd.Timestamp("2024-07-06T20:00:00Z")],
            "open": [1.0],
            "high": [1.0],
            "low": [1.0],
            "close": [1.0],
            "volume": [1.0],
        }
    )  # Saturday
    _, report = validate_bars(df, Resolution.DAILY, "XNYS")
    assert "non_session_bar" in checks(report, "error")


def test_empty_frame_is_error():
    empty = pd.DataFrame(columns=["symbol", "ts", "open", "high", "low", "close", "volume"])
    _, report = validate_bars(empty, Resolution.DAILY, "XNYS")
    assert not report.ok and report.errors[0].check == "empty"


def minute_frame(day, minutes, start="13:31", extra=()):
    ends = [pd.Timestamp(f"{day}T{start}:00Z") + pd.Timedelta(minutes=i) for i in range(minutes)]
    ends += [pd.Timestamp(e) for e in extra]
    prices = [
        100.0 + (i % 7) * 0.01 for i in range(len(ends))
    ]  # synthetic, varying (no stale-price warnings)
    return pd.DataFrame(
        {
            "symbol": "SPY",
            "ts": ends,
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": 1.0,
        }
    )


def test_minute_coverage_full_session_and_early_close():
    _, full = validate_bars(minute_frame("2024-07-02", 390), Resolution.MINUTE, "XNYS")
    assert full.ok and full.completeness == {"SPY": 1.0} and full.issues == []
    _, early = validate_bars(minute_frame("2024-07-03", 210), Resolution.MINUTE, "XNYS")  # 13:00 ET close
    assert early.completeness == {"SPY": 1.0}


def test_missing_minutes_and_extended_hours_are_warnings():
    frame = minute_frame("2024-07-02", 380, extra=["2024-07-02T12:00:00Z"])
    _, report = validate_bars(frame, Resolution.MINUTE, "XNYS")
    assert report.ok
    assert {i.check for i in report.warnings} == {"missing_minutes", "outside_regular_session"}
    assert report.completeness["SPY"] == round(380 / 390, 6)
    assert report.warnings[0].examples[0].startswith("2024-07-02 (10 missing)")
