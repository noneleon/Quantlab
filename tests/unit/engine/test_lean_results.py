import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from quantlab.engine.lean.results import ResultParseError, parse_results_dir, stat_value

FIXTURE = Path(__file__).parents[2] / "fixtures" / "lean" / "sample_result.json"


@pytest.fixture
def results_dir(tmp_path):
    shutil.copy(FIXTURE, tmp_path / "RUN1.json")
    return tmp_path


def test_parses_status_statistics_and_configuration(results_dir):
    r = parse_results_dir(results_dir, "RUN1")
    assert r.status == "Completed" and r.finished and r.succeeded and r.runtime_error == ""
    assert r.statistics["Sharpe Ratio"] == "1.028"
    assert r.runtime_statistics["Equity"] == "$99,839.57"
    assert r.start == datetime(2013, 10, 7, tzinfo=UTC)
    assert r.end == datetime(2013, 10, 11, 23, 59, 59, tzinfo=UTC)
    assert r.parameters == {"fast": "10", "slow": "30"}  # non-string values normalised
    assert r.source_files == (results_dir / "RUN1.json",)


def test_equity_series_handles_candles_xy_and_nulls(results_dir):
    eq = parse_results_dir(results_dir, "RUN1").equity
    assert [v for _, v in eq] == [100000.0, 100350.0, 99900.0, 99839.57]  # null point skipped, close used
    assert all(t.tzinfo is UTC for t, _ in eq)
    assert eq == tuple(sorted(eq))


def test_orders_sorted_with_readable_enums(results_dir):
    orders = parse_results_dir(results_dir, "RUN1").orders
    assert [o.id for o in orders] == [1, 2, 3]
    first, second, third = orders
    assert (first.type, first.direction, first.status, first.symbol) == ("Limit", "Buy", "Filled", "SPY")
    assert first.time == datetime(2013, 10, 7, 13, 31, tzinfo=UTC)  # naive ISO treated as UTC
    assert first.tag == ""  # null tag
    assert (second.type, second.direction, second.quantity) == ("Market", "Sell", -10.0)
    assert (third.type, third.status, third.direction, third.time) == ("TrailingStop", "42", "7", None)


def test_summary_statistics_used_when_main_file_has_none(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["statistics"] = {}
    (tmp_path / "R.json").write_text(json.dumps(data))
    (tmp_path / "R-summary.json").write_text(json.dumps({"statistics": {"Net Profit": "1%"}}))
    r = parse_results_dir(tmp_path, "R")
    assert r.statistics == {"Net Profit": "1%"} and len(r.source_files) == 2


def test_runtime_error_is_not_success(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["state"] = {"Status": "RuntimeError", "RuntimeError": "ZeroDivisionError in on_data"}
    (tmp_path / "R.json").write_text(json.dumps(data))
    r = parse_results_dir(tmp_path, "R")
    assert r.finished and not r.succeeded and "ZeroDivision" in r.runtime_error


def test_running_backtest_is_not_finished(tmp_path):
    (tmp_path / "R.json").write_text(json.dumps({"charts": {}}))
    r = parse_results_dir(tmp_path, "R")
    assert r.status == "Running" and not r.finished and r.orders == () and r.equity == ()


@pytest.mark.parametrize("content", ['{"charts": {"Strategy Eq', "[]"])
def test_partial_or_malformed_file_raises(tmp_path, content):
    (tmp_path / "R.json").write_text(content)
    with pytest.raises(ResultParseError):
        parse_results_dir(tmp_path, "R")


def test_missing_file_raises(tmp_path):
    with pytest.raises(ResultParseError, match="not found"):
        parse_results_dir(tmp_path, "nope")


def test_bad_time_raises(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["algorithmConfiguration"]["startDate"] = "not a date"
    (tmp_path / "R.json").write_text(json.dumps(data))
    with pytest.raises(ResultParseError, match="invalid time"):
        parse_results_dir(tmp_path, "R")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("-0.160%", -0.0016),
        ("$99,839.57", 99839.57),
        ("$-294.86", -294.86),
        ("-0.16 %", -0.0016),
        ("1.028", 1.028),
        ("2", 2.0),
        ("1e-3", 0.001),
        ("N/A", None),
        ("", None),
        ("12 days", None),
    ],
)
def test_stat_value(text, expected):
    got = stat_value(text)
    assert got == pytest.approx(expected) if expected is not None else got is None
