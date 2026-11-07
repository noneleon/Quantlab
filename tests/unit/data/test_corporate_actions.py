"""Corporate-action factors — golden test against LEAN's real AAPL factor file.

Events are decoded from LEAN's file; the decoded dividends/splits are checked against AAPL's actual history
(independent validation of the formula), then QuantLab recomputes the factor rows and must reproduce
LEAN's file.
"""

from datetime import date
from itertools import pairwise
from pathlib import Path

import pandas as pd
import pytest

from quantlab.data.calendars import sessions
from quantlab.data.corporate_actions import (
    FAR_FUTURE,
    CorporateActionError,
    actions_hash,
    adjust_bars,
    factor_file_rows,
    normalize_actions,
    validate_actions,
)

LEAN_AAPL = Path(__file__).parents[2] / "fixtures" / "lean" / "aapl_factor_file.csv"


def lean_rows():
    rows = []
    for line in LEAN_AAPL.read_text().split():
        d, pf, sf, ref = line.split(",")
        rows.append((date(int(d[:4]), int(d[4:6]), int(d[6:])), float(pf), float(sf), float(ref)))
    return rows


def next_session(day):
    following = sessions("XNYS", day, date(day.year + 1, 12, 31))
    return next(d for d in following if d > day)


def decode_events(rows):
    """LEAN rows -> (actions frame, closes series) for AAPL."""
    actions, closes = [], {rows[0][0]: 16.25}  # first data close (LEAN sample daily), not used by the factors
    for (d, pf, sf, ref), (_, pf_next, sf_next, _) in pairwise(rows[1:]):
        closes[d] = ref
        ex = next_session(d)
        if abs(pf / pf_next - 1) > 1e-9:
            actions.append(
                {"symbol": "AAPL", "ex_date": ex, "kind": "dividend", "value": ref * (1 - pf / pf_next)}
            )
        if abs(sf / sf_next - 1) > 1e-9:
            actions.append({"symbol": "AAPL", "ex_date": ex, "kind": "split", "value": sf_next / sf})
    return normalize_actions(pd.DataFrame(actions)), pd.Series(closes).sort_index()


def test_decoded_events_match_real_aapl_history():
    actions, _ = decode_events(lean_rows())
    by_key = {(r.ex_date, r.kind): r.value for r in actions.itertuples()}
    # Splits: 2:1 (2000-06-21), 2:1 (2005-02-28), 7:1 (2014-06-09), 4:1 (2020-08-31)
    for day, ratio in [
        (date(2000, 6, 21), 2),
        (date(2005, 2, 28), 2),
        (date(2014, 6, 9), 7),
        (date(2020, 8, 31), 4),
    ]:
        assert by_key[(day, "split")] == pytest.approx(ratio, rel=1e-5)
    # Cash dividends (raw, per share at the time): $2.65 (2012-08-09), $0.82 (2020-08-07), $0.205 (2020-11-06)
    for day, cash in [(date(2012, 8, 9), 2.65), (date(2020, 8, 7), 0.82), (date(2020, 11, 6), 0.205)]:
        assert by_key[(day, "dividend")] == pytest.approx(cash, abs=0.005)


def test_recomputed_factor_rows_reproduce_lean_file():
    rows = lean_rows()
    actions, closes = decode_events(rows)
    ours = factor_file_rows(closes, actions)
    assert [r.date for r in ours] == [r[0] for r in rows]
    for mine, (_, pf, sf, ref) in zip(ours, rows, strict=True):
        assert mine.price_factor == pytest.approx(pf, rel=2e-6)
        assert mine.split_factor == pytest.approx(sf, rel=2e-6)
        assert mine.reference_price == pytest.approx(ref)
    assert ours[-1].date == FAR_FUTURE and ours[-1].lean_line() == "20501231,1,1,0"
    assert ours[0].lean_line().endswith(",1")  # LEAN's first row uses reference price 1


def frame(days, closes, symbol="X"):
    return pd.DataFrame(
        {
            "symbol": symbol,
            "ts": pd.to_datetime([f"{d} 20:00" for d in days], utc=True),
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": 100.0,
        }
    )


def test_adjust_split_and_dividend_hand_computed():
    bars = frame(["2024-07-01", "2024-07-02", "2024-07-03"], [100.0, 104.0, 52.0])
    acts = normalize_actions(
        pd.DataFrame(
            [
                {"symbol": "X", "ex_date": "2024-07-02", "kind": "dividend", "value": 1.0},  # prev close 100
                {
                    "symbol": "X",
                    "ex_date": "2024-07-03",
                    "kind": "split",
                    "value": 2.0,
                },  # 2:1, prev close 104
            ]
        )
    )
    days = pd.Series([date(2024, 7, 1), date(2024, 7, 2), date(2024, 7, 3)])
    split = adjust_bars(bars, days, acts, "split")
    assert split["close"].tolist() == pytest.approx([50.0, 52.0, 52.0])
    assert split["volume"].tolist() == pytest.approx([200.0, 200.0, 100.0])
    total = adjust_bars(bars, days, acts, "split_dividend")
    assert total["close"].tolist() == pytest.approx([50.0 * 0.99, 52.0, 52.0])
    assert adjust_bars(bars, days, acts, "raw")["close"].tolist() == [100.0, 104.0, 52.0]


def test_validation_and_normalization():
    closes = {"X": pd.Series([100.0, 101.0], index=[date(2024, 7, 1), date(2024, 7, 2)])}
    ok = normalize_actions(
        pd.DataFrame([{"symbol": "x", "ex_date": "2024-07-02", "kind": "Dividend", "value": 1}])
    )
    assert ok.iloc[0]["symbol"] == "X" and ok.iloc[0]["kind"] == "dividend"
    assert validate_actions(ok, closes) == []
    bad = normalize_actions(
        pd.DataFrame(
            [
                {"symbol": "X", "ex_date": "2024-07-01", "kind": "split", "value": 2},  # no bar before
                {
                    "symbol": "X",
                    "ex_date": "2024-07-02",
                    "kind": "dividend",
                    "value": 150,
                },  # >= previous close
                {"symbol": "Y", "ex_date": "2024-07-02", "kind": "split", "value": 2},  # unknown symbol
                {"symbol": "X", "ex_date": "2024-07-09", "kind": "dividend", "value": 1},  # after last bar
            ]
        )
    )
    problems = validate_actions(bad, closes)
    assert len(problems) == 4
    with pytest.raises(CorporateActionError):
        normalize_actions(
            pd.DataFrame([{"symbol": "X", "ex_date": "2024-07-02", "kind": "merger", "value": 1}])
        )
    with pytest.raises(CorporateActionError):
        normalize_actions(
            pd.DataFrame([{"symbol": "X", "ex_date": "2024-07-02", "kind": "split", "value": 0}])
        )


def test_actions_hash_is_order_independent_and_none_when_empty():
    a = pd.DataFrame(
        [
            {"symbol": "X", "ex_date": "2024-07-02", "kind": "split", "value": 2},
            {"symbol": "X", "ex_date": "2024-07-03", "kind": "dividend", "value": 1},
        ]
    )
    assert actions_hash(a) == actions_hash(a.iloc[::-1])
    assert actions_hash(a.iloc[:0]) is None
