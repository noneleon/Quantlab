from datetime import UTC, datetime

import pandas as pd
import pytest

from quantlab.data.providers.base import BarRequest, DataProvider, ProviderError
from quantlab.data.providers.csv_file import ColumnMap, CsvFileProvider
from quantlab.data.schema import BAR_COLUMNS, Resolution
from tests.unit.data.conftest import csv_text

DAILY = BarRequest((), Resolution.DAILY)


def test_daily_dates_become_session_close_and_adj_close_ignored(daily_csv):
    p = CsvFileProvider(daily_csv, calendar="XNYS", symbol="spy")
    assert isinstance(p, DataProvider)
    raw = p.fetch_bars(DAILY)
    f = raw.frame
    assert list(f.columns) == list(BAR_COLUMNS) and (f["symbol"] == "SPY").all()
    assert f["ts"].iloc[0] == pd.Timestamp(datetime(2024, 7, 1, 20, tzinfo=UTC))
    assert f["ts"].iloc[2] == pd.Timestamp(datetime(2024, 7, 3, 17, tzinfo=UTC))  # early close
    assert f["close"].iloc[0] == 101.0  # raw close, not Adj Close (100.5)
    assert any("adjusted-close" in n for n in raw.notes)
    assert raw.source == "file:spy.csv" and not p.license.redistribution_allowed


def test_intraday_naive_times_localized_and_shifted(tmp_path):
    path = tmp_path / "m.csv"
    path.write_text(
        csv_text(["2024-07-01 09:30:00,1,2,0.5,1.5,1,10"], "timestamp,open,high,low,close,x,volume")
    )
    p = CsvFileProvider(
        path, calendar="XNYS", symbol="SPY", timezone="America/New_York", timestamp_is_bar_start=True
    )
    f = p.fetch_bars(BarRequest((), Resolution.MINUTE)).frame
    assert f["ts"].iloc[0] == pd.Timestamp("2024-07-01T13:31:00Z")


def test_symbol_column_filter_and_explicit_columns(tmp_path):
    path = tmp_path / "multi.csv"
    path.write_text("Ticker,D,O,H,L,C\nAAA,2024-07-01,1,2,0.5,1.5\nBBB,2024-07-01,3,4,2.5,3.5\n")
    cols = ColumnMap(ts="D", open="O", high="H", low="L", close="C")
    p = CsvFileProvider(path, calendar="XNYS", columns=cols)
    f = p.fetch_bars(DAILY).frame
    assert sorted(f["symbol"]) == ["AAA", "BBB"] and f["volume"].isna().all()
    only = CsvFileProvider(path, calendar="XNYS", columns=cols, symbol="bbb").fetch_bars(DAILY).frame
    assert list(only["symbol"]) == ["BBB"]


@pytest.mark.parametrize(
    ("content", "kwargs", "match"),
    [
        ("Date,Open,High,Low\n2024-07-01,1,2,0.5\n", {"symbol": "X"}, "required columns"),
        ("Date,Open,High,Low,Close\nnot-a-date,1,2,0.5,1\n", {"symbol": "X"}, "unparseable"),
        ("Date,Open,High,Low,Close\n2024-07-04,1,2,0.5,1\n", {"symbol": "X"}, "not a trading session"),
        ("Date,Open,High,Low,Close\n2024-07-01,1,2,0.5,1\n", {}, "no symbol column"),
    ],
)
def test_errors(tmp_path, content, kwargs, match):
    path = tmp_path / "bad.csv"
    path.write_text(content)
    with pytest.raises(ProviderError, match=match):
        CsvFileProvider(path, calendar="XNYS", **kwargs).fetch_bars(DAILY)


def test_missing_file_and_bad_timezone(tmp_path):
    with pytest.raises(ProviderError, match="not found"):
        CsvFileProvider(tmp_path / "x.csv", calendar="XNYS", symbol="X").fetch_bars(DAILY)
    with pytest.raises(ProviderError, match="timezone"):
        CsvFileProvider(tmp_path / "x.csv", calendar="XNYS", symbol="X", timezone="Mars/Base")
