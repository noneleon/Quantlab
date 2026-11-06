"""Synthetic daily bars on real NYSE session dates (provenance: synthetic — never real performance)."""

import pytest

# 2024-07-01 .. 2024-07-09 NYSE sessions (07-04 holiday); 07-03 is an early close (13:00 New York).
SESSIONS = ["2024-07-01", "2024-07-02", "2024-07-03", "2024-07-05", "2024-07-08", "2024-07-09"]


def csv_text(rows, header="Date,Open,High,Low,Close,Adj Close,Volume"):
    return header + "\n" + "\n".join(rows) + "\n"


def daily_rows(dates=SESSIONS, base=100.0):
    rows = []
    for i, d in enumerate(dates):
        o = base + i
        rows.append(f"{d},{o:.2f},{o + 2:.2f},{o - 1:.2f},{o + 1:.2f},{o + 0.5:.2f},{1000 + i}")
    return rows


@pytest.fixture
def daily_csv(tmp_path):
    path = tmp_path / "spy.csv"
    path.write_text(csv_text(daily_rows()))
    return path
